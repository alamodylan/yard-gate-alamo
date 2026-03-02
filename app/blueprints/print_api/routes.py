# app/blueprints/print_api/routes.py
from flask import Blueprint, request, jsonify, current_app
from datetime import datetime, timezone, timedelta

from sqlalchemy.exc import ProgrammingError, OperationalError  # ✅ NUEVO

from app.extensions import db
from app.models.print_job import PrintJob

bp = Blueprint("print_api", __name__, url_prefix="/api/print")


def _require_agent_key():
    key = request.headers.get("X-PRINT-KEY", "")
    expected = current_app.config.get("PRINT_AGENT_KEY", "")
    return bool(expected) and key == expected


@bp.post("/jobs")
def create_job():
    # Lo llama la web (Android) cuando toca "Imprimir"
    data = request.get_json(silent=True) or {}

    payload_text = (data.get("payload_text") or "").strip()
    if not payload_text:
        return jsonify({"error": "payload_text requerido"}), 400

    try:
        job = PrintJob(
            status="PENDING",
            ticket_id=data.get("ticket_id"),
            payload_text=payload_text,
            requested_by=data.get("requested_by"),
            request_origin=data.get("request_origin"),
        )
        db.session.add(job)
        db.session.commit()
        return jsonify({"ok": True, "job_id": job.id})

    except (ProgrammingError, OperationalError):
        # ✅ Si la tabla/schema aún no existe, no tumbamos la app
        db.session.rollback()
        return jsonify({"ok": False, "error": "print_queue_not_ready"}), 503


@bp.get("/pending")
def claim_next_job():
    # SOLO la PC del gate (agente)
    if not _require_agent_key():
        return jsonify({"error": "unauthorized"}), 401

    device_id = request.args.get("device_id", "GATE-PC")
    now = datetime.now(timezone.utc)

    try:
        # ============================
        # ✅ Cambio mínimo (robustez):
        # Re-enqueue de CLAIMED viejos (agente caído / consola cerrada / etc.)
        # ============================
        stale_minutes = int(current_app.config.get("PRINT_JOB_STALE_MINUTES", 5))
        stale_before = now - timedelta(minutes=stale_minutes)

        (
            db.session.query(PrintJob)
            .filter(PrintJob.status == "CLAIMED")
            .filter(PrintJob.claimed_at != None)  # noqa: E711
            .filter(PrintJob.claimed_at < stale_before)
            .update(
                {
                    PrintJob.status: "PENDING",
                    PrintJob.claimed_by: None,
                    PrintJob.claimed_at: None,
                },
                synchronize_session=False,
            )
        )
        db.session.commit()
        # ============================

        # Importante: PostgreSQL soporta FOR UPDATE SKIP LOCKED
        job = (
            db.session.query(PrintJob)
            .filter(PrintJob.status == "PENDING")
            .order_by(PrintJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

        if not job:
            return jsonify({"ok": True, "job": None})

        job.status = "CLAIMED"
        job.claimed_by = device_id
        job.claimed_at = now
        job.attempts = (job.attempts or 0) + 1
        db.session.commit()

        return jsonify(
            {
                "ok": True,
                "job": {
                    "id": job.id,
                    "payload_text": job.payload_text,
                },
            }
        )

    except (ProgrammingError, OperationalError):
        # ✅ Tabla/schema no existe o BD aún no está lista para la cola
        db.session.rollback()
        return jsonify({"ok": True, "job": None})


@bp.post("/jobs/<int:job_id>/done")
def mark_done(job_id):
    if not _require_agent_key():
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").upper()
    err = data.get("error")

    try:
        job = db.session.get(PrintJob, job_id)
        if not job:
            return jsonify({"error": "not_found"}), 404

        now = datetime.now(timezone.utc)

        if status == "DONE":
            job.status = "DONE"
            job.printed_at = now
            job.last_error = None
        else:
            job.status = "FAILED"
            job.last_error = (err or "Error desconocido")[:4000]

        db.session.commit()
        return jsonify({"ok": True})

    except (ProgrammingError, OperationalError):
        db.session.rollback()
        return jsonify({"ok": False, "error": "print_queue_not_ready"}), 503
