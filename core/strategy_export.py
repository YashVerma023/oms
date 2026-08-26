"""Strategy tag files: one CSV per server, and the compiled review workbook.

Two outputs from one calculation:

  * **Per server** - the platform's own format, preamble and all, holding only
    the tags that server's algo runs on the chosen cycle step. This is the file
    that gets uploaded, so it follows `config/strategy_tag_template.csv`
    exactly; only Enabled, StrategyTag and User Account are filled in.

  * **Compiled** - one sheet per algo, one row per account, one column per tag.
    A review copy for the desk, not an upload format.

Which tags a server writes comes from the tag map in Admin Controls, and which
accounts are in each tag comes from the step that tag belongs to - the first
step of the cycle that lists it. That is what makes carry-forward fall out
rather than being special-cased: NF4DTE belongs to the 4DTE step, so on a 0DTE
day it still holds the accounts that were running at 4DTE, at the multiplier
their allocation gives.

Nothing here writes to the database.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import zipfile
from typing import Any

from sqlalchemy import bindparam, text

from core import rules, rules_io, strategy_tags
from core.strategy_tags import StrategyTagError
from database.db import db

logger = logging.getLogger(__name__)

# Re-exported so a caller catches one error type for the whole feature.
__all__ = ["StrategyTagError", "plan", "build", "zipped", "compiled"]

TEMPLATE = "config/strategy_tag_template.csv"

# The three columns the generator fills. Everything else is template.
COL_ENABLED = "Enabled"
COL_TAG = "StrategyTag"
COL_ACCOUNTS = "User Account"


def _template() -> tuple[list[list[str]], list[str], list[str]]:
    """(preamble rows, header, the default row) from the template file.

    Raises:
        StrategyTagError: the template is missing or the wrong shape.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / TEMPLATE
    try:
        rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    except OSError as exc:
        raise StrategyTagError(
            f"The strategy tag template at {TEMPLATE} could not be read: {exc}"
        ) from exc

    if len(rows) < 7:
        raise StrategyTagError(
            f"{TEMPLATE} needs 5 comment lines, a header and one default row."
        )

    header = rows[5]
    for column in (COL_ENABLED, COL_TAG, COL_ACCOUNTS):
        if column not in header:
            raise StrategyTagError(f"{TEMPLATE} has no '{column}' column.")
    return rows[:5], header, rows[6]


def _accounts(on_date: dt.date, servers: list[str] | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT `userId`, `alias`, `algo`, `server`, `allocation`, "
        "`SubCategory`, `Running Type`, `Running Days` "
        "FROM `all_users` WHERE `Date` = :d "
    )
    params: dict[str, Any] = {"d": on_date}
    binds = []

    if servers is not None:
        if not servers:
            return []
        sql += "AND `server` IN :servers "
        params["servers"] = servers
        binds.append(bindparam("servers", expanding=True))

    sql += "ORDER BY `server`, `userId`"
    statement = text(sql)
    if binds:
        statement = statement.bindparams(*binds)
    return [dict(r) for r in db.session.execute(statement, params).mappings().all()]


def _owning_step(cycle: str, tag: str) -> str | None:
    """The step a tag first appears at, which is the step it belongs to.

    NF4DTE is listed again on 1DTE and 0DTE so that it carries forward; the
    accounts in it are still the ones that were running at 4DTE.
    """
    for step in rules_io.cycle_steps(cycle):
        for row in rules_io.strategy_tag_map():
            if (row["cycle"].lower() == cycle.lower()
                    and row["dte"].upper() == step.upper()
                    and tag in row["tags"]):
                return step
    return None


def _in_scope(account: dict[str, Any], step: str) -> bool:
    """Whether an account is running at a given DTE step.

    Uses the same `dte_filters` block the allocation check uses, so 'running
    today' means one thing across the portal.
    """
    wanted = rules_io.dte_filter(step)
    if not wanted:
        return True                     # a step with no filter admits everyone

    for key, column in rules_io.DTE_COLUMNS.items():
        allowed = wanted.get(key)
        if not allowed:
            continue
        value = str(account.get(column) or "").strip().replace(" ", "").lower()
        if value not in {str(a).strip().replace(" ", "").lower() for a in allowed}:
            return False
    return True


