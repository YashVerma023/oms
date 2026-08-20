"""Admin role blueprint. Superadmin shares these views plus extras (TBD)."""

from __future__ import annotations

import logging

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from auth import roles_required
from core import all_users, rules
from core.importer import IMPORT_SPECS, import_sheet
from core.tables import TABLE_PAGES, as_of, fetch_rows, get_columns

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED = ("admin", "superadmin")


@bp.route("/")
@roles_required(*ALLOWED)
def dashboard():
    return render_template("admin/dashboard.html")


@bp.route("/table/<page_key>")
@roles_required(*ALLOWED)
def table(page_key: str):
    """Render a data-table page. `page_key` must be a known page."""
    if page_key not in TABLE_PAGES:
        abort(404)

    page = TABLE_PAGES[page_key]
    edit_url = None
    if page.get("edit_endpoint"):
        # __KEY__ is substituted per row in the browser.
        edit_url = url_for(page["edit_endpoint"], user_id="__KEY__")

    return render_template(
        "shared/table.html",
        page_key=page_key,
        title=page["title"],
        columns=get_columns(page_key),
        edit_url=edit_url,
        edit_key=page.get("edit_key"),
        reconcile_url=(
            url_for(page["reconcile_endpoint"]) if page.get("reconcile_endpoint") else None
        ),
        as_of=as_of(page_key),
    )


@bp.route("/api/table/<page_key>")
@roles_required(*ALLOWED)
def table_data(page_key: str):
    """Row data for a table page, consumed by static/js/table.js."""
    if page_key not in TABLE_PAGES:
        abort(404)

    return jsonify(
        columns=get_columns(page_key),
        rows=fetch_rows(page_key),
    )


@bp.route("/all-users/reconcile", methods=["POST"])
@roles_required(*ALLOWED)
def reconcile_all_users():
    """Re-apply the all_users business rules across the table.

    Called by the Refresh button on the All Users tab before it refetches.
    """
    try:
        return jsonify(all_users.reconcile_all())
    except Exception:
        logger.exception("Reconcile of all_users failed")
        return jsonify(error="Reconcile failed - see logs/omp.log."), 500


@bp.route("/all-users/<path:user_id>/edit", methods=["GET", "POST"])
@roles_required(*ALLOWED)
def edit_user(user_id: str):
    """Edit one all_users row. Every field is editable except userId."""
    record = all_users.get_user(user_id)
    if record is None:
        abort(404)

    if request.method == "POST":
        try:
            stored = all_users.update_user(user_id, request.form.to_dict())
        except LookupError:
            abort(404)
        except Exception:
            flash("Could not save the changes - see logs/omp.log.", "error")
            return redirect(url_for("admin.edit_user", user_id=user_id))

        message = f"Saved {user_id}."
        if stored.get("ml_pct") is None and rules.inactive_state(stored):
            message += (
                f" Marked {rules.inactive_state(stored)}: server, Running Type and "
                "Running Days were aligned, algo set to 0, ml_pct cleared."
            )
        else:
            message += f" ml_pct recalculated as {stored.get('ml_pct')}."
        flash(message, "success")
        return redirect(url_for("admin.table", page_key="all-users"))

    return render_template(
        "admin/edit_user.html",
        record=record,
        columns=all_users.editable_columns(),
        server_options=all_users.server_options(),
        running_type_options=rules.RUNNING_TYPE_OPTIONS,
        running_days_options=rules.RUNNING_DAYS_OPTIONS,
        inactive_states=rules.INACTIVE_STATES,
    )


@bp.route("/uploads", methods=["GET", "POST"])
@roles_required(*ALLOWED)
def uploads():
    """Upload a sheet into one of the loadable tables."""
    report = None

    if request.method == "POST":
        target = request.form.get("target", "")

        if target not in IMPORT_SPECS:
            flash("Choose which table to upload into.", "error")
            return redirect(url_for("admin.uploads"))

        spec = IMPORT_SPECS[target]
        uploads = [f for f in request.files.getlist("file") if f and f.filename]

        if not uploads:
            flash("Choose a file to upload.", "error")
            return redirect(url_for("admin.uploads"))

        if not spec.multiple and len(uploads) > 1:
            flash(f"{spec.title} accepts one file at a time.", "error")
            return redirect(url_for("admin.uploads"))

        wrong = [f.filename for f in uploads if not f.filename.lower().endswith(spec.accept)]
        if wrong:
            flash(
                f"{spec.title} expects {' or '.join(spec.accept)} - "
                f"got {', '.join(wrong)}.",
                "error",
            )
            return redirect(url_for("admin.uploads"))

        try:
            report = import_sheet(target, [(f.stream, f.filename) for f in uploads])
            flash(
                f"{spec.title}: {report.loaded} row(s) loaded"
                + (f" from {len(uploads)} files" if len(uploads) > 1 else "")
                + (f", {report.skipped} skipped" if report.skipped else "")
                + ".",
                "success",
            )
        except ValueError as exc:
            logger.warning("Upload rejected for '%s': %s", target, exc)
            flash(str(exc), "error")
        except Exception:
            logger.exception("Upload failed for target '%s'", target)
            flash("Upload failed. Nothing was changed - see logs/omp.log.", "error")

    return render_template("admin/uploads.html", specs=IMPORT_SPECS, report=report)
