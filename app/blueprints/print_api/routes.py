# app/blueprints/print_api/routes.py

import threading
import time
from datetime import datetime, timezone, timedelta

from flask import Blueprint, request, jsonify, current_app
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.extensions import db
from app.models.print_job import PrintJob


bp = Blueprint(
    "print_api",
    __name__,
    url_prefix="/api/print",
)


# =========================================================
# Control local del barrido de trabajos vencidos
# =========================================================
#
# Evita ejecutar UPDATE + COMMIT en cada consulta del agente.
#
# Cada proceso Gunicorn mantiene su propio control. Esto es correcto:
# aunque dos workers ejecuten ocasionalmente el barrido, será mucho
# menos costoso que hacerlo en cada solicitud.
#
_stale_sweep_lock = threading.Lock()
_last_stale_sweep_monotonic = 0.0


# =========================================================
# Seguridad del agente
# =========================================================

def _require_agent_key():
    key = request.headers.get("X-PRINT-KEY", "")
    expected = current_app.config.get("PRINT_AGENT_KEY", "")

    return bool(expected) and key == expected

def _print_queue_enabled() -> bool:
    """
    Indica si la cola de impresión está habilitada.

    Cuando está deshabilitada:
    - /api/print/pending responde sin consultar PostgreSQL.
    - no se reclaman trabajos.
    - no se ejecuta el barrido de trabajos vencidos.
    - /api/print/jobs no crea nuevos registros.
    """
    value = current_app.config.get(
        "PRINT_QUEUE_ENABLED",
        False,
    )

    if isinstance(value, bool):
        return value

    return (
        str(value)
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )


# =========================================================
# Barrido controlado de trabajos CLAIMED vencidos
# =========================================================

def _should_run_stale_sweep() -> bool:
    """
    Decide si corresponde revisar trabajos CLAIMED vencidos.

    El intervalo se controla con:

        PRINT_JOB_STALE_SWEEP_SECONDS

    Por defecto se revisa una vez cada 60 segundos por proceso
    Gunicorn, en lugar de hacerlo en cada solicitud del agente.
    """
    global _last_stale_sweep_monotonic

    try:
        sweep_seconds = int(
            current_app.config.get(
                "PRINT_JOB_STALE_SWEEP_SECONDS",
                60,
            )
        )
    except (TypeError, ValueError):
        sweep_seconds = 60

    # Evita configuraciones excesivamente agresivas.
    sweep_seconds = max(sweep_seconds, 15)

    now_monotonic = time.monotonic()

    if (
        now_monotonic - _last_stale_sweep_monotonic
        < sweep_seconds
    ):
        return False

    with _stale_sweep_lock:
        now_monotonic = time.monotonic()

        if (
            now_monotonic - _last_stale_sweep_monotonic
            < sweep_seconds
        ):
            return False

        # Se marca antes de ejecutar para impedir que varios threads
        # entren simultáneamente al mismo barrido.
        _last_stale_sweep_monotonic = now_monotonic

        return True


def _requeue_stale_claimed_jobs(now: datetime) -> int:
    """
    Devuelve a PENDING los trabajos CLAIMED que excedieron
    el tiempo permitido.

    Solo hace COMMIT cuando realmente encontró trabajos vencidos.
    """
    try:
        stale_minutes = int(
            current_app.config.get(
                "PRINT_JOB_STALE_MINUTES",
                5,
            )
        )
    except (TypeError, ValueError):
        stale_minutes = 5

    stale_minutes = max(stale_minutes, 1)
    stale_before = now - timedelta(minutes=stale_minutes)

    updated_count = (
        db.session.query(PrintJob)
        .filter(
            PrintJob.status == "CLAIMED",
            PrintJob.claimed_at.isnot(None),
            PrintJob.claimed_at < stale_before,
        )
        .update(
            {
                PrintJob.status: "PENDING",
                PrintJob.claimed_by: None,
                PrintJob.claimed_at: None,
            },
            synchronize_session=False,
        )
    )

    if updated_count > 0:
        db.session.commit()
    else:
        # El UPDATE abrió una transacción aunque no modificara filas.
        # El rollback la cierra sin generar un COMMIT innecesario.
        db.session.rollback()

    return updated_count


# =========================================================
# Crear trabajo de impresión
# =========================================================