def plan(
    on_date: dt.date,
    cycle: str,
    dte: str,
    servers: list[str] | None = None,
    rounding_basis: Any = None,
) -> dict[str, Any]:
    """Work out every server's tags and multipliers. Writes nothing.

    Returns:
        {"servers": {server: {"algo", "tags": {tag: {uid: mult}}, "accounts"}},
         "skipped": [...]} - `skipped` names servers that could not be built
        and why, rather than dropping them silently.
    """
    basis = rounding_basis if rounding_basis is not None else rules_io.rounding()["basis"]
    common = rules_io.common_series()

    by_server: dict[str, list[dict[str, Any]]] = {}
    for account in _accounts(on_date, servers):
        server = str(account.get("server") or "").strip()
        # DLR ACC and NOT RUNNING carry their state in `server`, so they drop
        # out here without a second rule.
        if not server or rules.inactive_state(account) is not None:
            continue
        by_server.setdefault(server.upper(), []).append(account)

    built: dict[str, Any] = {}
    skipped: list[str] = []

    for server, accounts in sorted(by_server.items()):
        try:
            algo = strategy_tags.algo_of(accounts, server)
        except StrategyTagError as exc:
            skipped.append(str(exc))
            continue

        tags = rules_io.tags_for(algo, cycle, dte)
        if not tags:
            skipped.append(
                f"{server}: no tags are mapped for algo {algo} on "
                f"{cycle} {dte}. Add a rule in Admin Controls."
            )
            continue

        holdings: dict[str, dict[str, int]] = {t: {} for t in tags}
        series = [t for t in tags if t not in common["tags"]]
        # The 0DTE series is whatever is left once the single-tag steps are
        # taken out - G1..G5 for algo 1, B for 7, E for 15.
        single = {t for t in tags if _owning_step(cycle, t) != "0DTE"}
        algo_series = [t for t in series if t not in single]

        for account in accounts:
            uid = str(account["userId"]).strip()
            allocation = account.get("allocation")

            for tag in sorted(single, key=tags.index):
                step = _owning_step(cycle, tag)
                if step is None or not _in_scope(account, step):
                    continue
                band = rules_io.strategy_bands().get(step)
                if band is None:
                    continue
                holdings[tag][uid] = strategy_tags.step_multiplier(allocation, band)

            if not _in_scope(account, "0DTE") or dte.upper() != "0DTE":
                continue

            for group in (algo_series,
                          [t for t in tags if t in common["tags"]]):
                if not group:
                    continue
                if group is not algo_series and not strategy_tags.in_common_series(
                    account.get("SubCategory"), common["subcategories"]
                ):
                    continue
                for tag, mult in strategy_tags.series_multipliers(
                    allocation, basis, group
                ).items():
                    holdings[tag][uid] = mult

        built[server] = {
            "algo": algo,
            "tags": {t: holdings[t] for t in tags if holdings[t]},
            "accounts": accounts,
        }

    logger.info(
        "Strategy tags for %s %s %s: %s server(s) built, %s skipped",
        on_date, cycle, dte, len(built), len(skipped),
    )
    return {"servers": built, "skipped": skipped, "bucket": strategy_tags.bucket_size(basis)}


def filename(server: str, on_date: dt.date) -> str:
    """'VS27 25 AUG 26 STRATEGYTAG.csv'."""
    stamp = on_date.strftime("%d %b %y").upper()
    return f"{server.strip().upper()} {stamp} STRATEGYTAG.csv"


def archive_name(on_date: dt.date) -> str:
    return f"STRATEGYTAG {on_date.strftime('%d %b %y').upper()}.zip"


