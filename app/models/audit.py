from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True
    )

    action = db.Column(db.String(50), nullable=False)
    entity_type = db.Column(db.String(50), nullable=True)
    entity_id = db.Column(db.Integer, nullable=True)

    meta = db.Column(db.JSON, nullable=True)

    user = db.relationship(
        "User",
        backref=db.backref("audit_logs", lazy=True),
        lazy=True
    )
