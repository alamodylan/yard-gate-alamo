from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class TicketPrint(db.Model):
    __tablename__ = "ticket_prints"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    movement_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.movements.id"),
        nullable=False
    )

    printed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    printed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True
    )

    ticket_payload = db.Column(db.Text, nullable=False)

    movement = db.relationship(
        "Movement",
        backref=db.backref("ticket_prints", lazy=True),
        lazy=True
    )