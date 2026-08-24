"""All Users is a group of views sharing one navbar tab.

    python tests/test_subtabs.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_USER", "u")
os.environ.setdefault("DB_NAME", "omp")
os.environ.setdefault("SECRET_KEY", "test")

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
import roles.superadmin as superadmin  # noqa: E402
from database.db import db  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label} -> {got}")
    else:
        failures.append(label)
        print(f"FAIL  {label} -> {got!r}   want {want!r}")


def make_app() -> Flask:
    app = Flask(__name__, template_folder=str(ROOT / "templates"),
                static_folder=str(ROOT / "static"))
    app.config.update(SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(superadmin.bp)

    @app.context_processor
    def nav():
        return {"table_pages": tables.nav_pages()}

    return app


def client(app: Flask, role: str = "admin"):
    c = app.test_client()
    with c.session_transaction() as session:
        session.update(user_id=1, role=role, name="X", email="x@y.z")
    return c


# Built through Data Operation, so no longer placeholders.
BUILT = ("category", "incidents", "personal", "positional")
PENDING = ("exceptions",)


def run() -> None:
    print("-- the registry --")
    check("navbar tabs", list(tables.nav_pages()),
          ["all-users", "running", "usersetting", "server-config"])
    check("jainam moved out of the navbar", "jainam" in tables.nav_pages(), False)
    check("sub-tab order", [t["key"] for t in tables.subtabs("all-users")],
          ["all-users", "jainam", "category", "incidents",
           "positional", "exceptions", "personal"])
    check("captions", [t["label"] for t in tables.subtabs("all-users")],
          ["Main", "Jainam", "Category", "Incidents", "Positional",
           "Exceptions", "Personal"])
    check("current is the page you are on",
          [t["label"] for t in tables.subtabs("jainam") if t["current"]], ["Jainam"])
    check("only the unbuilt ones are pending",
          [t["key"] for t in tables.subtabs("all-users") if t["pending"]], list(PENDING))
    check("the built ones are not",
          [t["key"] for t in tables.subtabs("all-users")
           if t["key"] in BUILT and t["pending"]], [])
    check("Incidents points at the singular table",
          tables.TABLE_PAGES["incidents"]["table"], "incident")
    check("Positional shows the maxloss sheet",
          tables.TABLE_PAGES["positional"]["table"], "maxloss")
    check("a page in no group gets no strip", tables.subtabs("running"), [])

    app = make_app()
    with app.app_context():
        print("\n-- pages that have no table yet --")
        for key in PENDING:
            response = client(app).get(f"/admin/table/{key}")
            html = response.data.decode()
            check(f"{key}: renders", response.status_code, 200)
            check(f"{key}: says so", "is not built yet" in html, True)
            # The grid script would fetch and fail; it must not be included.
            check(f"{key}: no grid script", "OMP_TABLE" in html, False)

        html = client(app).get("/admin/table/exceptions").data.decode()
        strip = re.search(r'<nav class="subtabs">.*?</nav>', html, re.S).group(0)
        check("strip lists every sibling", len(re.findall(r"<a class=\"subtab", strip)), 7)
        check("one is marked current", len(re.findall(r'class="subtab active"', strip)), 1)
        check("the unbuilt one is dotted", len(re.findall(r"subtab-dot", strip)), 1)

        navbar = re.search(r'<nav class="nav">.*?</nav>', html, re.S).group(0)
        check("Jainam is not in the navbar", "Jainam</a>" in navbar, False)
        check("All Users is still there", "All Users</a>" in navbar, True)

        print("\n-- the JSON endpoints know too --")
        response = client(app).get("/admin/api/table/exceptions")
        check("rows endpoint answers empty",
              (response.status_code, json.loads(response.data)),
              (200, {"columns": [], "rows": [], "date": None}))
        check("delete is refused",
              client(app).post("/admin/table/exceptions/delete",
                               json={"keys": [["x"]]}).status_code, 400)
        check("an unknown page still 404s",
              client(app).get("/admin/table/nope").status_code, 404)
        check("operators see the sub-tabs too",
              client(app, "operator").get("/admin/table/exceptions").status_code, 200)


if __name__ == "__main__":
    run()
    print("\n" + ("All sub-tab checks passed" if not failures
                  else f"{len(failures)} FAILED: {', '.join(failures)}"))
    sys.exit(1 if failures else 0)
