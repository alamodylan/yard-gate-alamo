# app/__init__.py
from flask import Flask, current_app, session, url_for
from dotenv import load_dotenv

from app.config import Config
from app.extensions import db, migrate, login_manager
from flask_login import current_user

import pytz
from datetime import datetime


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # =========================
    # Timezone / Date formatting
    # =========================
    CR_TZ = pytz.timezone("America/Costa_Rica")
    UTC_TZ = pytz.utc

    def _to_cr(dt: datetime | None) -> datetime | None:
        """Convierte un datetime (asumido UTC si viene naive) a Costa Rica."""
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = UTC_TZ.localize(dt)
        return dt.astimezone(CR_TZ)

    @app.template_filter("dt_cr")
    def dt_cr(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        """
        Uso en Jinja: {{ mv.occurred_at|dt_cr }}
        - Convierte a Costa Rica
        - Formatea sin microsegundos
        """
        local_dt = _to_cr(dt)
        if not local_dt:
            return "—"
        return local_dt.strftime(fmt)

    # ✅ Helper global para templates: has_endpoint('blueprint.endpoint')
    @app.context_processor
    def inject_has_endpoint():
        def has_endpoint(endpoint: str) -> bool:
            return endpoint in current_app.view_functions
        return dict(has_endpoint=has_endpoint)
    
    # ✅ Helper global para permisos en templates: can('permiso')
    @app.context_processor
    def inject_permissions():
        from app.utils.permissions import user_has_permission

        return {
            "can": lambda permission: user_has_permission(current_user, permission),
        }

    # ✅ Predio activo (site) para templates + helper
    @app.context_processor
    def inject_active_site():
        """
        Inyecta:
          - active_site_id: int|None
          - active_site: Site|None
          - get_active_site_id(): callable
        """
        from app.models.site import Site  # import aquí para evitar ciclos

        def get_active_site_id():
            v = session.get("active_site_id")
            try:
                return int(v) if v is not None else None
            except Exception:
                return None

        sid = get_active_site_id()
        site = Site.query.get(sid) if sid else None

        return dict(
            active_site_id=sid,
            active_site=site,
            get_active_site_id=get_active_site_id
        )
    
        # ✅ Notificaciones globales para campanita
    @app.context_processor
    def inject_notifications():
        from app.models.dispatch import UserNotification
        from app.services.notifications import notification_url

        if not current_user.is_authenticated:
            return dict(
                notification_count=0,
                notification_items=[],
            )

        active_site_id = session.get("active_site_id")

        unread_query = UserNotification.query.filter(
            UserNotification.user_id == current_user.id,
            UserNotification.is_read == False,  # noqa: E712
        )

        list_query = UserNotification.query.filter(
            UserNotification.user_id == current_user.id,
        )

        if active_site_id:
            unread_query = unread_query.filter(UserNotification.site_id == int(active_site_id))
            list_query = list_query.filter(UserNotification.site_id == int(active_site_id))

        latest_notifications = (
            list_query
            .order_by(UserNotification.created_at.desc())
            .limit(10)
            .all()
        )

        notification_items = []

        for n in latest_notifications:
            endpoint, params = notification_url(n)

            href = "#"
            if endpoint and endpoint in current_app.view_functions:
                href = url_for("dispatch.read_notification", notification_id=n.id)

            notification_items.append({
                "id": n.id,
                "title": n.title,
                "message": n.message,
                "related_type": n.related_type,
                "related_id": n.related_id,
                "created_at": n.created_at,
                "is_read": n.is_read,
                "href": href,
            })

        return dict(
            notification_count=unread_query.count(),
            notification_items=notification_items,
        )

    # Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.yard import yard_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.inventory import inventory_bp
    from app.blueprints.print_api.routes import bp as print_api_bp
    from app.blueprints.dispatch import dispatch_bp

    app.register_blueprint(inventory_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(yard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dispatch_bp)
    app.register_blueprint(print_api_bp)

    # Simple healthcheck
    @app.get("/health")
    def health():
        return {"ok": True}

    return app