"""Admin role blueprint. Superadmin shares these views plus extras (TBD)."""

from __future__ import annotations

import datetime as dt
import json
import logging

from flask import (
    Blueprint, Response, abort, flash, jsonify, redirect, render_template,
    request, session, url_for,
)

import access
from auth import roles_required
# Aliased: the view below is also called `dashboard`.
from core import dashboard as pivot
from core import all_users, crud, derive, personal, rules, rules_io, usersetting_export
from core import data_ops as data_ops_core
from database import schema
from core.importer import IMPORT_SPECS, import_sheet
from core.tables import TABLE_PAGES, as_of, default_date, fetch_rows, get_columns
from core.tables import date_column as table_date_column
from core.tables import nav_pages, subtabs as table_subtabs
from core.tables import delete_rows as delete_table_rows

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/admin")

ALLOWED = ("admin", "superadmin", "operator")


# setup_check pulls in pandas/numpy for the allocation rules. Imported lazily so
# a broken scientific stack disables only the Setup tab instead of stopping the
# whole portal at startup.
def _setup_check():
    from core import setup_check

    return setup_check


def _setup_unavailable(exc: Exception) -> str:
    logger.exception("The Setup feature is unavailable")
    return (
        "Setup needs pandas and numpy, which failed to load: "
        f"{exc}. Reinstall them with 'python -m pip install -r requirements.txt'."
    )



@bp.route("/")
@roles_required(*ALLOWED)
def dashboard():
    return render_template(
        "admin/dashboard.html",
        today=dt.date.today().isoformat(),
        # Operators cannot browse other dates here either.
        locked=access.is_operator(),
    )


@bp.route("/api/dashboard")
@roles_required(*ALLOWED)
def dashboard_data():
    """The algo/server/subcategory/user pivot for one date."""
    raw = (request.args.get("date") or "").strip()
    if access.is_operator() or not raw:
        on_date = dt.date.today()
    else:
        try:
            on_date = dt.date.fromisoformat(raw)
        except ValueError:
            return jsonify(error="That is not a valid date."), 400

    try:
        return jsonify(pivot.build(on_date, access.operator_servers()))
    except Exception:
        logger.exception("Building the dashboard pivot failed")
        return jsonify(error="Could not build the dashboard - see logs/omp.log."), 500


@bp.route("/table/<page_key>")
@roles_required(*ALLOWED)
def table(page_key: str):
    """Render a data-table page. `page_key` must be a known page."""
    if page_key not in TABLE_PAGES:
        abort(404)

    page = TABLE_PAGES[page_key]

    # A sub-tab whose table has not been built yet: show the strip and an
    # explanation, and touch none of the query paths.
    if page.get("pending"):
        return render_template(
            "shared/table.html",
            page_key=page_key,
            title=page["title"],
            columns=[],
            pending=True,
            subtabs=table_subtabs(page_key),
            nav_key=page.get("group") or page_key,
        )

    editable = page_key in crud.EDITABLE and access.can_edit(page_key)

    edit_url = None
    if page.get("edit_endpoint") and editable:
        # __KEY__ is substituted per row in the browser.
        edit_url = url_for(page["edit_endpoint"], row_id="__KEY__")

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
        # Present only for pages listed in crud.EDITABLE.
        new_url=(
            url_for("admin.new_row", page_key=page_key) if editable else None
        ),
        field_url=(
            url_for("admin.update_cell", page_key=page_key) if editable else None
        ),
        editable=crud.editable_meta(page_key) if editable else None,
        # Operators never delete, so the control is not rendered at all.
        delete_url=(
            url_for("admin.delete_rows", page_key=page_key)
            if access.can_delete() else None
        ),
        delete_key=list(page.get("delete_key", ())) if access.can_delete() else [],
        # Calendar control, present only on dated pages an operator may browse.
        # Resolved against the live table: a user-built page only gets the
        # calendar once its Date column actually exists.
        date_column=(
            table_date_column(page_key)
            if not access.locked_to_today(page_key) else None
        ),
        selected_date=default_date(page_key),
        # Per-server file download, on the pages that define one.
        export_url=(
            url_for(page["export_endpoint"]) if page.get("export_endpoint") else None
        ),
        export_title=page.get("export_title", "Download files"),
        subtabs=table_subtabs(page_key),
        nav_key=page.get("group") or page_key,
    )


