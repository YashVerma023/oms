"""MySQL connectivity for OMP.

Responsibilities:
    1. Load DB credentials from the project-level .env file.
    2. Create the target database if it does not already exist (logged).
    3. Expose the Flask-SQLAlchemy extension used by the rest of the app.

Run standalone to provision the database:
    python -m database.db
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import mysql.connector
from dotenv import load_dotenv
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from mysql.connector import errorcode

BASE_DIR: Path = Path(__file__).resolve().parent.parent
LOG_DIR: Path = BASE_DIR / "logs"
ENV_PATH: Path = BASE_DIR / ".env"

load_dotenv(ENV_PATH)

logger = logging.getLogger(__name__)

db = SQLAlchemy()

CONNECT_RETRIES: int = 3
RETRY_DELAY_SECONDS: int = 2
# MySQL identifier safety: the DB name reaches CREATE DATABASE as a literal
# (identifiers cannot be parameterized), so it is whitelisted, not escaped.
_VALID_DB_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def setup_logging(level: str | None = None) -> None:
    """Configure root logging to logs/omp.log + stderr. Call once at app startup."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=(level or os.getenv("LOG_LEVEL", "INFO")).upper(),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(LOG_DIR / "omp.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # mysql-connector logs plugin loading at INFO on every connect - noise.
    logging.getLogger("mysql.connector").setLevel(logging.WARNING)


def get_config() -> dict[str, Any]:
    """Read DB settings from the environment. Raises if required values are missing."""
    if not ENV_PATH.exists():
        logger.warning("No .env file at %s - falling back to process environment", ENV_PATH)

    user = os.getenv("DB_USER")
    if not user:
        raise RuntimeError(f"DB_USER is not set (expected in {ENV_PATH})")

    name = os.getenv("DB_NAME", "omp")
    if not _VALID_DB_NAME.match(name):
        raise ValueError(f"Invalid DB_NAME {name!r}: use letters, digits and underscore only")

    try:
        port = int(os.getenv("DB_PORT", "3306"))
    except ValueError as exc:
        raise ValueError(f"DB_PORT must be an integer, got {os.getenv('DB_PORT')!r}") from exc

    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": port,
        "user": user,
        # Empty password is valid for a default local MySQL/XAMPP root account.
        "password": os.getenv("DB_PASSWORD", ""),
        "database": name,
    }


def _connect(server_only: bool = False) -> mysql.connector.MySQLConnection:
    """Open a raw connection, retrying transient failures.

    Args:
        server_only: connect to the MySQL server without selecting a database
            (required before the database exists).
    """
    cfg = get_config()
    target = cfg.pop("database")
    if not server_only:
        cfg["database"] = target

    last_error: Exception | None = None
    for attempt in range(1, CONNECT_RETRIES + 1):
        try:
            conn = mysql.connector.connect(**cfg, charset="utf8mb4", connection_timeout=10)
            logger.debug("Connected to MySQL at %s:%s as %s", cfg["host"], cfg["port"], cfg["user"])
            return conn
        except mysql.connector.Error as exc:
            last_error = exc
            if exc.errno in (errorcode.ER_ACCESS_DENIED_ERROR, errorcode.ER_BAD_DB_ERROR):
                logger.error("MySQL rejected the connection (errno %s): %s", exc.errno, exc.msg)
                raise
            logger.warning(
                "MySQL connection attempt %s/%s to %s:%s failed: %s",
                attempt, CONNECT_RETRIES, cfg["host"], cfg["port"], exc,
            )
            if attempt < CONNECT_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    raise ConnectionError(
        f"Could not connect to MySQL at {cfg['host']}:{cfg['port']} after {CONNECT_RETRIES} attempts"
    ) from last_error


def ensure_database() -> bool:
    """Create the configured database if missing.

    Returns:
        True if the database was created by this call, False if it already existed.
    """
    cfg = get_config()
    name = cfg["database"]
    conn = _connect(server_only=True)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute("SHOW DATABASES LIKE %s", (name,))
            if cursor.fetchone() is not None:
                logger.info("Database '%s' already exists on %s:%s", name, cfg["host"], cfg["port"])
                return False

            cursor.execute(
                f"CREATE DATABASE `{name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            logger.info(
                "Database '%s' created on %s:%s (utf8mb4/utf8mb4_unicode_ci)",
                name, cfg["host"], cfg["port"],
            )
            return True
        finally:
            cursor.close()
    finally:
        conn.close()


def build_uri() -> str:
    """SQLAlchemy URI for the configured database."""
    cfg = get_config()
    return (
        f"mysql+mysqlconnector://{quote_plus(cfg['user'])}:{quote_plus(cfg['password'])}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}?charset=utf8mb4"
    )


def init_app(app: Flask) -> None:
    """Provision the database and tables if needed, then bind Flask-SQLAlchemy."""
    # Local import: schema imports this module.
    from database.schema import ensure_default_admin, ensure_tables

    ensure_database()
    ensure_tables()
    ensure_default_admin()
    app.config["SQLALCHEMY_DATABASE_URI"] = build_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,   # drop connections MySQL closed under wait_timeout
        "pool_recycle": 280,
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "10")),
    }
    db.init_app(app)
    logger.info("Flask-SQLAlchemy bound to database '%s'", get_config()["database"])


if __name__ == "__main__":
    setup_logging()
    created = ensure_database()

    # Self-check: the database must be reachable and selectable after provisioning.
    conn = _connect()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DATABASE()")
        selected = cursor.fetchone()[0]
        cursor.close()
    finally:
        conn.close()

    assert selected == get_config()["database"], f"Selected {selected!r}, expected {get_config()['database']!r}"
    logger.info("Self-check passed - connected to '%s' (created=%s)", selected, created)
