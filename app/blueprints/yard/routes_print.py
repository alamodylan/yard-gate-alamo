import os
from datetime import datetime

import requests
from flask import render_template, jsonify, abort
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.container import Container
from app.models.movement import Movement
from app.models.ticket import TicketPrint
from app.services.audit import audit_log
from app.services.ticketing import build_ticket_payload

from .routes import _ensure_active_site, APP_NAME


def _register_ticket_print(site_id: int, movement_id: int, printed_by_user_id: int, payload: str) -> TicketPrint:
    row = TicketPrint(
        site_id=site_id,
        movement_id=movement_id,
        printed_by_user_id=printed_by_user_id,
        ticket_payload=payload,
        printed_at=datetime.utcnow(),
    )
    db.session.add(row)
    return row


# =========================
# Tickets / impresión
# =========================
@yard_bp.post("/print/<int:movement_id>")
@login_required
def print_ticket(movement_id: int):
    site_id = _ensure_active_site()

    mv = Movement.query.get_or_404(movement_id)
    if mv.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    c = Container.query.get_or_404(mv.container_id)

    payload = build_ticket_payload("Yard Gate Álamo", mv, c)

    agent_url = os.environ.get("PRINT_AGENT_URL")  # ej: http://10.0.0.50:9109/print
    if not agent_url:
        return jsonify({"error": "PRINT_AGENT_URL no configurado"}), 500

    r = requests.post(
        agent_url,
        json={"printer": "EPSON_M188D", "payload": payload},
        timeout=10,
    )

    if r.status_code != 200:
        return jsonify({"error": "Print agent error", "detail": r.text}), 502

    _register_ticket_print(site_id, mv.id, current_user.id, payload)
    audit_log(current_user.id, "TICKET_PRINTED_AGENT", "movement", mv.id, {"container": c.code, "site_id": site_id})
    db.session.commit()

    return jsonify({"ok": True})


@yard_bp.get("/ticket/<int:movement_id>")
@login_required
def ticket_view(movement_id: int):
    site_id = _ensure_active_site()

    mv = Movement.query.get_or_404(movement_id)
    if mv.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    c = Container.query.get_or_404(mv.container_id)

    payload = build_ticket_payload(APP_NAME, mv, c)
    _register_ticket_print(site_id, mv.id, current_user.id, payload)
    audit_log(current_user.id, "TICKET_PRINTED", "movement", mv.id, {"container": c.code, "site_id": site_id})
    db.session.commit()

    return render_template("yard/ticket.html", mv=mv, c=c, payload=payload)


@yard_bp.get("/ticket/reprint/<int:print_id>")
@login_required
def ticket_reprint(print_id: int):
    site_id = _ensure_active_site()

    tp = TicketPrint.query.get_or_404(print_id)
    if tp.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    mv = Movement.query.get_or_404(tp.movement_id)
    c = Container.query.get_or_404(mv.container_id)

    audit_log(
        current_user.id,
        "TICKET_REPRINTED",
        "ticket_print",
        tp.id,
        {"movement_id": mv.id, "container": c.code, "site_id": site_id},
    )
    db.session.commit()

    return render_template("yard/ticket.html", mv=mv, c=c, payload=tp.ticket_payload, is_reprint=True)