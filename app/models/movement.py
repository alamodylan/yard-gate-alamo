# app/models/movement.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Movement(db.Model):
    __tablename__ = "movements"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id"),
        nullable=False
    )

    movement_type = db.Column(db.String(20), nullable=False)  # GATE_IN | GATE_OUT | MOVE

    occurred_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # Ubicación registrada en ese evento (histórico)
    bay_code = db.Column(db.String(3), nullable=True)  # A01
    depth_row = db.Column(db.Integer, nullable=True)
    tier = db.Column(db.Integer, nullable=True)

    # Chofer
    driver_name = db.Column(db.String(150), nullable=True)
    driver_id_doc = db.Column(db.String(50), nullable=True)
    truck_plate = db.Column(db.String(20), nullable=True)

    notes = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    photos = db.relationship(
        "MovementPhoto",
        backref="movement",
        cascade="all, delete-orphan",
        lazy=True
    )


class MovementPhoto(db.Model):
    __tablename__ = "movement_photos"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    movement_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.movements.id"),
        nullable=False
    )

    photo_type = db.Column(db.String(30), nullable=False)  # CONTAINER, DAMAGE, DRIVER_ID, OTHER
    url = db.Column(db.Text, nullable=False)  # URL pública (R2) o path local
    uploaded_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)