# app/__init__.py

from time import perf_counter

import pytz
from datetime import datetime

from dotenv import load_dotenv
from flask import (
    Flask,
    current_app,
    g,
    request,
    session,
)
from flask_login import current_user

from app.config import Config
from app.extensions import db, migrate, login_manager


def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    # =========================================================
    # Extensions
    # =========================================================
    db.init_app(app)
    migrate.init_app(app, db)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # =========================================================
    # Timezone / Date formatting
    # =========================================================
    cr_tz = pytz.timezone("America/Costa_Rica")
    utc_tz = pytz.utc

    def _to_cr(dt: datetime | None) -> datetime | None:
        """
        Convierte un datetime a Costa Rica.

        Si el datetime no trae timezone, se interpreta como UTC,
        conservando el comportamiento actual del sistema.
        """
        if not dt:
            return None

        if dt.tzinfo is None:
            dt = utc_tz.localize(dt)

        return dt.astimezone(cr_tz)

    @app.template_filter("dt_cr")
    def dt_cr(
        dt: datetime | None,
        fmt: str = "%Y-%m-%d %H:%M:%S",
    ) -> str:
        local_dt = _to_cr(dt)

        if not local_dt:
            return "—"

        return local_dt.strftime(fmt)

    # =========================================================
    # Contexto de petición
    # =========================================================
    @app.before_request
    def load_request_context():
        """
        Prepara datos reutilizables durante una única petición.

        Evita que distintos context processors consulten varias veces
        el mismo predio activo.
        """
        g.request_started_at = perf_counter()

        active_site_id = session.get("active_site_id")

        try:
            active_site_id = (
                int(active_site_id)
                if active_site_id is not None
                else None
            )
        except (TypeError, ValueError):
            active_site_id = None

        g.active_site_id = active_site_id
        g.active_site = None

        if active_site_id:
            from app.models.site import Site

            g.active_site = db.session.get(
                Site,
                active_site_id,
            )

    # =========================================================
    # Medición de rendimiento
    # =========================================================
    @app.after_request
    def log_slow_request(response):
        """
        Registra en Render las peticiones lentas.

        No cambia la respuesta ni afecta la lógica de negocio.
        """
        started_at = getattr(
            g,
            "request_started_at",
            None,
        )

        if started_at is None:
            return response

        elapsed_ms = (
            perf_counter() - started_at
        ) * 1000

        try:
            slow_request_ms = int(
                current_app.config.get(
                    "SLOW_REQUEST_MS",
                    1000,
                )
            )
        except (TypeError, ValueError):
            slow_request_ms = 1000

        if elapsed_ms >= slow_request_ms:
            current_app.logger.warning(
                "SLOW_REQUEST method=%s path=%s status=%s duration_ms=%.2f",
                request.method,
                request.path,
                response.status_code,
                elapsed_ms,
            )

        return response

    # =========================================================
    # Helper global: has_endpoint()
    # =========================================================
    @app.context_processor
    def inject_has_endpoint():
        def has_endpoint(endpoint: str) -> bool:
            return endpoint in current_app.view_functions

        return {
            "has_endpoint": has_endpoint,
        }

    # =========================================================
    # Helper global: can()
    # =========================================================
    @app.context_processor
    def inject_permissions():
        from app.utils.permissions import user_has_permission

        return {
            "can": lambda permission: user_has_permission(
                current_user,
                permission,
            ),
        }

    # =========================================================
    # Predio activo para templates
    # =========================================================
    @app.context_processor
    def inject_active_site():
        """
        Inyecta:

        - active_site_id
        - active_site
        - get_active_site_id()

        Usa el valor ya cargado en flask.g y no vuelve a consultar
        la base de datos.
        """
        active_site_id = getattr(
            g,
            "active_site_id",
            None,
        )

        active_site = getattr(
            g,
            "active_site",
            None,
        )

        def get_active_site_id():
            return getattr(
                g,
                "active_site_id",
                None,
            )

        return {
            "active_site_id": active_site_id,
            "active_site": active_site,
            "get_active_site_id": get_active_site_id,
        }

    # =========================================================
    # Notificaciones globales para la campanita
    # =========================================================
    @app.context_processor
    def inject_notifications():
        """
        Inyecta únicamente valores iniciales vacíos.

        Las notificaciones se cargarán de forma diferida desde base.html
        mediante un endpoint JSON, evitando consultas a PostgreSQL durante
        la carga principal de cada pantalla.
        """
        return {
            "notification_count": 0,
            "notification_items": [],
        }

    # =========================================================
    # Blueprints
    # =========================================================
    from app.blueprints.auth import auth_bp
    from app.blueprints.yard import yard_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.inventory import inventory_bp
    from app.blueprints.dispatch import dispatch_bp
    from app.blueprints.print_api.routes import bp as print_api_bp

    app.register_blueprint(inventory_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(yard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dispatch_bp)
    app.register_blueprint(print_api_bp)

    # =========================================================
    # Healthcheck
    # =========================================================
    @app.get("/health")
    def health():
        return {
            "ok": True,
        }

    return app