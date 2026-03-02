# app/models/user.py
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db, login_manager

SCHEMA = "yard_gate_alamo"


class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    # Roles permitidos (por ahora)
    ROLE_ADMIN = "admin"
    ROLE_PREDIO = "predio"
    ROLE_SUPERVISION = "supervision"
    ROLE_INSPECCION = "inspeccion"

    ALLOWED_ROLES = {ROLE_ADMIN, ROLE_PREDIO, ROLE_SUPERVISION, ROLE_INSPECCION}

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(db.String(20), nullable=False, default=ROLE_PREDIO)  # admin | predio | supervision | inspeccion
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # -------------------------
    # Auth helpers
    # -------------------------
    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

    # -------------------------
    # Role helpers
    # -------------------------
    @property
    def is_admin(self) -> bool:
        return (self.role or "").lower() == self.ROLE_ADMIN

    def has_role(self, *roles: str) -> bool:
        r = (self.role or "").lower()
        return r in {x.lower() for x in roles}

    # -------------------------
    # Site access helpers
    # -------------------------
    @property
    def site_ids(self) -> list[int]:
        """
        IDs de predios asignados al usuario (desde user_sites).
        Admin devuelve lista vacía a propósito: significa "todos" en can_access_site().
        """
        if self.is_admin:
            return []
        # user_sites viene por backref definido en UserSite.user relationship
        return [us.site_id for us in (getattr(self, "user_sites", None) or [])]

    def can_access_site(self, site_id: int | None) -> bool:
        """
        - Admin: siempre True
        - No admin: True si el site_id está asignado en user_sites
        """
        if self.is_admin:
            return True
        if not site_id:
            return False
        return int(site_id) in set(self.site_ids)


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))