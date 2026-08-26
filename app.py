"""OMP - Operations Management Portal. Application entry point.

Run:
    python app.py

On startup this creates the 'omp' database if it does not exist, then serves
the Flask app. Blueprints and models get registered in create_app().
"""

from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, redirect, session, url_for
from sqlalchemy import text

import auth
from core.tables import nav_pages
from database.db import db, init_app, setup_logging
from roles.admin import bp as admin_bp
from roles.superadmin import bp as superadmin_bp

logger = logging.getLogger(__name__)

# The fallback key, named so it can be recognised and refused.
DEV_SECRET = "dev-only-change-me"


def _production() -> bool:
    """Anything that is not an explicit local development run."""
    return os.getenv("OMP_ENV", "production").lower() != "development"


def create_app() -> Flask:
    """Build and configure the Flask application."""
    setup_logging()
    logger.info("Starting OMP")

    app = Flask(__name__)

    # A guessable secret key means anyone can forge a session cookie and sign
    # in as superadmin. Outside development the app refuses to start rather
    # than quietly running on the fallback.
    secret = os.getenv("SECRET_KEY", "")
    if not secret or secret == DEV_SECRET:
        if _production():
            raise RuntimeError(
                "SECRET_KEY is not set. Generate one with "
                "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
                "and put it in the environment before starting OMP."
            )
        secret = DEV_SECRET
        logger.warning(
            "Running on the development SECRET_KEY. Sessions are forgeable - "
            "never use this outside a local machine."
        )
    app.config["SECRET_KEY"] = secret

    # Session cookies: not readable from JavaScript, not sent cross-site, and
    # HTTPS-only once a certificate is in front of the app.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true",
    )
    # Reject oversized uploads before they are read into memory.
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024

    init_app(app)  # ensures the database exists, then binds Flask-SQLAlchemy

    app.register_blueprint(auth.bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(superadmin_bp)

    @app.context_processor
    def inject_nav():
        """Nav links are driven by the table page registry."""
        return {"table_pages": nav_pages()}

    @app.route("/")
    def index():
        """Send signed-in users to their role's landing page, others to login."""
        role = session.get("role")
        if role:
            return redirect(url_for(auth.ROLE_HOME.get(role, "auth.login")))
        return redirect(url_for("auth.login"))

    @app.route("/health")
    def health():
        """Liveness + DB connectivity check."""
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify(status="ok", database=app.config["SQLALCHEMY_DATABASE_URI"].rsplit("/", 1)[-1])
        except Exception as exc:  # surfaced to the caller, not swallowed
            logger.exception("Health check failed")
            return jsonify(status="error", detail=str(exc)), 503

    logger.info("Application ready")
    return app


app = create_app()


if __name__ == "__main__":
    # `python app.py` is the development server. In a container gunicorn
    # imports `app` directly and never reaches this block. Debug defaults off:
    # the interactive debugger is a remote shell for anyone who can reach it.
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
