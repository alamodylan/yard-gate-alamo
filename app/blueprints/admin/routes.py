from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user

from app.blueprints.admin import admin_bp
from app.extensions import db
from app.models.user import User
from app.models.site import Site, UserSite
from app.models.audit import AuditLog
from app.utils.security import admin_required
from app.services.audit import audit_log


@admin_bp.get("/users")
@login_required
@admin_required
def users_view():
    users = User.query.order_by(User.id.desc()).all()
    sites = Site.query.filter_by(is_active=True).order_by(Site.name.asc()).all()

    return render_template(
        "admin/users.html",
        users=users,
        sites=sites,
        allowed_roles=sorted(User.ALLOWED_ROLES),
    )


@admin_bp.post("/users/create")
@login_required
@admin_required
def users_create():
    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""
    role = (request.form.get("role") or User.ROLE_INSPECCION).strip().lower()
    site_ids = request.form.getlist("site_ids")

    if not username or len(username) < 3:
        flash("Usuario inválido (mín 3 caracteres).", "danger")
        return redirect(url_for("admin.users_view"))

    if role not in User.ALLOWED_ROLES:
        flash("Rol inválido.", "danger")
        return redirect(url_for("admin.users_view"))

    if len(password) < 6:
        flash("Contraseña muy corta (mín 6).", "danger")
        return redirect(url_for("admin.users_view"))

    exists = User.query.filter_by(username=username).first()
    if exists:
        flash("Ese usuario ya existe.", "danger")
        return redirect(url_for("admin.users_view"))

    if role != User.ROLE_ADMIN and not site_ids:
        flash("Debe seleccionar al menos un predio para usuarios no administradores.", "danger")
        return redirect(url_for("admin.users_view"))

    valid_site_ids = {
        str(site.id)
        for site in Site.query.filter_by(is_active=True).all()
    }

    selected_site_ids = [
        int(site_id)
        for site_id in site_ids
        if site_id in valid_site_ids
    ]

    if role != User.ROLE_ADMIN and not selected_site_ids:
        flash("Los predios seleccionados no son válidos.", "danger")
        return redirect(url_for("admin.users_view"))

    u = User(username=username, role=role, is_active=True)
    u.set_password(password)

    db.session.add(u)
    db.session.flush()

    if role != User.ROLE_ADMIN:
        for site_id in selected_site_ids:
            db.session.add(
                UserSite(
                    user_id=u.id,
                    site_id=site_id,
                )
            )

    audit_log(
        current_user.id,
        "USER_CREATED",
        "user",
        u.id,
        {
            "username": username,
            "role": role,
            "site_ids": selected_site_ids if role != User.ROLE_ADMIN else "ALL",
        },
    )

    db.session.commit()

    flash("Usuario creado.", "success")
    return redirect(url_for("admin.users_view"))


@admin_bp.post("/users/update/<int:user_id>")
@login_required
@admin_required
def users_update(user_id: int):
    u = User.query.get_or_404(user_id)

    role = (request.form.get("role") or "").strip().lower()
    site_ids = request.form.getlist("site_ids")

    if role not in User.ALLOWED_ROLES:
        flash("Rol inválido.", "danger")
        return redirect(url_for("admin.users_view"))

    if u.id == current_user.id and role != User.ROLE_ADMIN:
        flash("No puedes quitarte el rol admin a ti mismo.", "warning")
        return redirect(url_for("admin.users_view"))

    valid_site_ids = {
        str(site.id)
        for site in Site.query.filter_by(is_active=True).all()
    }

    selected_site_ids = [
        int(site_id)
        for site_id in site_ids
        if site_id in valid_site_ids
    ]

    if role != User.ROLE_ADMIN and not selected_site_ids:
        flash("Debe seleccionar al menos un predio para usuarios no administradores.", "danger")
        return redirect(url_for("admin.users_view"))

    old_role = u.role
    old_site_ids = [us.site_id for us in getattr(u, "user_sites", [])]

    u.role = role

    UserSite.query.filter_by(user_id=u.id).delete()

    if role != User.ROLE_ADMIN:
        for site_id in selected_site_ids:
            db.session.add(
                UserSite(
                    user_id=u.id,
                    site_id=site_id,
                )
            )

    audit_log(
        current_user.id,
        "USER_UPDATED",
        "user",
        u.id,
        {
            "old_role": old_role,
            "new_role": role,
            "old_site_ids": old_site_ids,
            "new_site_ids": selected_site_ids if role != User.ROLE_ADMIN else "ALL",
        },
    )

    db.session.commit()

    flash("Usuario actualizado.", "success")
    return redirect(url_for("admin.users_view"))


@admin_bp.post("/users/toggle/<int:user_id>")
@login_required
@admin_required
def users_toggle(user_id: int):
    u = User.query.get_or_404(user_id)

    if u.id == current_user.id:
        flash("No puedes desactivarte a ti mismo 😄", "warning")
        return redirect(url_for("admin.users_view"))

    u.is_active = not u.is_active

    audit_log(
        current_user.id,
        "USER_TOGGLED",
        "user",
        u.id,
        {
            "is_active": u.is_active,
        },
    )

    db.session.commit()

    flash("Estado actualizado.", "success")
    return redirect(url_for("admin.users_view"))


@admin_bp.get("/audit")
@login_required
@admin_required
def audit_view():
    q_user = (request.args.get("user") or "").strip()
    q_action = (request.args.get("action") or "").strip()

    q = db.session.query(AuditLog, User.username).outerjoin(
        User,
        User.id == AuditLog.user_id,
    )

    if q_user.isdigit():
        q = q.filter(AuditLog.user_id == int(q_user))

    if q_action:
        q = q.filter(AuditLog.action.ilike(f"%{q_action}%"))

    logs = q.order_by(AuditLog.at.desc()).limit(500).all()

    return render_template("admin/audit.html", logs=logs)