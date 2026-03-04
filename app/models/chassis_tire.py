from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class ChassisTire(db.Model):
    __tablename__ = "chassis_tires"
    __table_args__ = (
        db.UniqueConstraint("chassis_id", "position_code", name="uq_chassis_tires_position"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    chassis_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.chassis.id"), nullable=False, index=True)
    position_code = db.Column(db.String(12), nullable=False)

    tire_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.tires.id"), nullable=True)
    marchamo = db.Column(db.String(30), nullable=True)

    tire_state = db.Column(db.String(20), nullable=False, default="OK")  # OK/GASTADA/PINCHADA/CAMBIAR/NO_APTA

    installed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    chassis = db.relationship("Chassis", lazy=True)
    tire = db.relationship("Tire", lazy=True)

    def __repr__(self) -> str:
        return f"<ChassisTire chassis={self.chassis_id} pos={self.position_code} state={self.tire_state}>"