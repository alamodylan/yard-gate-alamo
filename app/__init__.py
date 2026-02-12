from flask import Flask, current_app
from dotenv import load_dotenv

from app.config import Config
from app.extensions import db, migrate, login_manager

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

    # Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.yard import yard_bp
    from app.blueprints.admin import admin_bp
    from app.blueprints.inventory import inventory_bp

    app.register_blueprint(inventory_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(yard_bp)
    app.register_blueprint(admin_bp)

    # Simple healthcheck
    @app.get("/health")
    def health():
        return {"ok": True}

    return app

