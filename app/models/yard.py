from app.extensions import db

SCHEMA = "yard_gate_alamo"


class YardBlock(db.Model):
    __tablename__ = "yard_blocks"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(1), unique=True, nullable=False)  # A,B,C,D


class YardBay(db.Model):
    __tablename__ = "yard_bays"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    block_id = db.Column(db.Integer, db.ForeignKey(f"{SCHEMA}.yard_blocks.id"), nullable=False)
    bay_number = db.Column(db.Integer, nullable=False)  # 1..15
    code = db.Column(db.String(3), unique=True, nullable=False)  # A01..D15

    max_depth_rows = db.Column(db.Integer, nullable=False, default=20)
    max_tiers = db.Column(db.Integer, nullable=False, default=4)

    x = db.Column(db.Integer, nullable=False, default=0)
    y = db.Column(db.Integer, nullable=False, default=0)
    w = db.Column(db.Integer, nullable=False, default=50)
    h = db.Column(db.Integer, nullable=False, default=50)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    block = db.relationship("YardBlock")
