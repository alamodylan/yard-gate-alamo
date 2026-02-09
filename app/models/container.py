# app/models/container.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Container(db.Model):
    __tablename__ = "containers"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    # Formato: AAAA-000000-0 (13 chars incluyendo guiones)
    code = db.Column(db.String(13), unique=True, nullable=False)

    # 20ST, 40ST, 40HC, 45ST
    size = db.Column(db.String(10), nullable=False)

    year = db.Column(db.Integer, nullable=True)
    status_notes = db.Column(db.Text, nullable=True)

    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relación 1:1 con posición actual (si está en patio)
    position = db.relationship(
        "ContainerPosition",
        back_populates="container",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True
    )

    # Historial de movimientos (Gate In/Out/Moves)
    movements = db.relationship(
        "Movement",
        backref="container",
        lazy=True
    )


class ContainerPosition(db.Model):
    __tablename__ = "container_positions"
    __table_args__ = {"schema": SCHEMA}

    # En tu SQL: container_id es PK (1:1)
    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id", ondelete="CASCADE"),
        primary_key=True
    )

    bay_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.yard_bays.id"),
        nullable=False
    )

    depth_row = db.Column(db.Integer, nullable=False)  # 1..20
    tier = db.Column(db.Integer, nullable=False)       # 1..4

    placed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    placed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True
    )

    container = db.relationship("Container", back_populates="position")
    bay = db.relationship("YardBay")