@bp.post("/jobs")
def create_job():
    """
    Lo llama la aplicación web cuando solicita una impresión.

    Cuando PRINT_QUEUE_ENABLED=False:
    - no crea registros en print_jobs;
    - responde correctamente para no romper el flujo actual.
    """
    data = request.get_json(silent=True) or {}

    # Cola temporalmente deshabilitada.
    # No se inserta ningún trabajo en PostgreSQL.
    if not _print_queue_enabled():
        return jsonify({
            "ok": True,
            "job_id": None,
            "queue_enabled": False,
            "message": (
                "La cola de impresión está temporalmente "
                "deshabilitada."
            ),
        })

    payload_text = (
        data.get("payload_text") or ""
    ).strip()

    if not payload_text:
        return jsonify({
            "error": "payload_text requerido",
        }), 400

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

        return jsonify({
            "ok": True,
            "job_id": job.id,
            "queue_enabled": True,
        })

    except (ProgrammingError, OperationalError):
        db.session.rollback()

        return jsonify({
            "ok": False,
            "error": "print_queue_not_ready",
        }), 503


# =========================================================
# Reclamar siguiente trabajo pendiente
# =========================================================

@bp.get("/pending")
def claim_next_job():
    """
    Lo consulta el agente instalado en la PC del Gate.

    Cuando PRINT_QUEUE_ENABLED=False:
    - valida la clave del agente;
    - responde inmediatamente;
    - no consulta PostgreSQL;
    - no ejecuta el barrido de CLAIMED vencidos;
    - no abre transacciones.

    Cuando PRINT_QUEUE_ENABLED=True:
    - ejecuta el flujo normal de impresión.
    """
    if not _require_agent_key():
        return jsonify({
            "error": "unauthorized",
        }), 401

    # =====================================================
    # Cola temporalmente congelada
    # =====================================================
    if not _print_queue_enabled():
        return jsonify({
            "ok": True,
            "job": None,
            "queue_enabled": False,
        })

    device_id = (
        request.args.get("device_id") or "GATE-PC"
    ).strip()[:100]

    now = datetime.now(timezone.utc)

    try:
        # No se ejecuta en cada polling.
        if _should_run_stale_sweep():
            _requeue_stale_claimed_jobs(now)

        job = (
            db.session.query(PrintJob)
            .filter(PrintJob.status == "PENDING")
            .order_by(
                PrintJob.created_at.asc(),
                PrintJob.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .first()
        )

        if not job:
            # Libera inmediatamente la transacción de lectura.
            db.session.rollback()

            return jsonify({
                "ok": True,
                "job": None,
                "queue_enabled": True,
            })

        job.status = "CLAIMED"
        job.claimed_by = device_id
        job.claimed_at = now
        job.attempts = int(job.attempts or 0) + 1

        db.session.commit()

        return jsonify({
            "ok": True,
            "queue_enabled": True,
            "job": {
                "id": job.id,
                "payload_text": job.payload_text,
            },
        })

    except (ProgrammingError, OperationalError):
        db.session.rollback()

        # Mantiene el comportamiento actual:
        # si la cola no está disponible, el agente no tumba la app.
        return jsonify({
            "ok": True,
            "job": None,
            "queue_enabled": True,
        })


# =========================================================
# Confirmar resultado de impresión
# =========================================================

@bp.post("/jobs/<int:job_id>/done")
def mark_done(job_id: int):
    if not _require_agent_key():
        return jsonify({
            "error": "unauthorized",
        }), 401

    data = request.get_json(silent=True) or {}

    status = (
        data.get("status") or ""
    ).strip().upper()

    error_message = data.get("error")

    try:
        job = db.session.get(PrintJob, job_id)

        if not job:
            return jsonify({
                "error": "not_found",
            }), 404

        now = datetime.now(timezone.utc)

        if status == "DONE":
            job.status = "DONE"
            job.printed_at = now
            job.last_error = None

        else:
            job.status = "FAILED"
            job.last_error = (
                error_message or "Error desconocido"
            )[:4000]

        db.session.commit()

        return jsonify({
            "ok": True,
        })

    except (ProgrammingError, OperationalError):
        db.session.rollback()

        return jsonify({
            "ok": False,
            "error": "print_queue_not_ready",
        }), 503
