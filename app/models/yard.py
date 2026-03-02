# app/models/yard.py
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class YardBlock(db.Model):
    __tablename__ = "yard_blocks"
    __table_args__ = (
        # En BD: UNIQUE (site_id, code)
        db.UniqueConstraint("site_id", "code", name="uq_yard_blocks_site_code"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(1), nullable=False)  # A,B,C,D (se repite por predio)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False
    )

    # Mantiene tu relación existente, pero ahora con sitio
    site = db.relationship("Site")


class YardBay(db.Model):
    __tablename__ = "yard_bays"
    __table_args__ = (
        # En BD: UNIQUE (site_id, code)
        db.UniqueConstraint("site_id", "code", name="uq_yard_bays_site_code"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    block_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.yard_blocks.id"),
        nullable=False
    )

    bay_number = db.Column(db.Integer, nullable=False)  # 1..15
    code = db.Column(db.String(3), nullable=False)      # A01..D15 (se repite por predio)

    max_depth_rows = db.Column(db.Integer, nullable=False, default=20)
    max_tiers = db.Column(db.Integer, nullable=False, default=4)

    x = db.Column(db.Integer, nullable=False, default=0)
    y = db.Column(db.Integer, nullable=False, default=0)
    w = db.Column(db.Integer, nullable=False, default=50)
    h = db.Column(db.Integer, nullable=False, default=50)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False
    )

    # Conservamos relaciones
    block = db.relationship("YardBlock")
    site = db.relationship("Site")
