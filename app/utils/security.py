# app/utils/security.py
from functools import wraps
from flask import flash, redirect, url_for
from flask_login import current_user

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))

        role = (getattr(current_user, "role", "") or "").strip().lower()
        if role != "admin":
            flash("No tienes permisos de administrador.", "danger")
            return redirect(url_for("yard.map_view"))

        return fn(*args, **kwargs)
    return wrapper
