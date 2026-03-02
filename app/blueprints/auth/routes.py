from flask import render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required

from app.blueprints.auth import auth_bp
from app.models.user import User
from app.models.site import Site, UserSite  # ✅ NUEVO (los creamos/ya los vas a crear)

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

    # ✅ Si es admin: no fijamos predio automático (que elija)
    # ✅ Si NO es admin:
    #    - si tiene 1 solo predio -> set session y al mapa
    #    - si tiene varios -> selector
    session.pop("active_site_id", None)

    if (user.role or "").lower() != "admin":
        sites = (
            Site.query
            .join(UserSite, UserSite.site_id == Site.id)
            .filter(UserSite.user_id == user.id, Site.is_active == True)  # noqa: E712
            .order_by(Site.name.asc())
            .all()
        )

        if len(sites) == 1:
            session["active_site_id"] = sites[0].id
            return redirect(url_for("yard.map_view"))

    return redirect(url_for("yard.sites_view"))  # ✅ nueva vista

@auth_bp.get("/logout")
@login_required
def logout():
    logout_user()
    session.pop("active_site_id", None)  # ✅ limpia predio activo
    return redirect(url_for("auth.login"))