@bp.route("/api/table/<page_key>")
@roles_required(*ALLOWED)
def table_data(page_key: str):
    """Row data for a table page, consumed by static/js/table.js."""
    if page_key not in TABLE_PAGES:
        abort(404)

    # A sub-tab with no table yet answers empty rather than querying nothing.
    if TABLE_PAGES[page_key].get("pending"):
        return jsonify(columns=[], rows=[], date=None)

    on_date = (request.args.get("date") or "").strip() or None
    # An operator cannot reach another date by editing the query string.
    if access.locked_to_today(page_key):
        on_date = dt.date.today().isoformat()

    return jsonify(
        columns=get_columns(page_key),
        rows=fetch_rows(page_key, on_date, access.operator_servers()),
        date=on_date or default_date(page_key),
    )


@bp.route("/table/<page_key>/delete", methods=["POST"])
@roles_required(*ALLOWED)
def delete_rows(page_key: str):
    """Delete the selected rows of a table page."""
    if page_key not in TABLE_PAGES:
        abort(404)
    if not access.can_delete():
        return jsonify(error="Your role cannot delete rows."), 403
    if TABLE_PAGES[page_key].get("pending"):
        return jsonify(error="That table has not been created yet."), 400

    payload = request.get_json(silent=True) or {}
    try:
        deleted = delete_table_rows(page_key, payload.get("keys") or [])
        return jsonify(deleted=deleted)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Delete failed on page '%s'", page_key)
        return jsonify(error="Delete failed - see logs/omp.log."), 500


@bp.route("/table/<page_key>/new", methods=["GET", "POST"])
@roles_required(*ALLOWED)
def new_row(page_key: str):
    """Add a row to an editable table."""
    if page_key not in crud.EDITABLE:
        abort(404)
    if not access.can_edit(page_key):
        abort(403)

    spec = crud.get_spec(page_key)

    if request.method == "POST":
        try:
            key = crud.create_row(page_key, request.form.to_dict())
            flash(f"Added {key}.", "success")
            return redirect(url_for("admin.table", page_key=page_key))
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            logger.exception("Create failed for page '%s'", page_key)
            flash("Could not add the row - see logs/omp.log.", "error")

    return render_template(
        "shared/record_form.html",
        page_key=page_key,
        title=TABLE_PAGES[page_key]["title"],
        pk=spec.pk,
        columns=crud.form_columns(page_key),
        submitted=request.form if request.method == "POST" else {},
    )


@bp.route("/table/<page_key>/field", methods=["POST"])
@roles_required(*ALLOWED)
def update_cell(page_key: str):
    """Inline edit of a single cell. Returns the whole updated row as JSON."""
    if page_key not in crud.EDITABLE:
        abort(404)
    if not access.can_edit(page_key):
        return jsonify(error="Your role cannot edit this table."), 403

    payload = request.get_json(silent=True) or {}
    try:
        row = crud.update_field(
            page_key, payload.get("key", ""), payload.get("column", ""), payload.get("value")
        )
        return jsonify(row=row)
    except LookupError as exc:
        return jsonify(error=str(exc)), 404
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Inline update failed on page '%s'", page_key)
        return jsonify(error="Update failed - see logs/omp.log."), 500


def _controls_page(**overrides):
    """Admin Controls, with the rule tables read from the rules file."""
    context = {
        "today": dt.date.today().isoformat(),
        "working_rows": all_users.working_count(),
        "subcategories": rules_io.subcategory_rows(),
        "brokers": rules_io.broker_rows(),
        "methods": rules_io.METHODS,
        "broker_methods": rules_io.BROKER_METHODS,
        "modes": rules_io.modes(),
        "mode_state": rules_io.mode_state(),
        "schedule": rules_io.schedule_rows(),
        "fallback_mode": rules_io.FALLBACK_MODE,
        "dte_rules": rules_io.dte_summary(),
        "error_subcategories": None,
        "error_brokers": None,
    }
    context.update(overrides)
    return render_template("admin/controls.html", **context)


def _rows_from_form(fields: tuple[str, ...]) -> list[dict]:
    """Zip the parallel form arrays back into one dict per table row."""
    columns = {name: request.form.getlist(name) for name in fields}
    count = max((len(v) for v in columns.values()), default=0)
    return [
        {name: (values[i] if i < len(values) else "") for name, values in columns.items()}
        for i in range(count)
    ]


