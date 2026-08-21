"""Authentication: login, logout, and role-based access decorators."""

from __future__ import annotations

import logging
from functools import wraps
from typing import Callable

from flask import (
    Blueprint, flash, redirect, render_template, request, session, url_for
)
from sqlalchemy import text

from database.db import db

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

# Landing route per role. Only 'admin' exists so far; the rest fall back to it
# until their blueprints are built.
ROLE_HOME: dict[str, str] = {
    "superadmin": "admin.dashboard",
    "admin": "admin.dashboard",
    "operator": "admin.dashboard",
    "crm": "admin.dashboard",
}


def login_required(view: Callable) -> Callable:
    """Reject anonymous requests."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*allowed: str) -> Callable:
    """Reject users whose role is not in `allowed`."""
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            role = session.get("role")
            if role not in allowed:
                logger.warning(
                    "Access denied: user %s (role=%s) tried %s",
                    session.get("email"), role, request.path,
                )
                flash("You do not have access to that page.", "error")
                return redirect(url_for(ROLE_HOME.get(role, "auth.login")))
            return view(*args, **kwargs)
        return wrapped
    return decorator


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("login.html", email=email), 400

        row = db.session.execute(
            text("SELECT `id`, `role`, `name`, `email`, `password` "
                 "FROM `login` WHERE LOWER(`email`) = :email"),
            {"email": email},
        ).mappings().first()

        # ponytail: plaintext comparison per project decision (internal portal).
        if row is None or row["password"] != password:
            logger.warning("Failed login for '%s' from %s", email, request.remote_addr)
            flash("Invalid email or password.", "error")
            return render_template("login.html", email=email), 401

        session.clear()
        session["user_id"] = row["id"]
        session["role"] = row["role"]
        session["name"] = row["name"]
        session["email"] = row["email"]
        logger.info("Login: %s (role=%s)", row["email"], row["role"])

        # Only allow relative redirects - an absolute URL here is open redirect.
        nxt = request.args.get("next")
        if nxt and nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for(ROLE_HOME.get(row["role"], "auth.login")))

    if "user_id" in session:
        return redirect(url_for(ROLE_HOME.get(session.get("role"), "auth.login")))
    return render_template("login.html", email="")


@bp.route("/logout")
def logout():
    logger.info("Logout: %s", session.get("email"))
    session.clear()
    return redirect(url_for("auth.login"))
