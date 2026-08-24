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


def create_app() -> Flask:
    """Build and configure the Flask application."""
    setup_logging()
    logger.info("Starting OMP")

    app = Flask(__name__)
    # Dev fallback only - set SECRET_KEY in .env before deploying.
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
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
    app.run(
        host=os.getenv("FLASK_HOST", "127.0.0.1"),
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "true").lower() == "true",
    )
