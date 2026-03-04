# app/models/chassis_master.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Chassis(db.Model):
    __tablename__ = "chassis"
    __table_args__ = (
        db.UniqueConstraint("site_id", "chassis_number", name="uq_chassis_site_number"),
        db.Index("idx_chassis_site_number", "site_id", "chassis_number"),
        db.Index("idx_chassis_site_inyard", "site_id", "is_in_yard"),
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
    type_code = db.Column(db.String(10), nullable=True)

    plate = db.Column(db.String(20), nullable=True)
    has_plate = db.Column(db.Boolean, nullable=False, default=True)

    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    site = db.relationship("Site", lazy=True)

    def __repr__(self) -> str:
        return f"<Chassis {self.chassis_number} site_id={self.site_id} in_yard={self.is_in_yard}>"