def _csv_for(tags: dict[str, dict[str, int]]) -> str:
    """One server's file, in the platform's own layout."""
    preamble, header, default = _template()
    index = {name: i for i, name in enumerate(header)}

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for line in preamble:
        writer.writerow(line)
    writer.writerow(header)

    for tag, holdings in tags.items():
        row = list(default)
        row[index[COL_ENABLED]] = "True"
        row[index[COL_TAG]] = tag
        # `UID=n` for anything above 1; a bare UID means one lot, which is how
        # the platform writes it.
        row[index[COL_ACCOUNTS]] = ";".join(
            uid if mult == 1 else f"{uid}={mult}"
            for uid, mult in holdings.items()
        )
        writer.writerow(row)

    return buffer.getvalue()


def build(
    on_date: dt.date,
    cycle: str,
    dte: str,
    servers: list[str] | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Per-server CSVs.

    Returns:
        ([(filename, csv text)], notes about anything left out)
    """
    result = plan(on_date, cycle, dte, servers)
    files = [
        (filename(server, on_date), _csv_for(data["tags"]))
        for server, data in result["servers"].items()
        if data["tags"]
    ]
    if not files:
        raise StrategyTagError(
            f"No strategy tags to write for {cycle} {dte} on {on_date}. "
            + (result["skipped"][0] if result["skipped"] else
               "No running accounts were found for that day.")
        )
    return files, result["skipped"]


def zipped(files: list[tuple[str, str]], on_date: dt.date) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in files:
            archive.writestr(name, body)
    return out.getvalue()


# ---------------------------------------------------------------------------
# The compiled review workbook - one sheet per algo
# ---------------------------------------------------------------------------

def compiled_filename(on_date: dt.date) -> str:
    return f"STRATEGYTAG COMPILED {on_date.strftime('%d %b %y').upper()}.xlsx"


def compiled(
    on_date: dt.date,
    cycle: str,
    dte: str,
    servers: list[str] | None = None,
) -> tuple[bytes, int]:
    """One sheet per algo: SubCategory, User ID, allocation, then a tag column.

    A blank cell means the account does not run that tag - clearer at a glance
    than a zero, and it matches the CSV, where an absent account is one that
    does not trade.
    """
    import openpyxl

    result = plan(on_date, cycle, dte, servers)

    # Gather per algo, since one sheet covers every server on that algo.
    per_algo: dict[str, dict[str, Any]] = {}
    for server, data in result["servers"].items():
        entry = per_algo.setdefault(
            data["algo"], {"tags": [], "rows": {}, "servers": []}
        )
        entry["servers"].append(server)
        for tag in data["tags"]:
            if tag not in entry["tags"]:
                entry["tags"].append(tag)

        for account in data["accounts"]:
            uid = str(account["userId"]).strip()
            entry["rows"].setdefault(uid, {
                "subcategory": account.get("SubCategory") or "",
                "server": server,
                "allocation": account.get("allocation"),
                "tags": {},
            })
        for tag, holdings in data["tags"].items():
            for uid, mult in holdings.items():
                if uid in entry["rows"]:
                    entry["rows"][uid]["tags"][tag] = mult

    if not per_algo:
        raise StrategyTagError(
            f"Nothing to compile for {cycle} {dte} on {on_date}."
        )

    book = openpyxl.Workbook()
    book.remove(book.active)
    total = 0

    for algo in sorted(per_algo, key=lambda a: int(a) if a.isdigit() else 99):
        entry = per_algo[algo]
        sheet = book.create_sheet(f"Algo {algo}")
        sheet.append(["SubCategory", "User ID", "Server", "Allocation", *entry["tags"]])

        for uid in sorted(entry["rows"]):
            row = entry["rows"][uid]
            allocation = row["allocation"]
            sheet.append([
                row["subcategory"], uid, row["server"],
                float(allocation) if allocation is not None else None,
                *[row["tags"].get(tag) for tag in entry["tags"]],
            ])
            total += 1

        sheet.freeze_panes = "E2"

    out = io.BytesIO()
    book.save(out)
    logger.info("Compiled strategy tags for %s: %s row(s) across %s sheet(s)",
                on_date, total, len(per_algo))
    return out.getvalue(), total
