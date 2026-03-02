# app/models/container.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Container(db.Model):
    __tablename__ = "containers"
    __table_args__ = (
        db.UniqueConstraint("site_id", "code", name="uq_containers_site_code"),
        db.Index("ix_containers_site_code", "site_id", "code"),
        db.Index("ix_containers_site_in_yard", "site_id", "is_in_yard"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    # 🔹 Multi-predio
    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    # Formato: AAAA-000000-0 (13 chars incluyendo guiones)
    # ⚠️ NO unique global: debe ser único por site (ver UniqueConstraint)
    code = db.Column(db.String(13), nullable=False)

    # 20ST, 40ST, 40HC, 45ST
    size = db.Column(db.String(10), nullable=False)

    year = db.Column(db.Integer, nullable=True)
    status_notes = db.Column(db.Text, nullable=True)

    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relación 1:1 con posición actual (si está en patio)
    position = db.relationship(
        "ContainerPosition",
        back_populates="container",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )

    # Historial de movimientos (Gate In/Out/Moves)
    movements = db.relationship(
        "Movement",
        back_populates="container",
        lazy=True,
    )

    # Relación con Site
    site = db.relationship(
        "Site",
        backref=db.backref("containers", lazy=True),
        lazy=True,
    )


class ContainerPosition(db.Model):
    __tablename__ = "container_positions"
    __table_args__ = (
        db.Index("ix_container_positions_bay", "bay_id"),
        {"schema": SCHEMA},
    )

    # En tu SQL: container_id es PK (1:1)
    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id", ondelete="CASCADE"),
        primary_key=True,
    )

    bay_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.yard_bays.id"),
        nullable=False,
    )

    depth_row = db.Column(db.Integer, nullable=False)  # 1..20
    tier = db.Column(db.Integer, nullable=False)       # 1..4

    placed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    placed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    container = db.relationship("Container", back_populates="position")
    bay = db.relationship("YardBay", lazy=True)