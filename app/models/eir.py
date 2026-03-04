# app/models/eir.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class EIR(db.Model):
    __tablename__ = "eirs"
    __table_args__ = (
        db.Index("idx_eirs_site_date", "site_id", db.text("trip_date DESC")),
        db.Index("idx_eirs_gate_out_movement", "gate_out_movement_id"),
        db.Index("ix_eirs_site_id", "site_id"),
        db.Index("ix_eirs_status", "status"),
        db.Index("ix_eirs_container_id", "container_id"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.sites.id"), nullable=False)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.users.id"), nullable=False)

    terminal_name = db.Column(db.String(40), nullable=False)
    trip_date = db.Column(db.Date, nullable=False)

    carrier = db.Column(db.String(40), nullable=False, default="ATM")
    origin = db.Column(db.String(40), nullable=False)
    destination = db.Column(db.String(120), nullable=False)

    has_chassis = db.Column(db.Boolean, nullable=False, default=True)
    chassis_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.chassis.id"), nullable=True)

    has_container = db.Column(db.Boolean, nullable=False, default=False)
    container_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.containers.id"), nullable=True)

    is_reefer = db.Column(db.Boolean, nullable=False, default=False)
    has_genset = db.Column(db.Boolean, nullable=False, default=False)

    status = db.Column(db.String(20), nullable=False, default="DRAFT")  # DRAFT | FINAL | CANCELED

    gate_out_movement_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.movements.id"), nullable=True)

    site = db.relationship("Site", lazy=True)
    created_by = db.relationship("User", lazy=True)
    chassis = db.relationship("Chassis", lazy=True)
    container = db.relationship("Container", lazy=True)
    gate_out_movement = db.relationship("Movement", lazy=True)

    damages = db.relationship(
        "EIRContainerDamage",
        back_populates="eir",
        cascade="all, delete-orphan",
        lazy=True,
    )


class EIRContainerDamage(db.Model):
    __tablename__ = "eir_container_damages"
    __table_args__ = (
        db.Index("idx_eir_damages_eir", "eir_id"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    eir_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.eirs.id", ondelete="CASCADE"), nullable=False)

    side = db.Column(db.String(20), nullable=False)         # LEFT/RIGHT/FRONT/REAR/ROOF
    damage_type = db.Column(db.String(30), nullable=False)  # ABOLLADURA, RAYADO, etc.

    x = db.Column(db.Numeric(6, 3), nullable=False)  # 0..1
    y = db.Column(db.Numeric(6, 3), nullable=False)  # 0..1

    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.users.id"), nullable=False)

    eir = db.relationship("EIR", back_populates="damages", lazy=True)
    created_by = db.relationship("User", lazy=True)