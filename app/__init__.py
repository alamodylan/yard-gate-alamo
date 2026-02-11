from flask import Flask, current_app
from dotenv import load_dotenv

from app.config import Config
from app.extensions import db, migrate, login_manager

def create_app():
    load_dotenv()

    app = Flask(__name__)
    app.config.from_object(Config)

    # Extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

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
