from flask import Flask
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

    # Blueprints
    from app.blueprints.auth import auth_bp
    from app.blueprints.yard import yard_bp
    from app.blueprints.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(yard_bp)
    app.register_blueprint(admin_bp)

    # Simple healthcheck
    @app.get("/health")
    def health():
        return {"ok": True}

    return app