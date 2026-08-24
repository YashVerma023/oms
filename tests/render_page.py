"""Render a page to a file so a jsdom test can drive real markup.

    python tests/render_page.py /admin/setup /tmp/setup.html

No MySQL is needed: the page ships an empty table and fetches its data over
JSON, which the jsdom test stubs.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_USER", "u")
os.environ.setdefault("DB_NAME", "omp")
os.environ.setdefault("SECRET_KEY", "test")

# The connector is imported at module load but never used against a real server.
if "mysql.connector" not in sys.modules:
    sys.modules["mysql"] = types.ModuleType("mysql")
    connector = types.ModuleType("mysql.connector")
    connector.Error = Exception
    connector.MySQLConnection = object
    connector.errorcode = types.SimpleNamespace(
        ER_ACCESS_DENIED_ERROR=1045, ER_BAD_DB_ERROR=1049
    )
    sys.modules["mysql.connector"] = connector

from flask import Flask  # noqa: E402

import auth  # noqa: E402
import core.tables as tables  # noqa: E402
import roles.admin as admin  # noqa: E402
from database.db import db  # noqa: E402


def render(route: str, out: Path) -> None:
    app = Flask(
        __name__,
        template_folder=str(ROOT / "templates"),
        static_folder=str(ROOT / "static"),
    )
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)

    @app.context_processor
    def nav():
        return {"table_pages": tables.nav_pages()}

    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, role="admin", name="admin", email="test@example.com")

    response = client.get(route)
    if response.status_code != 200:
        raise SystemExit(f"{route} returned HTTP {response.status_code}")

    out.write_bytes(response.data)
    print(f"Wrote {out} ({len(response.data)} bytes)")


if __name__ == "__main__":
    render(sys.argv[1], Path(sys.argv[2]))
