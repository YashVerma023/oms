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

from database.db import _connect, get_config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DDL - ordered dict; tables are created in this order.
# ---------------------------------------------------------------------------

TABLES: dict[str, str] = {}

# Source: 'All User.xlsx' -> Main sheet.
TABLES["all_users"] = """
CREATE TABLE `all_users` (
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
    `created_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`     TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                     ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`userId`),
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
    `created_at`                  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at`                  TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                                  ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`User ID`),
    KEY `idx_usersetting_broker` (`Broker`)
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
    ensure_default_admin()

    # Self-check: every declared table must now exist.
    present = existing_tables()
    missing = [name for name in TABLES if name.lower() not in present]
    assert not missing, f"Tables still missing after provisioning: {missing}"
    logger.info("Self-check passed - all %s tables present", len(TABLES))
