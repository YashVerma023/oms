"""Superadmin blueprint.

Superadmin has every admin right plus the extras defined here. Admin views are
not duplicated - the admin blueprint already allows the superadmin role.
"""

from __future__ import annotations

import logging

from flask import Blueprint, flash, redirect, render_template, request, url_for

import access
from auth import roles_required
from core import logins

logger = logging.getLogger(__name__)

bp = Blueprint("superadmin", __name__, url_prefix="/superadmin")


@bp.route("/msusers", methods=["GET", "POST"])
@roles_required("superadmin")
def msusers():
    """List portal logins and add new ones."""
    if request.method == "POST":
        try:
            logins.create_login(
                role=request.form.get("role", ""),
                name=request.form.get("name", ""),
                email=request.form.get("email", ""),
                password=request.form.get("password", ""),
            )
            flash(f"Added {request.form.get('email')}.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        except Exception:
            flash("Could not add the user." + access.failure_detail(), "error")
        return redirect(url_for("superadmin.msusers"))

    return render_template(
        "superadmin/msusers.html",
        users=logins.list_logins(),
        roles=logins.ROLES,
    )
