"""Table definitions for OMP.

Column names match the source sheet headers exactly (per project decision), so
identifiers containing spaces, parentheses or a leading digit must always be
backticked in queries - e.g. `Running Type`, `FIX (CR)`, `0SL`.

Numeric columns are DECIMAL and nullable: the source sheets contain '#N/A',
'NA', 'As per VU' and similar placeholders, which the import layer converts to
NULL and logs rather than storing as text.

Run standalone to provision tables:
    python -m database.schema
"""

from __future__ import annotations

import logging
import os
import re

from database.db import _connect, get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL - ordered dict; tables are created in this order.
# ---------------------------------------------------------------------------

TABLES: dict[str, str] = {}

# Source: 'All User.xlsx' -> Main sheet.
#
# Dated history: one row per user per date.
# Uploaded rows are stamped with today's date; Admin Controls > Save All Users
# copies the current day's rows onto another date. The surrogate `id` is the
# primary key so (userId, Date) can carry a plain UNIQUE constraint.
TABLES["all_users"] = """
CREATE TABLE `all_users` (
    `id`             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `userId`         VARCHAR(32)     NOT NULL,
    `alias`          VARCHAR(120)    NULL,
    `Broker`         VARCHAR(64)     NULL,
    `max_loss`       DECIMAL(18,2)   NULL,
    `allocation`     DECIMAL(18,2)   NULL,
    `server`         VARCHAR(32)     NULL,
    `algo`           VARCHAR(8)      NULL,
    -- Wide on purpose: ml_pct is max_loss/allocation, and dealer rows carry an
    -- allocation placeholder of 1.0 which makes the ratio enormous.
    `ml_pct`         DECIMAL(18,4)   NULL,
    `Running Type`   VARCHAR(20)     NULL,
    `Running Days`   VARCHAR(20)     NULL,
    `FIX (CR)`       DECIMAL(12,4)   NULL,
    `0SL`            DECIMAL(18,2)   NULL,
    `Remarks`        VARCHAR(255)    NULL,
    `Operator Name`  VARCHAR(64)     NULL,
    `Category`       VARCHAR(16)     NULL,
    `SubCategory`    VARCHAR(16)     NULL,
    `Acc Type`       VARCHAR(32)     NULL,
    -- Last data column. Not present in the Main sheet - set to the upload day.
    `Date`           DATE            NOT NULL,
    `created_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                     ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_all_users_user_date` (`userId`, `Date`),
    KEY `idx_all_users_date` (`Date`),
    KEY `idx_all_users_server` (`server`),
    KEY `idx_all_users_operator` (`Operator Name`),
    KEY `idx_all_users_category` (`Category`, `SubCategory`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Source: 'All User.xlsx' -> Jainam sheet. One row per user per date.
TABLES["jainam"] = """
CREATE TABLE `jainam` (
    `Date`         DATE            NOT NULL,
    `UserID`       VARCHAR(32)     NOT NULL,
    `User Alias`   VARCHAR(120)    NULL,
    `Algo`         VARCHAR(8)      NULL,
    `VT`           DECIMAL(18,4)   NULL,
    `GB`           DECIMAL(18,4)   NULL,
    `PS`           DECIMAL(18,4)   NULL,
    `RD`           DECIMAL(18,4)   NULL,
    `RM`           DECIMAL(18,4)   NULL,
    `ALLOCATION`   DECIMAL(18,4)   NULL,
    `MAX LOSS`     DECIMAL(18,4)   NULL,
    `Type`         VARCHAR(32)     NULL,
    `Expiry`       VARCHAR(16)     NULL,
    `created_at`   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`Date`, `UserID`),
    KEY `idx_jainam_user` (`UserID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Source: the 'Max Loss Calculation' sheet of the daily Calculation workbook.
# Held as uploaded, one row per account per date, so what was applied on any
# given day can be read back. `Stoxxo Max Loss` and `MStech Max Loss` are kept
# apart deliberately: they are equal today but feed different systems.
TABLES["maxloss"] = """
CREATE TABLE `maxloss` (
    `Date`              DATE            NOT NULL,
    `User ID`           VARCHAR(32)     NOT NULL,
    `Broker`            VARCHAR(64)     NULL,
    `Broker Group`      VARCHAR(64)     NULL,
    `Algo`              VARCHAR(8)      NULL,
    `Server`            VARCHAR(32)     NULL,
    `Allocation`        DECIMAL(18,4)   NULL,
    `Realised P&L`      DECIMAL(18,4)   NULL,
    `Unrealised P&L`    DECIMAL(18,4)   NULL,
    `Stoxxo Max Loss`   DECIMAL(18,4)   NULL,
    `MStech Max Loss`   DECIMAL(18,4)   NULL,
    `created_at`        TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`Date`, `User ID`),
    KEY `idx_maxloss_user` (`User ID`),
    KEY `idx_maxloss_server` (`Server`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Source: USERSETTING.csv. Header is row 7; rows 1-6 are comments and are
# skipped on upload. Header ' LIMIT Type' is stored stripped as `LIMIT Type`.
TABLES["usersetting"] = """
CREATE TABLE `usersetting` (
    `Enabled`                     BOOLEAN         NULL,
    `User Alias`                  VARCHAR(120)    NULL,
    `User ID`                     VARCHAR(32)     NOT NULL,
    `Broker`                      VARCHAR(64)     NULL,
    `API Key`                     VARCHAR(128)    NULL,
    `API Secret`                  VARCHAR(255)    NULL,
    `Historical API`              BOOLEAN         NULL,
    `SquareOff Time`              TIME            NULL,
    `Auto Login`                  BOOLEAN         NULL,
    `Password`                    VARCHAR(128)    NULL,
    `Pin`                         VARCHAR(128)    NULL,
    `Max Profit`                  DECIMAL(18,2)   NULL,
    `Max Loss`                    DECIMAL(18,2)   NULL,
    `Qty on MaxLoss PerTrade`     BOOLEAN         NULL,
    `Max Loss Per Trade`          DECIMAL(18,2)   NULL,
    `Max Open Trades`             INT             NULL,
    `Telegram ID(s)`              VARCHAR(255)    NULL,
    `Email`                       VARCHAR(255)    NULL,
    `Qty Multiplier`              DECIMAL(10,4)   NULL,
    `Trading Authorization Req`   BOOLEAN         NULL,
    `SqOff NRML Orders`           TINYINT         NULL,
    `Qty By Exposure`             DECIMAL(18,4)   NULL,
    `API User Details`            VARCHAR(255)    NULL,
    `SqOff CNC Orders`            BOOLEAN         NULL,
    `ProfitLocking`               VARCHAR(64)     NULL,
    `MaxLossWaitSec`              INT             NULL,
    `TwoFA`                       VARCHAR(255)    NULL,
    `LIMIT Only`                  BOOLEAN         NULL,
    `LIMIT Type`                  VARCHAR(32)     NULL,
    `LIMIT Spread`                VARCHAR(16)     NULL,
    `MaxModifications`            INT             NULL,
    `LimitModificationGapTime`    INT             NULL,
    `API Version`                 VARCHAR(32)     NULL,
    `Proxy`                       VARCHAR(255)    NULL,
    `OrderPerSecond`              INT             NULL,
    `MaxChaseLimit`               VARCHAR(16)     NULL,
    `Remarks`                     VARCHAR(255)    NULL,
    `server`                      VARCHAR(32)     NOT NULL DEFAULT '',
    `algo`                        VARCHAR(8)      NULL,
    `created_at`                  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                                  ON UPDATE CURRENT_TIMESTAMP,
    -- One row per account per server. A feed or dealer login is present in
    -- several servers' files, each with that server's own settings, so the
    -- account on its own is not unique.
    PRIMARY KEY (`User ID`, `server`),
    KEY `idx_usersetting_broker` (`Broker`),
    KEY `idx_usersetting_server` (`server`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Source: running-users.csv. Append-only history - one row per user per import.
TABLES["running_users"] = """
CREATE TABLE `running_users` (
    `id`                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `userId`                VARCHAR(32)     NOT NULL,
    `alias`                 VARCHAR(120)    NULL,
    `positions`             INT             NULL,
    `order_counts`          INT             NULL,
    `pe_ratio`              DECIMAL(10,4)   NULL,
    `ce_ratio`              DECIMAL(10,4)   NULL,
    `max_loss`              DECIMAL(18,2)   NULL,
    `sl_p`                  DECIMAL(10,4)   NULL,
    `algo`                  VARCHAR(8)      NULL,
    `server`                VARCHAR(16)     NULL,
    `sqoff_initiated`       BOOLEAN         NULL,
    `sqoff_initiated_time`  DATETIME(3)     NULL,
    `mtm_p`                 DECIMAL(10,4)   NULL,
    `mtm`                   DECIMAL(18,2)   NULL,
    `margin`                DECIMAL(18,2)   NULL,
    `allocation`            DECIMAL(18,2)   NULL,
    `allocation_p`          DECIMAL(10,4)   NULL,
    `category`              VARCHAR(16)     NULL,
    `operator_name`         VARCHAR(32)     NULL,
    `broker`                VARCHAR(32)     NULL,
    `parent_id`             VARCHAR(32)     NULL,
    `capital`               DECIMAL(20,2)   NULL,
    `error`                 BOOLEAN         NULL,
    `error_msg`             TEXT            NULL,
    `check_time`            DATETIME(3)     NULL,
    `proxy`                 VARCHAR(255)    NULL,
    `imported_at`           TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_running_user_time` (`userId`, `check_time`),
    KEY `idx_running_imported` (`imported_at`),
    KEY `idx_running_error` (`error`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Source: 'Server Configs.xlsx' -> first sheet, 'Servers'.
# Two sheet columns are deliberately absent:
#   `Dte`   = Expiry - TODAY(), a countdown that is stale the day after upload,
#             so it is computed on read from `Expiry` instead of stored.
#   (`Avlbl` = Subscriptions - Logins is a stable subtraction, so it is kept.)
# `Expiry` arrives as an Excel date serial (46265 = 2026-08-31) because the
# cells use the General format; it is converted on import, not stored as an int.
TABLES["server_config"] = """
CREATE TABLE `server_config` (
    `Server`           VARCHAR(16)     NOT NULL,
    `Username`         VARCHAR(32)     NULL,
    `IP`               VARCHAR(45)     NULL,
    `Password`         VARCHAR(128)    NULL,
    `Stoxxo Id`        VARCHAR(64)     NULL,
    `Stoxxo Password`  VARCHAR(64)     NULL,
    `Expiry`           DATE            NULL,
    `Subscriptions`    INT             NULL,
    `Logins`           INT             NULL,
    `Active`           INT             NULL,
    `Avlbl`            INT             NULL,
    `Aum`              DECIMAL(18,4)   NULL,
    `Remarks`          VARCHAR(255)    NULL,
    `Operator`         VARCHAR(64)     NULL,
    `Stoxxo URL`       VARCHAR(128)    NULL,
    `created_at`       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                       ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`Server`),
    KEY `idx_server_config_operator` (`Operator`),
    KEY `idx_server_config_expiry` (`Expiry`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# Portal logins. Passwords are stored in plain text by explicit project
# decision (internal portal, superadmin must be able to read them).
# ponytail: plaintext credentials - switch `password` to a werkzeug hash and
# drop the read path if this portal ever becomes network-reachable.
TABLES["login"] = """
CREATE TABLE `login` (
    `id`          INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    `role`        VARCHAR(32)     NOT NULL,
    `name`        VARCHAR(120)    NOT NULL,
    `email`       VARCHAR(190)    NOT NULL,
    `password`    VARCHAR(255)    NOT NULL,
    `created_at`  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_login_email` (`email`),
    KEY `idx_login_role` (`role`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

def _split_definitions(ddl: str) -> list[str]:
    """Split a CREATE TABLE body into its top-level comma-separated clauses."""
    body = ddl[ddl.index("(") + 1 : ddl.rindex(")")]

    # Strip '--' comments *before* splitting: a comma inside a comment would
    # otherwise be treated as a clause separator and swallow the column
    # definition that follows it.
    body = "\n".join(
        line for line in body.splitlines() if not line.strip().startswith("--")
    )

    parts: list[str] = []
    depth = 0
    current: list[str] = []

    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))

    # Collapse each clause onto a single line.
    return [text for text in (" ".join(part.split()) for part in parts) if text]


_COLUMN_CLAUSE = re.compile(r"^`([^`]+)`\s+(.+)$")
_CONSTRAINT_START = ("PRIMARY KEY", "KEY ", "UNIQUE KEY", "INDEX ", "CONSTRAINT", "FOREIGN KEY")


def ddl_columns(table: str) -> list[tuple[str, str]]:
    """Column name and definition pairs, in order, from this module's DDL."""
    columns = []
    for clause in _split_definitions(TABLES[table]):
        if clause.upper().startswith(_CONSTRAINT_START):
            continue
        match = _COLUMN_CLAUSE.match(clause)
        if match:
            columns.append((match.group(1), match.group(2)))
    return columns


def ensure_columns() -> list[str]:
    """Add columns present in the DDL but missing from an existing table.

    `ensure_tables()` only creates whole tables, so a column added to
    database/schema.py would otherwise need a manual ALTER on every existing
    database - and the app would fail at query time until someone ran it.

    Only *additions* are applied. Widening a type or changing a default is
    still a manual migration, deliberately: those can lose data.

    Returns:
        Descriptions of the columns added, e.g. "usersetting.server".
    """
    present = existing_tables()
    added: list[str] = []

    conn = _connect()
    try:
        cursor = conn.cursor()
        try:
            for table in TABLES:
                if table.lower() not in present:
                    continue  # ensure_tables() will create it in full

                cursor.execute(
                    "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (get_config()["database"], table),
                )
                have = {row[0].lower() for row in cursor.fetchall()}

                previous: str | None = None
                for name, definition in ddl_columns(table):
                    if name.lower() in have:
                        previous = name
                        continue

                    position = f" AFTER `{previous}`" if previous else " FIRST"
                    cursor.execute(
                        f"ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}{position}"
                    )
                    added.append(f"{table}.{name}")
                    logger.info("Added column `%s`.`%s` %s", table, name, definition)
                    previous = name

            conn.commit()
        finally:
            cursor.close()
    except Exception:
        logger.exception("Adding missing columns failed after: %s", added or "none")
        raise
    finally:
        conn.close()

    if added:
        logger.info("Added %s missing column(s): %s", len(added), ", ".join(added))
    return added


_PRIMARY_KEY_CLAUSE = re.compile(r"^PRIMARY KEY\s*\((.+)\)$", re.IGNORECASE)


def ddl_primary_key(table: str) -> list[str]:
    """The primary key columns declared for `table`, in order."""
    for clause in _split_definitions(TABLES[table]):
        match = _PRIMARY_KEY_CLAUSE.match(clause.strip())
        if match:
            return [part.strip().strip("`") for part in match.group(1).split(",")]
    return []


def ensure_primary_keys() -> list[str]:
    """Widen a primary key that has gained a column in the DDL.

    Only *additions* are applied, and that restriction is what makes this safe
    to run unattended: adding a column to a key cannot fail on duplicates,
    because the existing key already guarantees uniqueness on a subset of the
    new one. Narrowing a key can fail or lose rows, so it is reported and left
    for a human.

    A column joining the key must be NOT NULL, so any NULLs in it are filled
    with the column's default first.

    Returns:
        Descriptions of the keys changed, e.g. "usersetting (User ID, server)".
    """
    present = existing_tables()
    changed: list[str] = []

    conn = _connect()
    try:
        cursor = conn.cursor()
        try:
            for table in TABLES:
                if table.lower() not in present:
                    continue

                wanted = ddl_primary_key(table)
                if not wanted:
                    continue

                cursor.execute(
                    "SELECT COLUMN_NAME FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "AND INDEX_NAME = 'PRIMARY' ORDER BY SEQ_IN_INDEX",
                    (get_config()["database"], table),
                )
                actual = [row[0] for row in cursor.fetchall()]

                if [c.lower() for c in actual] == [c.lower() for c in wanted]:
                    continue

                have = {c.lower() for c in actual}
                if not have or not have.issubset({c.lower() for c in wanted}):
                    logger.warning(
                        "Primary key of `%s` is (%s) but the schema wants (%s). "
                        "That is not a widening, so it needs a manual migration.",
                        table, ", ".join(actual) or "none", ", ".join(wanted),
                    )
                    continue

                definitions = dict(ddl_columns(table))
                for column in wanted:
                    if column.lower() in have:
                        continue
                    # A key column cannot be NULL.
                    cursor.execute(
                        f"UPDATE `{table}` SET `{column}` = '' WHERE `{column}` IS NULL"
                    )
                    cursor.execute(
                        f"ALTER TABLE `{table}` MODIFY `{column}` {definitions[column]}"
                    )

                columns = ", ".join(f"`{c}`" for c in wanted)
                cursor.execute(
                    f"ALTER TABLE `{table}` DROP PRIMARY KEY, ADD PRIMARY KEY ({columns})"
                )
                changed.append(f"{table} ({', '.join(wanted)})")
                logger.info(
                    "Widened primary key of `%s` from (%s) to (%s)",
                    table, ", ".join(actual), ", ".join(wanted),
                )

            conn.commit()
        finally:
            cursor.close()
    except Exception:
        logger.exception("Updating primary keys failed after: %s", changed or "none")
        raise
    finally:
        conn.close()

    return changed


def existing_tables() -> set[str]:
    """Table names currently present in the configured database, lowercased.

    MySQL on Windows stores table names folded to lowercase
    (lower_case_table_names=1) while Linux servers preserve case, so names are
    normalised here and all table names in TABLES are declared lowercase.
    """
    conn = _connect()
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = %s",
                (get_config()["database"],),
            )
            return {row[0].lower() for row in cursor.fetchall()}
        finally:
            cursor.close()
    finally:
        conn.close()


def ensure_tables() -> list[str]:
    """Create any missing tables.

    Returns:
        Names of the tables created by this call (empty if all already existed).
    """
    present = existing_tables()
    missing = [name for name in TABLES if name.lower() not in present]

    if not missing:
        logger.info("All %s tables already exist", len(TABLES))
        return []

    conn = _connect()
    created: list[str] = []
    try:
        cursor = conn.cursor()
        try:
            for name in missing:
                cursor.execute(TABLES[name])
                created.append(name)
                logger.info("Created table '%s'", name)
        finally:
            cursor.close()
    except Exception:
        # DDL auto-commits in MySQL - tables created before the failure remain.
        logger.exception(
            "Table creation failed; already created: %s. Remaining: %s",
            created or "none",
            [n for n in missing if n not in created],
        )
        raise
    finally:
        conn.close()

    logger.info("Created %s of %s tables: %s", len(created), len(TABLES), ", ".join(created))
    return created


def _default_logins() -> list[tuple[str, str, str, str]]:
    """Bootstrap logins as (role, name, email, password).

    Read from the environment so credentials can differ per deployment.
    """
    return [
        (
            os.getenv("DEFAULT_ADMIN_ROLE", "admin"),
            os.getenv("DEFAULT_ADMIN_NAME", "admin"),
            os.getenv("DEFAULT_ADMIN_EMAIL", "admin@gmail.com"),
            os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"),
        ),
        (
            os.getenv("DEFAULT_SUPERADMIN_ROLE", "superadmin"),
            os.getenv("DEFAULT_SUPERADMIN_NAME", "superadmin"),
            os.getenv("DEFAULT_SUPERADMIN_EMAIL", "superadmin@gmail.com"),
            os.getenv("DEFAULT_SUPERADMIN_PASSWORD", "sadmin123"),
        ),
    ]


def ensure_default_admin() -> list[str]:
    """Insert any bootstrap login whose email is not already present.

    Returns:
        Emails created by this call (empty if all already existed).
    """
    conn = _connect()
    created: list[str] = []
    try:
        cursor = conn.cursor()
        try:
            for role, name, email, password in _default_logins():
                cursor.execute("SELECT `id` FROM `login` WHERE `email` = %s", (email,))
                if cursor.fetchone() is not None:
                    logger.info("Default login '%s' already exists", email)
                    continue

                cursor.execute(
                    "INSERT INTO `login` (`role`, `name`, `email`, `password`) "
                    "VALUES (%s, %s, %s, %s)",
                    (role, name, email, password),
                )
                created.append(email)
                logger.info("Created default login '%s' with role '%s'", email, role)

            conn.commit()
            return created
        finally:
            cursor.close()
    except Exception:
        conn.rollback()
        logger.exception("Failed to create default logins")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    from database.db import setup_logging

    setup_logging()
    ensure_tables()
    ensure_columns()
    ensure_default_admin()

    # Self-check: every declared table must now exist.
    present = existing_tables()
    missing = [name for name in TABLES if name.lower() not in present]
    assert not missing, f"Tables still missing after provisioning: {missing}"
    logger.info("Self-check passed - all %s tables present", len(TABLES))
