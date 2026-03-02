# app/models/print_job.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class PrintJob(db.Model):
    __tablename__ = "print_jobs"
    __table_args__ = (
        db.Index("ix_print_jobs_status_created", "status", "created_at"),
        db.Index("ix_print_jobs_site_status_created", "site_id", "status", "created_at"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    status = db.Column(db.String(16), nullable=False, default="PENDING")  # PENDING|CLAIMED|DONE|FAILED

    # 🔹 Multi-predio (para no mezclar colas entre predios)
    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    ticket_id = db.Column(db.Integer, nullable=True)

    payload_text = db.Column(db.Text, nullable=False)

    requested_by = db.Column(db.String(120), nullable=True)
    request_origin = db.Column(db.String(120), nullable=True)

    claimed_by = db.Column(db.String(120), nullable=True)
    claimed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    printed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    attempts = db.Column(db.Integer, nullable=False, default=0)
    last_error = db.Column(db.Text, nullable=True)

    # Relación opcional (no rompe nada)
    site = db.relationship("Site", lazy=True)