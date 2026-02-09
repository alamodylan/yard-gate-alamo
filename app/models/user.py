from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login_manager

SCHEMA = "yard_gate_alamo"

class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False, default="predio")  # admin | predio
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))