from flask import render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required

from app.blueprints.auth import auth_bp
from app.models.user import User

@auth_bp.get("/login")
def login():
    return render_template("auth/login.html")

@auth_bp.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not user.check_password(password):
        flash("Credenciales inválidas", "danger")
        return redirect(url_for("auth.login"))

    login_user(user)
    return redirect(url_for("yard.map_view"))

@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))