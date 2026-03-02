# app/models/ticket.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class TicketPrint(db.Model):
    __tablename__ = "ticket_prints"
    __table_args__ = (
        db.Index("ix_ticket_prints_site_printed_at", "site_id", "printed_at"),
        db.Index("ix_ticket_prints_movement", "movement_id"),
        db.Index("ix_ticket_prints_site_movement", "site_id", "movement_id"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    # 🔹 Multi-predio (copiado desde movement.site_id al crear el ticket)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    movement_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.movements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    printed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    printed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
        index=True,
    )

    # Snapshot del ticket impreso (JSON string / texto)
    ticket_payload = db.Column(db.Text, nullable=False)

    # Relaciones
    site = db.relationship(
        "Site",
        backref=db.backref("ticket_prints", lazy=True),
        lazy=True,
    )

    movement = db.relationship(
        "Movement",
        backref=db.backref("ticket_prints", lazy=True),
        lazy=True,
    )