@bp.route("/controls", methods=["GET"])
@roles_required("admin", "superadmin")
def controls():
    """Admin Controls: operations that act on a whole table."""
    return _controls_page()


@bp.route("/controls/subcategories", methods=["POST"])
@roles_required("admin", "superadmin")
def save_subcategories():
    """Replace the SubCategory rules from the Admin Controls table."""
    rows = _rows_from_form(("name", "pct", "method", "note"))
    try:
        rules_io.save_subcategories(rows)
        flash("Category rules saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        # Keep what was typed on screen rather than discarding the edit.
        return _controls_page(subcategories=rows, error_subcategories=str(exc))
    except Exception:
        logger.exception("Saving the category rules failed")
        flash("Could not save the category rules - see logs/omp.log.", "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/brokers", methods=["POST"])
@roles_required("admin", "superadmin")
def save_brokers():
    """Replace the broker overrides from the Admin Controls table."""
    rows = _rows_from_form(("name", "method", "value"))
    try:
        rules_io.save_brokers(rows)
        flash("Broker rules saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(brokers=rows, error_brokers=str(exc))
    except Exception:
        logger.exception("Saving the broker rules failed")
        flash("Could not save the broker rules - see logs/omp.log.", "error")
        return redirect(url_for("admin.controls"))


@bp.route("/personal/rebuild", methods=["POST"])
@roles_required(*ALLOWED)
def rebuild_personal():
    """Rebuild the Personal view for one date from All Users.

    Wired to the tab's refresh button, so pressing it rebuilds the day on
    screen rather than merely refetching.
    """
    payload = request.get_json(silent=True) or {}
    raw = (payload.get("date") or "").strip()
    try:
        on_date = dt.date.fromisoformat(raw) if raw else dt.date.today()
    except ValueError:
        return jsonify(error="That is not a valid date."), 400

    try:
        result = personal.rebuild(on_date)
    except personal.PersonalError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Rebuilding Personal failed")
        return jsonify(error="Could not rebuild Personal - see logs/omp.log."), 500

    message = (
        f"Personal rebuilt for {result['date']}: {result['written']} account(s) "
        f"from {', '.join(result['account_types'])}."
    )
    if result["unmapped"]:
        message += (
            " No source in All Users for: " + ", ".join(result["unmapped"]) + "."
        )

    # `updated`/`checked` are what the table page's refresh button reports.
    return jsonify(
        updated=result["written"], checked=result["written"], message=message
    )


@bp.route("/data-ops", methods=["GET"])
@roles_required("admin", "superadmin")
def data_ops():
    """Data Operation: create a table by hand or from a sheet."""
    return render_template(
        "admin/data_ops.html",
        types=data_ops_core.TYPES,
        reserved=sorted(schema.TABLES),
    )


@bp.route("/data-ops/tables", methods=["GET"])
@roles_required("admin", "superadmin")
def data_ops_tables():
    """Tables with no page of their own, for the sidebar list."""
    try:
        return jsonify(tables=data_ops_core.extra_tables())
    except Exception:
        logger.exception("Listing the additional tables failed")
        return jsonify(error="Could not list the tables - see logs/omp.log."), 500


@bp.route("/data-ops/table/<name>", methods=["GET"])
@roles_required("admin", "superadmin")
def data_ops_table(name: str):
    """Structure and row count of one additional table."""
    try:
        return jsonify(data_ops_core.describe(name))
    except data_ops_core.DataOpError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Describing table '%s' failed", name)
        return jsonify(error="Could not read the table - see logs/omp.log."), 500


@bp.route("/data-ops/table/<name>/rows", methods=["GET"])
@roles_required("admin", "superadmin")
def data_ops_rows(name: str):
    """A page of data from one additional table."""
    try:
        return jsonify(data_ops_core.rows(
            name,
            limit=request.args.get("limit", type=int) or data_ops_core.MAX_PAGE,
            offset=request.args.get("offset", type=int) or 0,
        ))
    except data_ops_core.DataOpError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Reading rows of '%s' failed", name)
        return jsonify(error="Could not read the table - see logs/omp.log."), 500


@bp.route("/data-ops/inspect", methods=["POST"])
@roles_required("admin", "superadmin")
def data_ops_inspect():
    """Read an uploaded sheet and suggest a schema for it.

    Returns the worksheet names instead when an Excel file has more than one
    and none has been chosen yet.
    """
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify(error="Choose a file first."), 400

    sheet = (request.form.get("sheet") or "").strip() or None
    try:
        names = data_ops_core.sheet_names(upload.stream, upload.filename)
        upload.stream.seek(0)
        if names and len(names) > 1 and sheet is None:
            return jsonify(sheets=names)

        return jsonify(data_ops_core.inspect(upload.stream, upload.filename, sheet))
    except data_ops_core.DataOpError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:                    # noqa: BLE001 - shown to the user
        logger.exception("Inspecting '%s' failed", upload.filename)
        return jsonify(error=f"Could not read the file: {exc}"), 400


@bp.route("/data-ops/create", methods=["POST"])
@roles_required("admin", "superadmin")
def data_ops_create():
    """Create the table, loading the uploaded rows when a file is supplied."""
    table = request.form.get("table", "")
    sheet = (request.form.get("sheet") or "").strip() or None

    try:
        columns = json.loads(request.form.get("columns") or "[]")
    except json.JSONDecodeError:
        return jsonify(error="The column list was not readable."), 400

    upload = request.files.get("file")
    stream = upload.stream if upload and upload.filename else None

    try:
        result = data_ops_core.create_table(
            table, columns, stream, upload.filename if stream else "", sheet
        )
        logger.info("Table '%s' created by %s", result["table"], session.get("email"))
        return jsonify(result)
    except data_ops_core.DataOpError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Creating a table failed")
        return jsonify(error="Could not create the table - see logs/omp.log."), 500


@bp.route("/usersetting/download", methods=["GET"])
@roles_required(*ALLOWED)
def download_usersetting():
    """One usersetting CSV per server, in the platform's upload format.

    Admins get every server; an operator gets only the servers assigned to
    them in Server Config, the same scoping the tabs use.
    """
    try:
        files, orphans = usersetting_export.build(access.operator_servers())
    except Exception:
        logger.exception("Usersetting export failed")
        return jsonify(error="Could not build the files - see logs/omp.log."), 500

    if not files:
        return jsonify(error="No usersetting rows to export."), 404

    if len(files) == 1:
        name, body = files[0]
        response = Response(body, mimetype="text/csv")
    else:
        name = usersetting_export.archive_name()
        response = Response(usersetting_export.zipped(files), mimetype="application/zip")

    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    # Read by the browser so the page can say what was left out.
    response.headers["X-OMP-Files"] = str(len(files))
    response.headers["X-OMP-Skipped"] = ",".join(orphans)
    return response


@bp.route("/controls/today-mode", methods=["POST"])
@roles_required("admin", "superadmin")
def save_today_mode():
    """Pin the DTE mode the Dashboard filters by, or hand it back to the schedule."""
    mode = request.form.get("mode", "")
    try:
        rules_io.save_today_mode(mode, by=session.get("email", ""))
        state = rules_io.mode_state()
        flash(
            f"Dashboard pinned to {mode} for today."
            if mode
            else f"Dashboard follows the schedule: {state['mode']} today.",
            "success",
        )
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception:
        logger.exception("Saving today's DTE mode failed")
        flash("Could not save the DTE mode - see logs/omp.log.", "error")
    return redirect(url_for("admin.controls"))


@bp.route("/setup", methods=["GET"])
@roles_required(*ALLOWED)
def setup():
    """Allocation Check: compute expected allocations and write them back."""
    unavailable = None
    try:
        _setup_check()
    except Exception as exc:               # noqa: BLE001 - shown to the user
        unavailable = _setup_unavailable(exc)

    return render_template(
        "admin/setup.html",
        today=dt.date.today().isoformat(),
        modes=rules_io.modes(),
        rounding=rules_io.rounding(),
        unavailable=unavailable,
    )


@bp.route("/setup/run", methods=["POST"])
@roles_required(*ALLOWED)
def setup_run():
    """Run the check and return the proposed changes. Writes nothing."""
    payload = request.get_json(silent=True) or {}
    try:
        on_date = dt.date.fromisoformat((payload.get("date") or "").strip())
    except ValueError:
        return jsonify(error="Choose the date to check."), 400

    previous = (payload.get("previous") or "").strip()
    previous_date = None
    if previous:
        try:
            previous_date = dt.date.fromisoformat(previous)
        except ValueError:
            return jsonify(error="The previous-day date is not a valid date."), 400

    try:
        return jsonify(_setup_check().run_check(
            on_date,
            payload.get("mode", "1DTE"),
            previous_date,
            rounding_basis=payload.get("rounding"),
            servers=access.operator_servers(),
        ))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Allocation check failed")
        return jsonify(error="Check failed - see logs/omp.log."), 500


@bp.route("/setup/apply", methods=["POST"])
@roles_required(*ALLOWED)
def setup_apply():
    """Write the selected expected allocations to all_users and usersetting."""
    payload = request.get_json(silent=True) or {}
    try:
        on_date = dt.date.fromisoformat((payload.get("date") or "").strip())
    except ValueError:
        return jsonify(error="Missing the date to write against."), 400

    try:
        return jsonify(_setup_check().apply_changes(
            on_date, payload.get("updates") or [], servers=access.operator_servers()
        ))
    except Exception:
        logger.exception("Applying the allocation check failed")
        return jsonify(error="Apply failed - see logs/omp.log."), 500


@bp.route("/controls/save-all-users", methods=["POST"])
@roles_required("admin", "superadmin")
def save_all_users():
    """Store the All Users working set as the snapshot for a chosen date."""
    raw = (request.form.get("date") or "").strip()
    try:
        on_date = dt.date.fromisoformat(raw)
    except ValueError:
        flash("Choose a date to save against.", "error")
        return redirect(url_for("admin.controls"))

    try:
        result = all_users.save_snapshot(on_date)
        message = f"Saved {result['saved']} row(s) against {result['date']}."
        if result["replaced"]:
            message += f" Replaced {result['replaced']} row(s) already held for that date."
        flash(message, "success")
    except ValueError as exc:
        flash(str(exc), "error")
    except Exception:
        logger.exception("Saving the all_users snapshot failed")
        flash("Save failed - see logs/omp.log.", "error")

    return redirect(url_for("admin.controls"))


@bp.route("/usersetting/reconcile", methods=["POST"])
@roles_required(*ALLOWED)
def reconcile_usersetting():
    """Re-derive usersetting.algo from all_users.

    Called by the Refresh button on the Usersetting tab before it refetches.
    """
    try:
        return jsonify(updated=derive.sync_usersetting_algo())
    except Exception:
        logger.exception("Deriving usersetting.algo failed")
        return jsonify(error="Refresh failed - see logs/omp.log."), 500


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


@bp.route("/all-users/<row_id>/edit", methods=["GET", "POST"])
@roles_required(*ALLOWED)
def edit_user(row_id: str):
    """Edit one all_users row, identified by its id."""
    if not access.can_edit("all-users"):
        abort(403)

    record = all_users.get_user(row_id)
    if record is None:
        abort(404)

    if request.method == "POST":
        try:
            stored = all_users.update_user(row_id, request.form.to_dict())
        except LookupError:
            abort(404)
        except Exception:
            flash("Could not save the changes - see logs/omp.log.", "error")
            return redirect(url_for("admin.edit_user", row_id=row_id))

        message = f"Saved {record['userId']}."
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
        if not access.can_upload(target):
            flash("Your role cannot upload into that table.", "error")
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

        extra: dict = {}
        if spec.date_column:
            raw = (request.form.get("date") or "").strip()
            try:
                extra[spec.date_column] = dt.date.fromisoformat(raw)
            except ValueError:
                flash(f"{spec.title}: choose the date this data belongs to.", "error")
                return redirect(url_for("admin.uploads"))

        try:
            report = import_sheet(
                target, [(f.stream, f.filename) for f in uploads], extra
            )
            flash(
                f"{spec.title}: {report.loaded} row(s) loaded"
                + (f" for {extra[spec.date_column]}" if spec.date_column else "")
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

    return render_template(
        "admin/uploads.html",
        specs={k: v for k, v in IMPORT_SPECS.items() if access.can_upload(k)},
        report=report,
        today=dt.date.today().isoformat(),
    )
