from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Tire(db.Model):
    __tablename__ = "tires"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    tire_number = db.Column(db.String(30), nullable=False, unique=True)
    brand = db.Column(db.String(40), nullable=True)
    model = db.Column(db.String(40), nullable=True)
    size = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    status = db.Column(db.String(30), nullable=False, default="EN_TALLER_BODEGA")

    last_marchamo = db.Column(db.String(30), nullable=True)
    last_estrias_mm = db.Column(db.Integer, nullable=True)
    last_is_flat = db.Column(db.Boolean, nullable=False, default=False)
    last_tire_state = db.Column(db.String(20), nullable=True)

    def __repr__(self) -> str:
        return f"<Tire {self.tire_number}>"
    
class TirePosition(db.Model):
    __tablename__ = "tire_positions"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    axle_count = db.Column(db.SmallInteger, nullable=False)
    position_code = db.Column(db.String(30), nullable=False)
    label = db.Column(db.String(80), nullable=True)


class TireReading(db.Model):
    __tablename__ = "tire_readings"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    site_id = db.Column(db.Integer, nullable=False)
    chassis_id = db.Column(db.Integer, nullable=False)
    event_type = db.Column(db.String(30), nullable=False)
    event_id = db.Column(db.Integer, nullable=True)
    tire_position_id = db.Column(db.Integer, nullable=False)
    seal_1 = db.Column(db.String(60), nullable=True)
    seal_2 = db.Column(db.String(60), nullable=True)
    pressure_psi = db.Column(db.Numeric(6, 2), nullable=True)
    condition = db.Column(db.String(30), nullable=True)
    comments = db.Column(db.Text, nullable=True)
    recorded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    recorded_by_user_id = db.Column(db.Integer, nullable=True)