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
from core import compiled_export
from core import strategy_export
from core import data_ops as data_ops_core
from core import maxloss
from database.db import db
from database import schema
from core.importer import IMPORT_SPECS, import_sheet
from core.tables import TABLE_PAGES, as_of, default_date, fetch_rows, get_columns
from core.tables import latest_date  # noqa: E501
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
        return jsonify(error="Could not build the dashboard." + access.failure_detail()), 500


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

    rows = fetch_rows(page_key, on_date, access.operator_servers())
    return jsonify(
        columns=get_columns(page_key),
        rows=rows,
        date=on_date or default_date(page_key),
        # Only worth the query when the day is empty: it tells the user whether
        # an upload landed on a different date or never landed at all.
        latest=None if rows else latest_date(page_key),
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
        return jsonify(error="Delete failed." + access.failure_detail()), 500


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
            flash("Could not add the row." + access.failure_detail(), "error")

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
        return jsonify(error="Update failed." + access.failure_detail()), 500


def _controls_page(**overrides):
    """Admin Controls, with the rule tables read from the rules file."""
    context = {
        "today": dt.date.today().isoformat(),
        "subcategories": rules_io.subcategory_rows(),
        "methods": rules_io.METHODS,
        "modes": rules_io.modes(),
        "mode_state": rules_io.mode_state(),
        "schedule": rules_io.schedule_rows(),
        "fallback_mode": rules_io.FALLBACK_MODE,
        "dte_rules": rules_io.dte_summary(),
        "maxloss_rows": _maxloss_rows(),
        "submaxloss_rows": rules_io.subcategory_maxloss_rows(),
        "algomaxloss_rows": rules_io.algo_maxloss_rows(),
        "strategy_map": rules_io.strategy_map_rows(),
        "bands": rules_io.band_rows(),
        "common": rules_io.common_series(),
        "subcategory_choices": _subcategory_choices(),
        "cycles": list(rules_io.cycles()),
        "error_subcategories": None,
        "error_maxloss": None,
        "error_submaxloss": None,
        "error_algomaxloss": None,
        "error_strategymap": None,
        "error_bands": None,
        "error_common": None,
    }
    context.update(overrides)
    return render_template("admin/controls.html", **context)


def _subcategory_choices() -> list[dict]:
    """Every SubCategory to offer, ticked where it joins the common series.

    A stored SubCategory that no longer appears in All Users is still listed
    and still ticked - dropping it silently would quietly change who trades.
    """
    chosen = set(rules_io.common_series()["subcategories"])
    try:
        known = all_users.subcategories()
    except Exception:
        logger.exception("Could not read the SubCategory list")
        known = []

    return [
        {"name": name, "chosen": name in chosen, "missing": name not in known}
        for name in sorted(set(known) | chosen)
    ]


def _maxloss_rows() -> list[dict]:
    """One row per algo, one column per DTE mode, for the editor."""
    rules = rules_io.maxloss_rules()
    return [
        {"algo": algo, **{mode: rules.get(mode, {}).get(algo) for mode in rules_io.modes()}}
        for algo in rules_io.maxloss_algos()
    ]


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
        flash("Could not save the category rules." + access.failure_detail(), "error")
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
        return jsonify(error="Could not rebuild Personal." + access.failure_detail()), 500

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
        return jsonify(error="Could not list the tables." + access.failure_detail()), 500


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
        return jsonify(error="Could not read the table." + access.failure_detail()), 500


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
        return jsonify(error="Could not read the table." + access.failure_detail()), 500


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
        return jsonify(error="Could not create the table." + access.failure_detail()), 500


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
        return jsonify(error="Could not build the files." + access.failure_detail()), 500

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


XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _workbook_response(name: str, body: bytes, rows: int) -> Response:
    response = Response(body, mimetype=XLSX_MIME)
    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    # Read by the page so it can report what came back.
    response.headers["X-OMP-Files"] = "1"
    response.headers["X-OMP-Rows"] = str(rows)
    response.headers["X-OMP-Skipped"] = ""
    return response


@bp.route("/setup/download/usersetting-compiled")
@roles_required("admin", "superadmin")
def download_usersetting_compiled():
    """Every server's usersetting rows on one sheet, for review.

    Not the platform's upload format - `download_usersetting` stays the way
    back into the platform. Admin only: the sheet carries the API keys and
    passwords of every server at once.
    """
    try:
        name, body, rows = compiled_export.usersetting_workbook()
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        logger.exception("Compiled usersetting export failed")
        return jsonify(error="Could not build the workbook." + access.failure_detail()), 500

    return _workbook_response(name, body, rows)


@bp.route("/setup/download/all-users-compiled")
@roles_required("admin", "superadmin")
def download_all_users_compiled():
    """One day's all_users rows as a 'Main' sheet, ready to upload again."""
    raw = (request.args.get("date") or "").strip()
    try:
        on_date = dt.date.fromisoformat(raw) if raw else dt.date.today()
    except ValueError:
        return jsonify(error="That is not a valid date."), 400

    try:
        name, body, rows = compiled_export.all_users_workbook(on_date)
    except ValueError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        logger.exception("Compiled All Users export failed")
        return jsonify(error="Could not build the workbook." + access.failure_detail()), 500

    return _workbook_response(name, body, rows)


def _cycle_step(default_cycle: str | None = None) -> tuple[str, str]:
    """The cycle and step a download is for, from the query string."""
    cycle = (request.args.get("cycle") or "").strip() or (
        default_cycle or rules_io.scheduled_cycle(dt.date.today()) or rules_io.NIFTY
    )
    step = (request.args.get("dte") or "").strip() or rules_io.today_mode()
    return cycle, step


@bp.route("/setup/download/strategy-tags")
@roles_required(*ALLOWED)
def download_strategy_tags():
    """One strategy tag CSV per server, in the platform's upload format.

    Operators get this too - they cannot set the rules, but they do have to
    hand the files to their own servers - so it is scoped the same way every
    other operator view is.
    """
    raw = (request.args.get("date") or "").strip()
    try:
        on_date = dt.date.fromisoformat(raw) if raw else dt.date.today()
    except ValueError:
        return jsonify(error="That is not a valid date."), 400

    cycle, step = _cycle_step()

    try:
        files, skipped = strategy_export.build(
            on_date, cycle, step, access.operator_servers()
        )
    except strategy_export.StrategyTagError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        logger.exception("Strategy tag export failed")
        return jsonify(error="Could not build the files." + access.failure_detail()), 500

    if len(files) == 1:
        name, body = files[0]
        response = Response(body, mimetype="text/csv")
    else:
        name = strategy_export.archive_name(on_date)
        response = Response(
            strategy_export.zipped(files, on_date), mimetype="application/zip"
        )

    response.headers["Content-Disposition"] = f'attachment; filename="{name}"'
    response.headers["X-OMP-Files"] = str(len(files))
    response.headers["X-OMP-Skipped"] = ",".join(skipped)
    return response


@bp.route("/setup/download/strategy-compiled")
@roles_required("admin", "superadmin")
def download_strategy_compiled():
    """One sheet per algo, one row per account, one column per tag.

    Admin only: it spans every server at once, which is a view an operator is
    deliberately not given.
    """
    raw = (request.args.get("date") or "").strip()
    try:
        on_date = dt.date.fromisoformat(raw) if raw else dt.date.today()
    except ValueError:
        return jsonify(error="That is not a valid date."), 400

    cycle, step = _cycle_step()

    try:
        body, rows = strategy_export.compiled(on_date, cycle, step)
    except strategy_export.StrategyTagError as exc:
        return jsonify(error=str(exc)), 404
    except Exception:
        logger.exception("Compiled strategy tag export failed")
        return jsonify(error="Could not build the workbook." + access.failure_detail()), 500

    return _workbook_response(
        strategy_export.compiled_filename(on_date), body, rows
    )


@bp.route("/controls/strategy-map", methods=["POST"])
@roles_required("admin", "superadmin")
def save_strategy_map():
    """Replace the algo/cycle/step -> tags table."""
    rows = _rows_from_form(("algo", "cycle", "dte", "tags"))
    try:
        rules_io.save_strategy_map(rows)
        flash("Tag map saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(strategy_map=rows, error_strategymap=str(exc))
    except Exception:
        logger.exception("Saving the strategy tag map failed")
        flash("Could not save the tag map." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/strategy-bands", methods=["POST"])
@roles_required("admin", "superadmin")
def save_strategy_bands():
    """Replace the 4DTE / 1DTE multiplier bands."""
    rows = _rows_from_form(("step", "first_step", "width", "edge"))
    try:
        rules_io.save_strategy_bands(rows)
        flash("Multiplier bands saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(bands=rows, error_bands=str(exc))
    except Exception:
        logger.exception("Saving the strategy bands failed")
        flash("Could not save the bands." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/common-series", methods=["POST"])
@roles_required("admin", "superadmin")
def save_common_series():
    """Replace the A-series tag names and the SubCategories that join them."""
    try:
        rules_io.save_common_series(
            request.form.get("tags", ""),
            request.form.getlist("subcategories"),
        )
        flash("Common series saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(error_common=str(exc))
    except Exception:
        logger.exception("Saving the common series failed")
        flash("Could not save the common series." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/maxloss", methods=["POST"])
@roles_required("admin", "superadmin")
def save_maxloss_rules():
    """Replace the max-loss multipliers from the Admin Controls table."""
    rows = _rows_from_form(("algo", *rules_io.modes()))
    try:
        rules_io.save_maxloss_rules(rows)
        flash("Max loss rules saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        # Keep what was typed on screen rather than discarding the edit.
        return _controls_page(maxloss_rows=rows, error_maxloss=str(exc))
    except Exception:
        logger.exception("Saving the max loss rules failed")
        flash("Could not save the max loss rules." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/algo-maxloss", methods=["POST"])
@roles_required("admin", "superadmin")
def save_algo_maxloss():
    """Replace the per-algo max loss overrides."""
    fields = ["algo"]
    for mode in rules_io.modes():
        fields += [f"{mode}_mstech", f"{mode}_stoxxo"]

    rows = _rows_from_form(tuple(fields))
    try:
        rules_io.save_algo_maxloss(rows)
        flash("Algo max loss rules saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(algomaxloss_rows=rows, error_algomaxloss=str(exc))
    except Exception:
        logger.exception("Saving the algo max loss rules failed")
        flash("Could not save them." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


@bp.route("/controls/subcategory-maxloss", methods=["POST"])
@roles_required("admin", "superadmin")
def save_subcategory_maxloss():
    """Replace the SubCategory max loss overrides."""
    rows = _rows_from_form(("name", "mstech", "stoxxo"))
    try:
        rules_io.save_subcategory_maxloss(rows)
        flash("SubCategory max loss rules saved.", "success")
        return redirect(url_for("admin.controls"))
    except ValueError as exc:
        return _controls_page(submaxloss_rows=rows, error_submaxloss=str(exc))
    except Exception:
        logger.exception("Saving the SubCategory max loss rules failed")
        flash("Could not save them." + access.failure_detail(), "error")
        return redirect(url_for("admin.controls"))


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
        flash("Could not save the DTE mode." + access.failure_detail(), "error")
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
        cycles=rules_io.cycles(),
        cycle_state=rules_io.cycle_state(),
        rounding=rules_io.rounding(),
        unavailable=unavailable,
        # The compiled workbooks are admin only.
        compiled=access.is_admin(),
        # So the upload box can say what is already loaded.
        maxloss_state=_maxloss_state(),
    )


def _maxloss_state() -> dict:
    """The stored Max Loss sheet, or an empty state if the table is missing."""
    try:
        return maxloss.sheet_state()
    except Exception:
        logger.exception("Could not read the Max Loss sheet state")
        return {"date": None, "rows": 0}


@bp.route("/setup/maxloss", methods=["POST"])
@roles_required(*ALLOWED)
def upload_maxloss():
    """Load the Max Loss Calculation sheet from the Setup tab.

    The workbook carries its own Date column, so the day comes from the file
    rather than being chosen here, and loading it replaces only that day.
    """
    spec = IMPORT_SPECS["maxloss"]
    upload = request.files.get("file")

    if not upload or not upload.filename:
        flash("Choose the Max Loss workbook to upload.", "error")
    elif not upload.filename.lower().endswith(spec.accept):
        flash(f"Max Loss expects {' or '.join(spec.accept)}.", "error")
    else:
        try:
            report = import_sheet("maxloss", [(upload.stream, upload.filename)])
            flash(
                f"Max Loss: {report.loaded} row(s) loaded"
                + (f", {report.skipped} skipped" if report.skipped else "")
                + ". Shown under All Users -> Positional.",
                "success",
            )
        except ValueError as exc:
            logger.warning("Max Loss upload rejected: %s", exc)
            flash(str(exc), "error")
        except Exception:
            logger.exception("Max Loss upload failed")
            flash("Upload failed. Nothing was changed." + access.failure_detail(), "error")

    return redirect(url_for("admin.setup"))


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

    mode = payload.get("mode", "1DTE")
    cycle = (payload.get("cycle") or "").strip() or None
    servers = access.operator_servers()

    try:
        result = _setup_check().run_check(
            on_date,
            mode,
            previous_date,
            rounding_basis=payload.get("rounding"),
            servers=servers,
            cycle=cycle,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        logger.exception("Allocation check failed")
        return jsonify(error="Check failed." + access.failure_detail()), 500

    # Max loss is worked out from the allocations this run is about to write,
    # not the stored ones, so the two halves of a run cannot disagree.
    proposed = {
        row["userid"]: row["expected"]
        for row in result.get("rows", [])
        if row.get("apply") and row.get("expected") is not None
    }
    try:
        result["maxloss"] = maxloss.plan(
            on_date, mode,
            has_previous=rules_io.needs_previous(cycle, mode),
            servers=servers,
            proposed=proposed,
        )
    except maxloss.MaxLossError as exc:
        result["maxloss"] = {"error": str(exc), "rows": []}
    except Exception:
        logger.exception("Max loss plan failed")
        result["maxloss"] = {
            "error": "Max loss could not be worked out." + access.failure_detail(),
            "rows": [],
        }

    return jsonify(result)


@bp.route("/setup/apply", methods=["POST"])
@roles_required(*ALLOWED)
def setup_apply():
    """Write the selected allocations and max losses.

    All four writes - all_users.allocation, usersetting.Remarks,
    all_users.max_loss, usersetting.`Max Loss` - go in one transaction, so a
    failure half way leaves the day untouched rather than half set up.
    """
    payload = request.get_json(silent=True) or {}
    try:
        on_date = dt.date.fromisoformat((payload.get("date") or "").strip())
    except ValueError:
        return jsonify(error="Missing the date to write against."), 400

    servers = access.operator_servers()
    allocations = payload.get("updates") or []
    losses = payload.get("maxloss") or []

    try:
        result = _setup_check().apply_changes(
            on_date, allocations, servers=servers, commit=False
        )
        result.update(maxloss.apply(on_date, losses, servers=servers, commit=False))
        db.session.commit()
        return jsonify(result)
    except Exception:
        db.session.rollback()
        logger.exception("Applying the setup run failed")
        return jsonify(error="Apply failed." + access.failure_detail()), 500


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
        return jsonify(error="Refresh failed." + access.failure_detail()), 500


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
        return jsonify(error="Reconcile failed." + access.failure_detail()), 500


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
            flash("Could not save the changes." + access.failure_detail(), "error")
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

        # The companion table has its own permission. An operator may upload
        # All Users but not Jainam, so the second sheet must not become a way
        # round that.
        companion_allowed = (
            spec.companion is None or access.can_upload(spec.companion)
        )

        try:
            report = import_sheet(
                target, [(f.stream, f.filename) for f in uploads], extra,
                load_companion=companion_allowed,
            )
            flash(
                f"{spec.title}: {report.loaded} row(s) loaded"
                + (f" for {extra[spec.date_column]}" if spec.date_column else "")
                + (f" from {len(uploads)} files" if len(uploads) > 1 else "")
                + (f", {report.skipped} skipped" if report.skipped else "")
                + ".",
                "success",
            )
            if report.companion:
                extra_load = report.companion
                flash(
                    f"{extra_load['title']} was NOT updated - {extra_load['error']}"
                    if extra_load["error"]
                    else f"{extra_load['title']}: {extra_load['loaded']} row(s) "
                         f"loaded from the same workbook.",
                    "error" if extra_load["error"] else "success",
                )
        except ValueError as exc:
            logger.warning("Upload rejected for '%s': %s", target, exc)
            flash(str(exc), "error")
        except Exception:
            logger.exception("Upload failed for target '%s'", target)
            flash("Upload failed. Nothing was changed." + access.failure_detail(), "error")

    return render_template(
        "admin/uploads.html",
        specs={k: v for k, v in IMPORT_SPECS.items() if access.can_upload(k)},
        report=report,
        today=dt.date.today().isoformat(),
    )
