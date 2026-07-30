# app/models/user.py
from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import selectinload
from app.extensions import db, login_manager

SCHEMA = "yard_gate_alamo"


class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}

    # -------------------------
    # Roles permitidos
    # -------------------------
    ROLE_ADMIN = "admin"
    ROLE_INSPECCION = "inspeccion"
    ROLE_PATIO = "patio"
    ROLE_SUPERVISION = "supervision"
    ROLE_CONTROL_EQUIPO = "control_equipo"
    ROLE_DESPACHADOR = "despachador"
    ROLE_OPERADOR = "operador"
    ROLE_TALLER = "taller"
    ROLE_TRACKING = "tracking"
    ROLE_SEGURIDAD = "seguridad"
    ROLE_TRAFICO = "trafico"

    # Compatibilidad temporal con usuarios existentes
    ROLE_PREDIO = "predio"

    ALLOWED_ROLES = {
        ROLE_ADMIN,
        ROLE_INSPECCION,
        ROLE_PATIO,
        ROLE_SUPERVISION,
        ROLE_CONTROL_EQUIPO,
        ROLE_DESPACHADOR,
        ROLE_OPERADOR,
        ROLE_TALLER,
        ROLE_TRACKING,
        ROLE_SEGURIDAD,
        ROLE_TRAFICO,
        ROLE_PREDIO,
    }

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    role = db.Column(
        db.String(20),
        nullable=False,
        default=ROLE_INSPECCION,
    )
    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

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
    def normalized_role(self) -> str:
        return (self.role or "").strip().lower()

    @property
    def is_admin(self) -> bool:
        return self.normalized_role == self.ROLE_ADMIN

    def has_role(self, *roles: str) -> bool:
        allowed = {
            (role or "").strip().lower()
            for role in roles
        }
        return self.normalized_role in allowed

    # -------------------------
    # Site access helpers
    # -------------------------
    @property
    def site_ids(self) -> list[int]:
        """
        IDs de predios asignados al usuario desde user_sites.

        Admin devuelve lista vacía a propósito:
        significa "todos" dentro de can_access_site().
        """
        if self.is_admin:
            return []

        user_sites = getattr(self, "user_sites", None) or []

        return [
            user_site.site_id
            for user_site in user_sites
        ]


    @property
    def has_multiple_sites(self) -> bool:
        """
        Sirve para mostrar 'Cambiar predio'
        solo cuando el usuario tiene varios predios habilitados.
        """
        if self.is_admin:
            return True

        user_sites = getattr(self, "user_sites", None) or []

        return len(user_sites) > 1


    def can_access_site(self, site_id: int | None) -> bool:
        """
        Admin:
            Puede acceder a todos los predios.

        No admin:
            Solo puede acceder a los predios asignados en user_sites.
        """
        if self.is_admin:
            return True

        if not site_id:
            return False

        requested_site_id = int(site_id)
        user_sites = getattr(self, "user_sites", None) or []

        return any(
            user_site.site_id == requested_site_id
            for user_site in user_sites
        )


@login_manager.user_loader
def load_user(user_id: str):
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return None

    return (
        User.query
        .options(
            selectinload(User.user_sites)
        )
        .filter(User.id == user_id_int)
        .first()
    )