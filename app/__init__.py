# app/__init__.py

from time import perf_counter

import pytz
from datetime import datetime

from dotenv import load_dotenv
from flask import (
    Flask,
    current_app,
    g,
    has_request_context,
    request,
    session,
)
from flask_login import current_user
from sqlalchemy import event
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
    # Instrumentación SQL por petición
    # =========================================================
    with app.app_context():
        engine = db.engine

        if not getattr(engine, "_yard_sql_metrics_registered", False):

            @event.listens_for(engine, "before_cursor_execute")
            def before_cursor_execute(
                conn,
                cursor,
                statement,
                parameters,
                context,
                executemany,
            ):
                if has_request_context():
                    context._yard_query_started_at = perf_counter()

            @event.listens_for(engine, "after_cursor_execute")
            def after_cursor_execute(
                conn,
                cursor,
                statement,
                parameters,
                context,
                executemany,
            ):
                if not has_request_context():
                    return

                started_at = getattr(
                    context,
                    "_yard_query_started_at",
                    None,
                )

                if started_at is None:
                    return

                elapsed_ms = (
                    perf_counter() - started_at
                ) * 1000

                g.sql_query_count = (
                    getattr(g, "sql_query_count", 0) + 1
                )

                g.sql_total_ms = (
                    getattr(g, "sql_total_ms", 0.0)
                    + elapsed_ms
                )

                g.sql_slowest_ms = max(
                    getattr(g, "sql_slowest_ms", 0.0),
                    elapsed_ms,
                )

            engine._yard_sql_metrics_registered = True

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

        Evita consultas duplicadas del predio activo y no consulta PostgreSQL
        para recursos estáticos ni para el healthcheck.
        """
        g.request_started_at = perf_counter()

        g.sql_query_count = 0
        g.sql_total_ms = 0.0
        g.sql_slowest_ms = 0.0

        g.active_site_id = None
        g.active_site = None

        # No consultar PostgreSQL para archivos estáticos ni healthcheck.
        if request.endpoint == "static" or request.path == "/health":
            return None

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

        if active_site_id:
            from app.models.site import Site

            g.active_site = db.session.get(
                Site,
                active_site_id,
            )

        return None

    # =========================================================
    # Medición de rendimiento
    # =========================================================
    @app.after_request
    def log_slow_request(response):
        """
        Registra las peticiones lentas incluyendo métricas SQL.

        Permite distinguir entre:
        - tiempo consumido ejecutando SQL;
        - cantidad de consultas;
        - consulta más lenta;
        - tiempo restante fuera de PostgreSQL.
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

        sql_query_count = getattr(
            g,
            "sql_query_count",
            0,
        )

        sql_total_ms = getattr(
            g,
            "sql_total_ms",
            0.0,
        )

        sql_slowest_ms = getattr(
            g,
            "sql_slowest_ms",
            0.0,
        )

        non_sql_ms = max(
            elapsed_ms - sql_total_ms,
            0.0,
        )

        try:
            slow_request_ms = int(
                current_app.config.get(
                    "SLOW_REQUEST_MS",
                    1000,
                )
            )
        except (TypeError, ValueError):
            slow_request_ms = 1000

        # Información útil visible también desde DevTools del navegador.
        response.headers["Server-Timing"] = (
            f"app;dur={elapsed_ms:.2f}, "
            f"sql;dur={sql_total_ms:.2f}"
        )

        if elapsed_ms >= slow_request_ms:
            current_app.logger.warning(
                (
                    "SLOW_REQUEST "
                    "method=%s "
                    "path=%s "
                    "endpoint=%s "
                    "status=%s "
                    "duration_ms=%.2f "
                    "sql_queries=%s "
                    "sql_total_ms=%.2f "
                    "sql_slowest_ms=%.2f "
                    "non_sql_ms=%.2f"
                ),
                request.method,
                request.path,
                request.endpoint,
                response.status_code,
                elapsed_ms,
                sql_query_count,
                sql_total_ms,
                sql_slowest_ms,
                non_sql_ms,
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