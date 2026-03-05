# app/models/chassis.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Chassis(db.Model):
    __tablename__ = "chassis"
    __table_args__ = (
        db.UniqueConstraint("site_id", "chassis_number", name="uq_chassis_site_number"),
        db.Index("idx_chassis_site_number", "site_id", "chassis_number"),
        db.Index("idx_chassis_site_inyard", "site_id", "site_id", "is_in_yard"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    chassis_number = db.Column(db.String(30), nullable=False)

    # Ej: 40FT_2AX, 40FT_3AX, 20FT_2AX, 20FT_3AX, UNKNOWN
    type_code = db.Column(db.String(10), nullable=True)

    # Placa (si aplica)
    plate = db.Column(db.String(20), nullable=True)

    # ✅ NUEVAS columnas (soportan import masivo y dashboard)
    length_ft = db.Column(db.Integer, nullable=True)  # 20 / 40 / 45
    axles = db.Column(db.Integer, nullable=True)      # 2 / 3

    # Flags
    has_plate = db.Column(db.Boolean, nullable=False, default=False)
    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)

    status = db.Column(db.String(30), nullable=False, default="BUENO")
    chassis_kind = db.Column(db.String(30), nullable=False, default="CHASIS")

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    site = db.relationship("Site", lazy=True)

    def __repr__(self) -> str:
        return f"<Chassis {self.chassis_number} site={self.site_id} in_yard={self.is_in_yard}>"


class ChassisInventory(db.Model):
    __tablename__ = "chassis_inventory"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    chassis_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.chassis.id"),
        nullable=False,
        index=True,
    )

    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)
    status_notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    chassis = db.relationship("Chassis", lazy=True)
    site = db.relationship("Site", lazy=True)

    def __repr__(self) -> str:
        return f"<ChassisInventory chassis_id={self.chassis_id} site={self.site_id}>"