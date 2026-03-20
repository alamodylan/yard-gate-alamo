from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class TireRetreadEvent(db.Model):
    __tablename__ = "tire_retread_events"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    tire_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.tires.id"), nullable=False, index=True)

    previous_estrias_mm = db.Column(db.Integer, nullable=True)
    new_estrias_mm = db.Column(db.Integer, nullable=True)

    previous_marchamo = db.Column(db.String(50), nullable=True)
    new_marchamo = db.Column(db.String(50), nullable=True)

    created_by = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.users.id"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    sent_at = db.Column(db.DateTime(timezone=True), nullable=True)
    returned_at = db.Column(db.DateTime(timezone=True), nullable=True)

    event_status = db.Column(db.String(20), nullable=True)

    sent_by = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.users.id"), nullable=True)
    returned_by = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.users.id"), nullable=True)

    notes = db.Column(db.Text, nullable=True)

    tire = db.relationship("Tire", lazy=True)

    def __repr__(self) -> str:
        return (
            f"<TireRetreadEvent tire_id={self.tire_id} "
            f"status={self.event_status} "
            f"old_mm={self.previous_estrias_mm} "
            f"new_mm={self.new_estrias_mm}>"
        )