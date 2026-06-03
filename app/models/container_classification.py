# app/models/container_classification.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class ContainerClassification(db.Model):
    __tablename__ = "container_classifications"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id"),
        nullable=False,
        index=True,
    )

    classified_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    classified_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    shipping_line = db.Column(db.String(80), nullable=True)

    max_gross_kg = db.Column(db.Integer, nullable=True)
    tare_kg = db.Column(db.Integer, nullable=True)
    manufacture_year = db.Column(db.SmallInteger, nullable=True)

    needs_workshop = db.Column(db.Boolean, nullable=True)

    summary_text = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    container = db.relationship(
        "Container",
        lazy=True,
    )