# app/blueprints/yard/routes.py
import re
import os
import io
from datetime import datetime
from io import BytesIO

import pytz
import requests
from sqlalchemy import text  # ✅ para SQL directo (predios EIR/chasis)

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    jsonify,
    send_file,
    session,
    abort,
)
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.movement import Movement, MovementPhoto
from app.models.ticket import TicketPrint
from app.models.site import Site, UserSite
from app.services.audit import audit_log
from app.services.yard_logic import find_first_free_slot
from app.services.storage import get_storage, build_photo_key
from app.services.ticketing import build_ticket_payload
from app.models.eir import EIR
from app.models.chassis import ChassisInventory
from app.models.chassis import Chassis
from app.models.tire import Tire
from app.models.chassis_tire import ChassisTire

CR_TZ = pytz.timezone("America/Costa_Rica")
UTC_TZ = pytz.utc

CONTAINER_RE = re.compile(r"^[A-Z]{4}-\d{6}-\d$")
SIZES = ["20ST", "40ST", "40HC", "45ST"]
APP_NAME = "Yard Gate Álamo"

REPORT_TYPES = {"GATE_IN", "GATE_OUT", "MOVE"}

CHASSIS_NUM_RE = re.compile(r"^\d{5}$")
TIRE_STATES = {"OK", "GASTADA", "PINCHADA", "CAMBIAR", "NO_APTA"}

# ✅ Predios reales (ATM no es predio; MAERSK queda igual)
PREDIO_CODES = {"COYOL", "CALDERA", "LIMON"}


# =========================
# Multi-predio helpers (site)
# =========================
def _allowed_sites_for_user(user):
    if getattr(user, "role", None) == "admin":
        return Site.query.filter_by(is_active=True).order_by(Site.name.asc()).all()

    return (
        db.session.query(Site)
        .join(UserSite, UserSite.site_id == Site.id)
        .filter(UserSite.user_id == user.id, Site.is_active == True)  # noqa: E712
        .order_by(Site.name.asc())
        .all()
    )


def _get_active_site_id():
    return session.get("active_site_id")


def _set_active_site_id(site_id: int):
    session["active_site_id"] = int(site_id)


def _ensure_active_site():
    allowed = _allowed_sites_for_user(current_user)
    if not allowed:
        abort(403)

    active_id = _get_active_site_id()
    allowed_ids = {s.id for s in allowed}

    if not active_id or active_id not in allowed_ids:
        _set_active_site_id(allowed[0].id)
        active_id = allowed[0].id

    return active_id


# ==========================================================
# Predio activo (helper para templates dinámicos)
# ==========================================================
def _active_site():
    site_id = session.get("active_site_id")
    if not site_id:
        return None
    return Site.query.get(site_id)


def _active_site_key():
    site = _active_site()
    if not site:
        return ""
    value = getattr(site, "code", None) or getattr(site, "name", None) or ""
    return value.strip().upper()


@yard_bp.app_context_processor
def inject_active_site():
    return {"active_site_key": _active_site_key()}


def _is_predio_site(site_id: int) -> bool:
    """
    True si el predio activo es COYOL/CALDERA/LIMON.
    MAERSK y cualquier otro se queda con el flujo actual.
    """
    s = Site.query.get(site_id)
    return bool(s and (s.code or "").upper() in PREDIO_CODES)


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
# Dashboard Sites
# =========================
@yard_bp.get("/sites")
@login_required
def sites_dashboard():
    allowed = _allowed_sites_for_user(current_user)
    active_id = _get_active_site_id()
    return render_template("yard/sites.html", sites=allowed, active_site_id=active_id)


@yard_bp.post("/sites/select")
@login_required
def sites_select():
    site_id = request.form.get("site_id")
    if not site_id or not str(site_id).isdigit():
        flash("Predio inválido.", "danger")
        return redirect(url_for("yard.sites_dashboard"))

    site_id = int(site_id)
    allowed_ids = {s.id for s in _allowed_sites_for_user(current_user)}
    if site_id not in allowed_ids:
        flash("No tienes acceso a ese predio.", "danger")
        return redirect(url_for("yard.sites_dashboard"))

    _set_active_site_id(site_id)
    return redirect(url_for("yard.map_view"))


@yard_bp.get("/")
@login_required
def home():
    allowed = _allowed_sites_for_user(current_user)
    if len(allowed) == 1:
        _set_active_site_id(allowed[0].id)
        return redirect(url_for("yard.map_view"))
    return redirect(url_for("yard.sites_dashboard"))


@yard_bp.get("/map")
@login_required
def map_view():
    site_id = _ensure_active_site()

    blocks = (
        YardBlock.query
        .filter_by(site_id=site_id)
        .order_by(YardBlock.code.asc())
        .all()
    )

    selected_block = (request.args.get("block") or "A").upper()
    if selected_block not in {"A", "B", "C", "D"}:
        selected_block = "A"

    return render_template("yard/map.html", blocks=blocks, selected_block=selected_block)


@yard_bp.get("/bay/<string:bay_code>")
@login_required
def bay_detail_view(bay_code: str):
    site_id = _ensure_active_site()

    bay_code = bay_code.upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True, site_id=site_id).first_or_404()

    rows = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(ContainerPosition.bay_id == bay.id, Container.site_id == site_id)
        .order_by(ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
        .all()
    )

    items = []
    for c, p in rows:
        items.append(
            {
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "depth_row": p.depth_row,
                "tier": p.tier,
            }
        )

    return render_template("yard/bay_detail.html", bay=bay, items=items)


# =========================
# APIs mapa / bandeja
# =========================
@yard_bp.get("/api/yard/containers-in-yard")
@login_required
def api_containers_in_yard():
    """
    Para bandeja (mapa):
    Retorna contenedores en patio con su posición actual.
    """
    site_id = _ensure_active_site()

    rows = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(Container.is_in_yard == True, Container.site_id == site_id)  # noqa: E712
        .order_by(YardBay.code.asc(), ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
        .all()
    )

    payload = []
    for c, p, bay in rows:
        payload.append(
            {
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "year": c.year,
                "status_notes": c.status_notes,
                "position": {
                    "bay_code": bay.code,
                    "depth_row": p.depth_row,
                    "tier": p.tier,
                },
            }
        )

    return jsonify({"rows": payload})


@yard_bp.get("/api/yard/bays")
@login_required
def api_bays_by_block():
    site_id = _ensure_active_site()

    block_code = (request.args.get("block") or "").upper()
    block = YardBlock.query.filter_by(code=block_code, site_id=site_id).first()
    if not block:
        return jsonify({"bays": []})

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True, site_id=site_id)
        .order_by(YardBay.bay_number.asc())
        .all()
    )
    return jsonify({"bays": [{"id": b.id, "bay_number": b.bay_number, "code": b.code} for b in bays]})


@yard_bp.get("/api/yard/map")
@login_required
def api_yard_map():
    """
    Devuelve las estibas del bloque con conteo (used/capacity).
    Ideal: incluye x,y,w,h y límites para permitir layout visual real en frontend.
    """
    site_id = _ensure_active_site()

    block_code = (request.args.get("block") or "A").upper()
    block = YardBlock.query.filter_by(code=block_code, site_id=site_id).first()
    if not block:
        return jsonify({"error": "Bloque inválido"}), 400

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True, site_id=site_id)
        .order_by(YardBay.bay_number.asc())
        .all()
    )

    counts = dict(
        db.session.query(ContainerPosition.bay_id, db.func.count(ContainerPosition.container_id))
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(YardBay.site_id == site_id)
        .group_by(ContainerPosition.bay_id)
        .all()
    )

    payload = []
    for b in bays:
        capacity = b.max_depth_rows * b.max_tiers
        used = int(counts.get(b.id, 0))
        payload.append(
            {
                "id": b.id,
                "code": b.code,
                "bay_number": b.bay_number,
                "used": used,
                "capacity": capacity,
                "max_depth_rows": b.max_depth_rows,
                "max_tiers": b.max_tiers,
                "x": b.x,
                "y": b.y,
                "w": b.w,
                "h": b.h,
            }
        )

    return jsonify({"block": block_code, "bays": payload})


@yard_bp.get("/api/yard/block/<string:block_code>/availability")
@login_required
def api_block_availability(block_code: str):
    """
    Disponibilidad por estiba (verde/rojo) basada en capacidad total.
    """
    site_id = _ensure_active_site()

    block_code = (block_code or "").upper()
    block = YardBlock.query.filter_by(code=block_code, site_id=site_id).first()
    if not block:
        return jsonify({"error": "Bloque inválido"}), 400

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True, site_id=site_id)
        .order_by(YardBay.bay_number.asc())
        .all()
    )

    counts = dict(
        db.session.query(ContainerPosition.bay_id, db.func.count(ContainerPosition.container_id))
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(YardBay.site_id == site_id)
        .group_by(ContainerPosition.bay_id)
        .all()
    )

    payload = []
    for b in bays:
        capacity = b.max_depth_rows * b.max_tiers
        used = int(counts.get(b.id, 0))
        free = capacity - used
        payload.append(
            {
                "id": b.id,
                "code": b.code,
                "bay_number": b.bay_number,
                "used": used,
                "capacity": capacity,
                "free": free,
                "available": free > 0,
            }
        )

    return jsonify({"block": block_code, "bays": payload})


@yard_bp.get("/api/yard/bays/<string:bay_code>/last-available")
@login_required
def api_bay_last_available(bay_code: str):
    """
    Devuelve la sugerencia de slot según la regla REAL del sistema:
    - más adentro primero (depth_row más alto)
    - tier automático (1..max)
    """
    site_id = _ensure_active_site()

    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True, site_id=site_id).first()
    if not bay:
        return jsonify({"error": "Estiba inválida"}), 400

    slot = find_first_free_slot(bay.id)
    if not slot:
        return jsonify({"ok": False, "error": "BAY_FULL"}), 409

    depth_row, tier = slot
    return jsonify({"ok": True, "bay_code": bay.code, "depth_row": depth_row, "tier": tier})


@yard_bp.get("/api/yard/bays/<string:bay_code>/rows-availability")
@login_required
def api_bay_rows_availability(bay_code: str):
    """
    Disponibilidad por FILAS para una estiba.
    Ideal: incluye suggested_tier por fila para evitar request extra.
    """
    site_id = _ensure_active_site()

    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True, site_id=site_id).first()
    if not bay:
        return jsonify({"error": "Estiba inválida"}), 400

    max_levels = int(bay.max_tiers or 4)

    counts_by_row = dict(
        db.session.query(ContainerPosition.depth_row, db.func.count(ContainerPosition.container_id))
        .join(Container, Container.id == ContainerPosition.container_id)
        .filter(ContainerPosition.bay_id == bay.id, Container.site_id == site_id)
        .group_by(ContainerPosition.depth_row)
        .all()
    )

    rows = []
    for row_num in range(1, int(bay.max_depth_rows or 1) + 1):
        used = int(counts_by_row.get(row_num, 0))
        is_full = used >= max_levels

        suggested_tier = None
        if not is_full:
            occ_tiers = {
                int(t[0]) for t in db.session.query(ContainerPosition.tier)
                .join(Container, Container.id == ContainerPosition.container_id)
                .filter(
                    ContainerPosition.bay_id == bay.id,
                    ContainerPosition.depth_row == row_num,
                    Container.site_id == site_id
                )
                .all()
            }
            for t in range(1, max_levels + 1):
                if t not in occ_tiers:
                    suggested_tier = t
                    break

        rows.append(
            {
                "row": row_num,
                "levels_used": used,
                "max_levels": max_levels,
                "is_full": is_full,
                "suggested_tier": suggested_tier,
            }
        )

    return jsonify({"ok": True, "bay_code": bay.code, "rows": rows})


@yard_bp.get("/api/yard/bays/<string:bay_code>/row/<int:row_number>/suggest-tier")
@login_required
def api_bay_row_suggest_tier(bay_code: str, row_number: int):
    """
    Sugerir tier exacto dentro de una fila específica (1..max_tiers).
    Mantenerlo aunque rows-availability tenga suggested_tier, para “fuente de verdad”.
    """
    site_id = _ensure_active_site()

    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True, site_id=site_id).first()
    if not bay:
        return jsonify({"error": "Estiba inválida"}), 400

    if row_number < 1 or row_number > int(bay.max_depth_rows or 0):
        return jsonify({"ok": False, "error": "ROW_OUT_OF_RANGE"}), 400

    occupied = (
        db.session.query(ContainerPosition.tier)
        .join(Container, Container.id == ContainerPosition.container_id)
        .filter(
            ContainerPosition.bay_id == bay.id,
            ContainerPosition.depth_row == row_number,
            Container.site_id == site_id
        )
        .all()
    )
    occupied_tiers = {int(t[0]) for t in occupied}

    for tier in range(1, int(bay.max_tiers or 1) + 1):
        if tier not in occupied_tiers:
            return jsonify({"ok": True, "bay_code": bay.code, "depth_row": row_number, "tier": tier})

    return jsonify({"ok": False, "error": "ROW_FULL"}), 409


@yard_bp.post("/api/yard/place")
@login_required
def api_place_container():
    """
    Coloca un contenedor en una estiba.
    Compatibilidad:
      - Viejo: { "container_id": 123, "to_bay_code": "A07" } -> AUTO (find_first_free_slot)
      - Nuevo: { "container_id": 123, "to_bay_code": "A07", "to_depth_row": 10, "to_tier": 2 } -> EXACTO
    """
    site_id = _ensure_active_site()

    data = request.get_json(silent=True) or {}
    container_id = data.get("container_id")
    to_bay_code = (data.get("to_bay_code") or "").upper()

    if not container_id or not to_bay_code:
        return jsonify({"error": "Datos incompletos"}), 400

    c = Container.query.get(container_id)
    if not c or not c.is_in_yard or c.site_id != site_id:
        return jsonify({"error": "Contenedor no existe o no está en patio (predio actual)"}), 400

    to_bay = YardBay.query.filter_by(code=to_bay_code, is_active=True, site_id=site_id).first()
    if not to_bay:
        return jsonify({"error": "Estiba destino inválida"}), 400

    # Lock de estiba para evitar colisiones en concurrencia (sin unique constraint DB)
    db.session.query(YardBay).filter(YardBay.id == to_bay.id).with_for_update().one()

    to_depth_row = data.get("to_depth_row")
    to_tier = data.get("to_tier")

    if to_depth_row is not None and to_tier is not None:
        try:
            depth_row = int(to_depth_row)
            tier = int(to_tier)
        except Exception:
            return jsonify({"error": "Fila/Nivel inválidos"}), 400

        if not (1 <= depth_row <= to_bay.max_depth_rows) or not (1 <= tier <= to_bay.max_tiers):
            return jsonify({"error": "Fila/Nivel fuera de rango"}), 400

        occupied = ContainerPosition.query.filter_by(
            bay_id=to_bay.id,
            depth_row=depth_row,
            tier=tier
        ).first()
        if occupied:
            return jsonify({"error": "Slot ocupado"}), 409
    else:
        slot = find_first_free_slot(to_bay.id)
        if not slot:
            return jsonify({"error": "Estiba llena"}), 409
        depth_row, tier = slot

    old_pos = ContainerPosition.query.filter_by(container_id=c.id).first()
    old = None
    if old_pos:
        old_bay = YardBay.query.get(old_pos.bay_id)
        old = {
            "bay_code": old_bay.code if old_bay else None,
            "depth_row": old_pos.depth_row,
            "tier": old_pos.tier,
        }

    ContainerPosition.query.filter_by(container_id=c.id).delete()
    db.session.add(
        ContainerPosition(
            container_id=c.id,
            bay_id=to_bay.id,
            depth_row=depth_row,
            tier=tier,
            placed_by_user_id=current_user.id,
        )
    )

    mv = Movement(
        site_id=site_id,
        container_id=c.id,
        movement_type="MOVE",
        occurred_at=datetime.utcnow(),
        bay_code=to_bay.code,
        depth_row=depth_row,
        tier=tier,
        created_by_user_id=current_user.id,
        notes="PLACED_BY_BLOCK_UI",
    )
    db.session.add(mv)

    audit_log(
        current_user.id,
        "CONTAINER_PLACED",
        "container",
        c.id,
        {
            "from": old,
            "to": {"bay_code": to_bay.code, "depth_row": depth_row, "tier": tier},
            "rule": "AUTO_LAST_AVAILABLE" if (to_depth_row is None or to_tier is None) else "MANUAL_EXACT",
            "site_id": site_id,
        },
    )

    db.session.commit()
    return jsonify({"ok": True, "bay_code": to_bay.code, "depth_row": depth_row, "tier": tier})


@yard_bp.post("/api/yard/move")
@login_required
def api_move_container():
    """
    Drag & drop (detalle de estiba): mueve contenedor a otra estiba.
    Payload:
      { "container_id": 123, "to_bay_code": "B07", "mode": "auto" }
      o manual:
      { "container_id": 123, "to_bay_code": "B07", "mode": "manual", "depth_row": 1, "tier": 2 }
    """
    site_id = _ensure_active_site()

    data = request.get_json(silent=True) or {}
    container_id = data.get("container_id")
    to_bay_code = (data.get("to_bay_code") or "").upper()
    mode = (data.get("mode") or "auto").lower()

    if not container_id or not to_bay_code:
        return jsonify({"error": "Datos incompletos"}), 400

    c = Container.query.get(container_id)
    if not c or not c.is_in_yard or c.site_id != site_id:
        return jsonify({"error": "Contenedor no existe o no está en patio (predio actual)"}), 400

    to_bay = YardBay.query.filter_by(code=to_bay_code, is_active=True, site_id=site_id).first()
    if not to_bay:
        return jsonify({"error": "Estiba destino inválida"}), 400

    # Lock de estiba destino
    db.session.query(YardBay).filter(YardBay.id == to_bay.id).with_for_update().one()

    if mode == "manual":
        try:
            depth_row = int(data.get("depth_row"))
            tier = int(data.get("tier"))
        except Exception:
            return jsonify({"error": "Fila/Nivel inválidos"}), 400

        if not (1 <= depth_row <= to_bay.max_depth_rows) or not (1 <= tier <= to_bay.max_tiers):
            return jsonify({"error": "Fila/Nivel fuera de rango"}), 400

        occupied = ContainerPosition.query.filter_by(bay_id=to_bay.id, depth_row=depth_row, tier=tier).first()
        if occupied:
            return jsonify({"error": "Slot ocupado"}), 409
    else:
        slot = find_first_free_slot(to_bay.id)
        if not slot:
            return jsonify({"error": "Estiba llena"}), 409
        depth_row, tier = slot

    old_pos = ContainerPosition.query.filter_by(container_id=c.id).first()
    old = None
    if old_pos:
        old_bay = YardBay.query.get(old_pos.bay_id)
        old = {
            "bay_code": old_bay.code if old_bay else None,
            "depth_row": old_pos.depth_row,
            "tier": old_pos.tier,
        }

    ContainerPosition.query.filter_by(container_id=c.id).delete()
    db.session.add(
        ContainerPosition(
            container_id=c.id,
            bay_id=to_bay.id,
            depth_row=depth_row,
            tier=tier,
            placed_by_user_id=current_user.id,
        )
    )

    mv = Movement(
        site_id=site_id,
        container_id=c.id,
        movement_type="MOVE",
        occurred_at=datetime.utcnow(),
        bay_code=to_bay.code,
        depth_row=depth_row,
        tier=tier,
        created_by_user_id=current_user.id,
        notes=None,
    )
    db.session.add(mv)

    audit_log(
        current_user.id,
        "CONTAINER_MOVED",
        "container",
        c.id,
        {"from": old, "to": {"bay_code": to_bay.code, "depth_row": depth_row, "tier": tier}, "site_id": site_id},
    )

    db.session.commit()
    return jsonify({"ok": True, "bay_code": to_bay.code, "depth_row": depth_row, "tier": tier})


@yard_bp.get("/api/yard/bays/<string:bay_code>/row/<int:row_number>/containers")
@login_required
def api_bay_row_containers(bay_code: str, row_number: int):
    site_id = _ensure_active_site()

    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True, site_id=site_id).first()
    if not bay:
        return jsonify({"ok": False, "error": "BAY_NOT_FOUND"}), 404

    if row_number < 1 or row_number > int(bay.max_depth_rows or 0):
        return jsonify({"ok": False, "error": "ROW_OUT_OF_RANGE"}), 400

    rows = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay.id,
            ContainerPosition.depth_row == row_number,
            Container.site_id == site_id
        )
        .order_by(ContainerPosition.tier.asc())
        .all()
    )

    items = []
    for c, p in rows:
        items.append({
            "id": c.id,
            "code": c.code,
            "size": c.size,
            "tier": p.tier
        })

    return jsonify({"ok": True, "bay_code": bay.code, "depth_row": row_number, "containers": items})


# =========================
# Gate In / Gate Out
# =========================
@yard_bp.get("/gate-in")
@login_required
def gate_in_view():
    site_id = _ensure_active_site()
    blocks = YardBlock.query.filter_by(site_id=site_id).order_by(YardBlock.code.asc()).all()
    return render_template("yard/gate_in.html", blocks=blocks, sizes=SIZES)


@yard_bp.post("/gate-in")
@login_required
def gate_in_post():
    site_id = _ensure_active_site()

    code = (request.form.get("container_code") or "").strip().upper()
    size = (request.form.get("size") or "").strip()
    year_raw = (request.form.get("year") or "").strip()
    status_notes = (request.form.get("status_notes") or "").strip()

    driver_name = (request.form.get("driver_name") or "").strip()
    driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
    truck_plate = (request.form.get("truck_plate") or "").strip()

    block_code = (request.form.get("block") or "").strip().upper()
    bay_number_raw = (request.form.get("bay_number") or "").strip()

    placement_mode = (request.form.get("placement_mode") or "auto").strip().lower()
    depth_row_raw = (request.form.get("depth_row") or "").strip()
    tier_raw = (request.form.get("tier") or "").strip()

    if not CONTAINER_RE.match(code):
        flash("Formato de contenedor inválido. Debe ser AAAA-000000-0.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    if size not in SIZES:
        flash("Tamaño inválido.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    year = None
    if year_raw:
        try:
            year = int(year_raw)
            if year < 1950 or year > (datetime.utcnow().year + 1):
                raise ValueError()
        except ValueError:
            flash("Año inválido.", "danger")
            return redirect(url_for("yard.gate_in_view"))

    if block_code not in {"A", "B", "C", "D"}:
        flash("Bloque inválido.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    try:
        bay_number = int(bay_number_raw)
        if not (1 <= bay_number <= 15):
            raise ValueError()
    except ValueError:
        flash("Estiba inválida (1..15).", "danger")
        return redirect(url_for("yard.gate_in_view"))

    block = YardBlock.query.filter_by(code=block_code, site_id=site_id).first()
    bay = (
        YardBay.query.filter_by(block_id=block.id, bay_number=bay_number, is_active=True, site_id=site_id).first()
        if block else None
    )
    if not bay:
        flash("Estiba no encontrada.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    # Lock de estiba durante asignación
    db.session.query(YardBay).filter(YardBay.id == bay.id).with_for_update().one()

    # ==========================================================
    # ✅ CAMBIO QUIRÚRGICO multi-predio:
    # Buscar por (site_id, code) + bloquear si está en patio en otro predio
    # ==========================================================
    existing_here = Container.query.filter_by(site_id=site_id, code=code).first()

    other_in_yard = (
        Container.query
        .filter(Container.code == code, Container.is_in_yard == True, Container.site_id != site_id)  # noqa: E712
        .first()
    )
    if other_in_yard:
        flash("Este contenedor está en patio, pero en otro predio.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    if existing_here and existing_here.is_in_yard:
        flash("Este contenedor ya está en patio.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    if not existing_here:
        c = Container(code=code, size=size, year=year, status_notes=status_notes, is_in_yard=True, site_id=site_id)
        db.session.add(c)
        db.session.flush()
    else:
        c = existing_here
        c.size = size
        c.year = year
        c.status_notes = status_notes
        c.is_in_yard = True
        db.session.add(c)
        db.session.flush()
    # ===================== FIN CAMBIO =========================

    if placement_mode == "manual":
        try:
            depth_row = int(depth_row_raw)
            tier = int(tier_raw)
        except ValueError:
            db.session.rollback()
            flash("Fila/Nivel inválidos.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if not (1 <= depth_row <= bay.max_depth_rows) or not (1 <= tier <= bay.max_tiers):
            db.session.rollback()
            flash("Fila/Nivel fuera de rango.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        occupied = ContainerPosition.query.filter_by(bay_id=bay.id, depth_row=depth_row, tier=tier).first()
        if occupied:
            db.session.rollback()
            flash("Ese slot ya está ocupado.", "danger")
            return redirect(url_for("yard.gate_in_view"))
    else:
        slot = find_first_free_slot(bay.id)
        if not slot:
            db.session.rollback()
            flash(f"La estiba {bay.code} está llena.", "danger")
            return redirect(url_for("yard.gate_in_view"))
        depth_row, tier = slot

    ContainerPosition.query.filter_by(container_id=c.id).delete()
    db.session.add(
        ContainerPosition(
            container_id=c.id,
            bay_id=bay.id,
            depth_row=depth_row,
            tier=tier,
            placed_by_user_id=current_user.id,
        )
    )

    mv = Movement(
        site_id=site_id,
        container_id=c.id,
        movement_type="GATE_IN",
        occurred_at=datetime.utcnow(),
        bay_code=bay.code,
        depth_row=depth_row,
        tier=tier,
        driver_name=driver_name or None,
        driver_id_doc=driver_id_doc or None,
        truck_plate=truck_plate or None,
        notes=status_notes or None,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(mv)
    db.session.flush()

    storage = get_storage()
    photos = request.files.getlist("photos") or []

    for f in photos:
        if not f or not f.filename:
            continue
        try:
            key = build_photo_key(c.code, mv.id, f.filename)
            url = storage.upload_fileobj(
                f,
                key,
                f.mimetype or "application/octet-stream"
            )
            db.session.add(
                MovementPhoto(
                    movement_id=mv.id,
                    photo_type="CONTAINER",
                    url=url
                )
            )
        except Exception as e:
            db.session.add(
                MovementPhoto(
                    movement_id=mv.id,
                    photo_type="UPLOAD_ERROR",
                    url=str(e)
                )
            )

    audit_log(
        current_user.id,
        "GATE_IN_CREATED",
        "container",
        c.id,
        {"container_code": c.code, "bay": bay.code, "depth_row": depth_row, "tier": tier, "site_id": site_id},
    )

    db.session.commit()
    flash(f"Gate In registrado: {c.code} en {bay.code} F{depth_row:02d} N{tier}.", "success")
    return redirect(url_for("yard.ticket_view", movement_id=mv.id))


def _fetch_open_eirs_for_site(site_id: int, limit: int = 200):
    """
    Predios: traer EIRs abiertos/pendientes para poder 'ligar'.
    ⚠️ Usa SQL directo para no depender de modelos aún.
    Ajusta nombres de tabla/columnas si tu DB los tiene distintos.
    """
    sql = text("""
        SELECT
            e.id,
            COALESCE(e.eir_number::text, e.id::text) AS display_number,
            e.status,
            e.container_code,
            e.chassis_code,
            e.created_at
        FROM yard_gate_alamo.eirs e
        WHERE e.site_id = :sid
          AND COALESCE(e.status,'') IN ('PENDIENTE','ASIGNADO','ABIERTO')
        ORDER BY e.created_at DESC NULLS LAST, e.id DESC
        LIMIT :lim
    """)
    rows = db.session.execute(sql, {"sid": site_id, "lim": int(limit)}).mappings().all()
    return rows


@yard_bp.get("/gate-out")
@login_required
def gate_out_view():
    site_id = _ensure_active_site()

    # 🔸 Si NO es MAERSK (o sea: COYOL / CALDERA / LIMON), Gate Out = EIR flow
    active_site = Site.query.get(site_id)
    if active_site and (active_site.code or "").upper() in {"COYOL", "CALDERA", "LIMON"}:
        containers = (
            db.session.query(Container, ContainerPosition, YardBay)
            .join(ContainerPosition, ContainerPosition.container_id == Container.id)
            .join(YardBay, YardBay.id == ContainerPosition.bay_id)
            .filter(Container.is_in_yard == True, Container.site_id == site_id)  # noqa: E712
            .order_by(YardBay.code.asc(), ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
            .all()
        )

        # Chasis disponibles en patio (inventario)
        chassis_rows = (
            db.session.query(ChassisInventory, Chassis)
            .join(Chassis, Chassis.id == ChassisInventory.chassis_id)
            .filter(ChassisInventory.site_id == site_id, ChassisInventory.is_in_yard == True)  # noqa: E712
            .order_by(Chassis.chassis_number.asc())
            .all()
        )

        # EIRs draft del predio (por si quieren “ligar” uno existente)
        eirs_draft = (
            EIR.query
            .filter_by(site_id=site_id, status="DRAFT")
            .order_by(EIR.id.desc())
            .limit(200)
            .all()
        )

        return render_template(
            "yard/gate_out_predios.html",
            containers=containers,
            chassis_rows=chassis_rows,
            eirs_draft=eirs_draft,
        )

    # 🔹 MAERSK / flujo viejo intacto
    containers = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(Container.is_in_yard == True, Container.site_id == site_id)  # noqa: E712
        .order_by(YardBay.code.asc(), ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
        .all()
    )
    return render_template("yard/gate_out.html", rows=containers)


@yard_bp.post("/gate-out")
@login_required
def gate_out_post():
    site_id = _ensure_active_site()
    active_site = Site.query.get(site_id)
    is_predio = bool(active_site and (active_site.code or "").upper() in {"COYOL", "CALDERA", "LIMON"})

    # ==========================================================
    # ✅ PREDIOS: Gate Out = crear/ligar EIR + sacar contenedor + sacar chasis
    # ==========================================================
    if is_predio:
        mode = (request.form.get("mode") or "create").lower()  # create | link
        eir_id_raw = request.form.get("eir_id")
        container_id_raw = request.form.get("container_id")
        chassis_id_raw = request.form.get("chassis_id")

        # Datos básicos EIR
        terminal_name = (request.form.get("terminal_name") or (active_site.name if active_site else "")).strip()
        trip_date_raw = (request.form.get("trip_date") or "").strip()
        origin = (request.form.get("origin") or "").strip()
        destination = (request.form.get("destination") or "").strip()

        driver_name = (request.form.get("driver_name") or "").strip()
        driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
        truck_plate = (request.form.get("truck_plate") or "").strip()
        notes = (request.form.get("notes") or "").strip()

        has_container = bool(container_id_raw and str(container_id_raw).isdigit())
        has_chassis = bool(chassis_id_raw and str(chassis_id_raw).isdigit())

        if not terminal_name or not trip_date_raw or not origin or not destination:
            flash("Completa Terminal, Fecha, Origen y Destino.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        try:
            trip_date = datetime.strptime(trip_date_raw, "%Y-%m-%d").date()
        except Exception:
            flash("Fecha inválida. Usa el selector (YYYY-MM-DD).", "danger")
            return redirect(url_for("yard.gate_out_view"))

        # Validar container si viene
        c = None
        bay_code = depth_row = tier = None
        if has_container:
            c = Container.query.get(int(container_id_raw))
            if not c or not c.is_in_yard or c.site_id != site_id:
                flash("Contenedor no válido o no está en patio (predio actual).", "danger")
                return redirect(url_for("yard.gate_out_view"))

            pos = ContainerPosition.query.filter_by(container_id=c.id).first()
            if pos:
                bay = YardBay.query.get(pos.bay_id)
                bay_code = bay.code if bay else None
                depth_row = pos.depth_row
                tier = pos.tier

        # Validar chassis si viene
        ch = None
        inv = None
        if has_chassis:
            ch = Chassis.query.get(int(chassis_id_raw))
            if not ch or ch.site_id != site_id:
                flash("Chasis inválido para este predio.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            inv = (
                ChassisInventory.query
                .filter_by(site_id=site_id, chassis_id=ch.id, is_in_yard=True)
                .first()
            )
            if not inv:
                flash("Ese chasis no está disponible en inventario (predio actual).", "danger")
                return redirect(url_for("yard.gate_out_view"))

        # En predios, SIEMPRE debe haber contenedor (Movements.container_id es NOT NULL)
        if not c:
            flash("En Gate Out de predios, debes seleccionar el contenedor.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        mv = Movement(
            site_id=site_id,
            container_id=c.id,
            movement_type="GATE_OUT",
            occurred_at=datetime.utcnow(),
            bay_code=bay_code,
            depth_row=depth_row,
            tier=tier,
            driver_name=driver_name or None,
            driver_id_doc=driver_id_doc or None,
            truck_plate=truck_plate or None,
            notes=notes or None,
            created_by_user_id=current_user.id,
            created_at=datetime.utcnow(),
        )
        db.session.add(mv)
        db.session.flush()

        # Crear o ligar EIR
        if mode == "link":
            if not eir_id_raw or not str(eir_id_raw).isdigit():
                db.session.rollback()
                flash("Selecciona un EIR válido para ligar.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            eir = EIR.query.get(int(eir_id_raw))
            if not eir or eir.site_id != site_id:
                db.session.rollback()
                flash("EIR no válido para este predio.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            if eir.status != "DRAFT":
                db.session.rollback()
                flash("Solo se puede ligar un EIR en estado DRAFT.", "danger")
                return redirect(url_for("yard.gate_out_view"))
        else:
            eir = EIR(
                site_id=site_id,
                created_by_user_id=current_user.id,
                terminal_name=terminal_name,
                trip_date=trip_date,
                origin=origin,
                destination=destination,
                carrier="ATM",
                has_chassis=True if ch else False,
                chassis_id=ch.id if ch else None,
                has_container=True,
                container_id=c.id,
                is_reefer=False,
                has_genset=False,
                status="DRAFT",
            )
            db.session.add(eir)
            db.session.flush()

        # Asegurar links mínimos
        eir.has_container = True
        eir.container_id = c.id

        if ch:
            eir.has_chassis = True
            eir.chassis_id = ch.id
        else:
            eir.has_chassis = False
            eir.chassis_id = None

        eir.gate_out_movement_id = mv.id
        eir.status = "FINAL"
        eir.updated_at = db.func.now()

        # Sacar contenedor del patio
        ContainerPosition.query.filter_by(container_id=c.id).delete()
        c.is_in_yard = False

        # Sacar chasis del inventario + marcar master fuera (si aplica)
        if inv:
            inv.is_in_yard = False
        if ch:
            ch.is_in_yard = False

        audit_log(
            current_user.id,
            "GATE_OUT_PREDIO_EIR_FINALIZED",
            "eir",
            eir.id,
            {
                "site_id": site_id,
                "eir_id": eir.id,
                "movement_id": mv.id,
                "container": c.code,
                "chassis_id": ch.id if ch else None,
            },
        )

        db.session.commit()
        flash(f"Gate Out (Predio) listo. EIR #{eir.id} FINAL. Contenedor {c.code} salió.", "success")
        return redirect(url_for("yard.ticket_view", movement_id=mv.id))

    # ==========================================================
    # 🔹 MAERSK: tu flujo viejo intacto
    # ==========================================================
    container_id = request.form.get("container_id")
    driver_name = (request.form.get("driver_name") or "").strip()
    driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
    truck_plate = (request.form.get("truck_plate") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    if not container_id or not str(container_id).isdigit():
        flash("Selecciona un contenedor.", "danger")
        return redirect(url_for("yard.gate_out_view"))

    c = Container.query.get(int(container_id))
    if not c or not c.is_in_yard or c.site_id != site_id:
        flash("Contenedor no válido o ya salió (predio actual).", "danger")
        return redirect(url_for("yard.gate_out_view"))

    pos = ContainerPosition.query.filter_by(container_id=c.id).first()
    bay_code = None
    depth_row = None
    tier = None
    if pos:
        bay = YardBay.query.get(pos.bay_id)
        bay_code = bay.code if bay else None
        depth_row = pos.depth_row
        tier = pos.tier

    mv = Movement(
        site_id=site_id,
        container_id=c.id,
        movement_type="GATE_OUT",
        occurred_at=datetime.utcnow(),
        bay_code=bay_code,
        depth_row=depth_row,
        tier=tier,
        driver_name=driver_name or None,
        driver_id_doc=driver_id_doc or None,
        truck_plate=truck_plate or None,
        notes=notes or None,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(mv)
    db.session.flush()

    storage = get_storage()
    photos = request.files.getlist("photos") or []
    for f in photos:
        if not f or not f.filename:
            continue
        key = build_photo_key(c.code, mv.id, f.filename)
        url = storage.upload_fileobj(f, key, f.mimetype or "application/octet-stream")
        db.session.add(MovementPhoto(movement_id=mv.id, photo_type="DRIVER_ID", url=url))

    ContainerPosition.query.filter_by(container_id=c.id).delete()
    c.is_in_yard = False

    audit_log(
        current_user.id,
        "GATE_OUT_CREATED",
        "container",
        c.id,
        {"container_code": c.code, "from_bay": bay_code, "depth_row": depth_row, "tier": tier, "site_id": site_id},
    )

    db.session.commit()
    flash(f"Gate Out registrado: {c.code}", "success")
    return redirect(url_for("yard.ticket_view", movement_id=mv.id))


# =========================
# Reportes (respetar filtros + export Excel)
# =========================
def _cr_range_to_utc_naive(date_from: str, date_to: str):
    """
    date_from/date_to vienen como YYYY-MM-DD (día CR).
    Convertimos [00:00:00 .. 23:59:59] CR -> UTC naive (para comparar con occurred_at guardado con utcnow()).
    """
    d1_local_naive = datetime.fromisoformat(date_from + "T00:00:00")
    d2_local_naive = datetime.fromisoformat(date_to + "T23:59:59")

    d1_utc = CR_TZ.localize(d1_local_naive).astimezone(UTC_TZ)
    d2_utc = CR_TZ.localize(d2_local_naive).astimezone(UTC_TZ)

    return d1_utc.replace(tzinfo=None), d2_utc.replace(tzinfo=None)


def _parse_report_filters(args):
    movement_type = (args.get("movement_type") or "").strip().upper()
    if movement_type and movement_type not in REPORT_TYPES:
        movement_type = ""

    date_from = args.get("date_from")
    date_to = args.get("date_to")

    if not date_from or not date_to:
        return None, None, None, "Indica rango de fechas."

    try:
        d1, d2 = _cr_range_to_utc_naive(date_from, date_to)
    except Exception:
        return None, None, None, "Formato de fecha inválido (usa YYYY-MM-DD)."

    if d2 < d1:
        return None, None, None, "El rango de fechas es inválido (Hasta < Desde)."

    return movement_type, d1, d2, None


def _query_report_rows(site_id, movement_type, d1, d2):
    q = (
        db.session.query(Movement, Container)
        .join(Container, Container.id == Movement.container_id)
        .filter(Movement.site_id == site_id)
        .filter(Movement.occurred_at >= d1, Movement.occurred_at <= d2)
    )

    if movement_type:
        q = q.filter(Movement.movement_type == movement_type)

    return q.order_by(Movement.occurred_at.asc()).all()


@yard_bp.get("/reports")
@login_required
def reports_view():
    return render_template("yard/reports.html", rows=None, movement_type="", date_from="", date_to="")


@yard_bp.get("/reports/run")
@login_required
def reports_run():
    site_id = _ensure_active_site()

    movement_type, d1, d2, err = _parse_report_filters(request.args)
    if err:
        flash(err, "danger")
        return redirect(url_for("yard.reports_view"))

    rows = _query_report_rows(site_id, movement_type, d1, d2)

    audit_log(
        current_user.id,
        "REPORT_RUN",
        "report",
        None,
        {
            "from": request.args.get("date_from"),
            "to": request.args.get("date_to"),
            "movement_type": movement_type or "ALL",
            "site_id": site_id,
        },
    )
    db.session.commit()

    return render_template(
        "yard/reports.html",
        rows=rows,
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        movement_type=movement_type,
    )


@yard_bp.get("/reports/export")
@login_required
def reports_export():
    site_id = _ensure_active_site()

    movement_type, d1, d2, err = _parse_report_filters(request.args)
    if err:
        flash(err, "danger")
        return redirect(url_for("yard.reports_view"))

    rows = _query_report_rows(site_id, movement_type, d1, d2)

    try:
        from openpyxl import Workbook
        from openpyxl.utils import get_column_letter
    except Exception:
        flash("No se puede exportar: falta openpyxl en requirements.txt", "danger")
        return redirect(url_for("yard.reports_run", **request.args))

    wb = Workbook()
    ws = wb.active
    ws.title = "Reportes"

    headers = ["Fecha/Hora", "Movimiento", "Contenedor", "Ubicación", "Chofer", "Placa"]
    ws.append(headers)

    for mv, c in rows:
        loc = "—"
        if mv.bay_code:
            parts = [mv.bay_code]
            if mv.depth_row:
                parts.append(f"F{int(mv.depth_row):02d}")
            if mv.tier:
                parts.append(f"N{int(mv.tier)}")
            loc = " ".join(parts)

        ws.append([
            mv.occurred_at.strftime("%Y-%m-%d %H:%M:%S") if mv.occurred_at else "",
            mv.movement_type or "",
            c.code if c else "",
            loc,
            mv.driver_name or "",
            mv.truck_plate or "",
        ])

    for col_idx in range(1, len(headers) + 1):
        max_len = 0
        for cell in ws[get_column_letter(col_idx)]:
            v = "" if cell.value is None else str(cell.value)
            if len(v) > max_len:
                max_len = len(v)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    buff = io.BytesIO()
    wb.save(buff)
    buff.seek(0)

    audit_log(
        current_user.id,
        "REPORT_EXPORTED",
        "report",
        None,
        {
            "from": request.args.get("date_from"),
            "to": request.args.get("date_to"),
            "movement_type": movement_type or "ALL",
            "rows": len(rows),
            "site_id": site_id,
        },
    )
    db.session.commit()

    mt = movement_type or "ALL"
    fname = f"reportes_{mt}_{request.args.get('date_from')}_a_{request.args.get('date_to')}.xlsx"

    return send_file(
        buff,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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


# =========================
# Chassis helpers
# =========================

def classify_chassis_number(num: str):
    prefix = (num or "")[:2]
    if prefix == "40":
        return 40, 2, "40FT_2AX"
    if prefix == "43":
        return 40, 3, "40FT_3AX"
    if prefix == "20":
        return 20, 2, "20FT_2AX"
    if prefix == "23":
        return 20, 3, "20FT_3AX"
    return None, None, "UNKNOWN"


def allowed_positions_for(axles: int):
    if axles == 2:
        return [
            "AX1_L_IN", "AX1_L_OUT", "AX1_R_IN", "AX1_R_OUT",
            "AX2_L_IN", "AX2_L_OUT", "AX2_R_IN", "AX2_R_OUT",
        ]
    if axles == 3:
        return [
            "AX1_L_IN", "AX1_L_OUT", "AX1_R_IN", "AX1_R_OUT",
            "AX2_L_IN", "AX2_L_OUT", "AX2_R_IN", "AX2_R_OUT",
            "AX3_L_IN", "AX3_L_OUT", "AX3_R_IN", "AX3_R_OUT",
        ]
    return []


# =========================
# Chassis pages
# =========================

@yard_bp.get("/chassis")
@login_required
def chassis_list():
    site_id = _ensure_active_site()
    rows = (
        Chassis.query
        .filter_by(site_id=site_id)
        .order_by(Chassis.chassis_number.asc())
        .all()
    )
    return render_template("yard/chassis_list.html", rows=rows)


@yard_bp.get("/chassis/dashboard")
@login_required
def chassis_dashboard():
    site_id = _ensure_active_site()
    base = Chassis.query.filter_by(site_id=site_id)

    counts = {
        "40FT_2AX": base.filter(Chassis.type_code == "40FT_2AX").count(),
        "40FT_3AX": base.filter(Chassis.type_code == "40FT_3AX").count(),
        "20FT_2AX": base.filter(Chassis.type_code == "20FT_2AX").count(),
        "20FT_3AX": base.filter(Chassis.type_code == "20FT_3AX").count(),
    }
    total = base.count()
    unknown = base.filter((Chassis.type_code.is_(None)) | (Chassis.type_code == "UNKNOWN")).count()

    return render_template("yard/chassis_dashboard.html", total=total, unknown=unknown, counts=counts)


@yard_bp.get("/chassis/import")
@login_required
def chassis_import_view():
    return render_template("yard/chassis_import.html")


@yard_bp.post("/chassis/import")
@login_required
def chassis_import_post():
    site_id = _ensure_active_site()

    f = request.files.get("file")
    if not f or not f.filename:
        flash("Sube un archivo Excel.", "danger")
        return redirect(url_for("yard.chassis_import_view"))

    try:
        from openpyxl import load_workbook
    except Exception:
        flash("Falta openpyxl en requirements.txt", "danger")
        return redirect(url_for("yard.chassis_import_view"))

    wb = load_workbook(f, data_only=True)
    ws = wb.active

    imported = 0
    updated = 0
    errors = []

    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        chassis_number = (str(row[0]).strip() if row and row[0] is not None else "")
        plate = (str(row[1]).strip() if len(row) > 1 and row[1] is not None else None)
        length_ft = row[2] if len(row) > 2 else None
        axles = row[3] if len(row) > 3 else None
        type_code = (str(row[4]).strip() if len(row) > 4 and row[4] is not None else None)

        if not CHASSIS_NUM_RE.match(chassis_number):
            errors.append(f"Fila {idx}: chassis_number inválido ({chassis_number})")
            continue

        # Completar por prefijo si faltan datos
        if not length_ft or not axles or not type_code:
            d_len, d_ax, d_type = classify_chassis_number(chassis_number)
            if d_len is None or d_ax is None:
                errors.append(f"Fila {idx}: prefijo no reconocido ({chassis_number})")
                continue
            length_ft = int(length_ft) if length_ft else d_len
            axles = int(axles) if axles else d_ax
            type_code = type_code or d_type

        try:
            length_ft = int(length_ft)
            axles = int(axles)
        except Exception:
            errors.append(f"Fila {idx}: length_ft/axles inválidos")
            continue

        if length_ft not in (20, 40, 45) or axles not in (2, 3):
            errors.append(f"Fila {idx}: fuera de rango length_ft={length_ft} axles={axles}")
            continue

        existing = Chassis.query.filter_by(site_id=site_id, chassis_number=chassis_number).first()
        if existing:
            existing.plate = plate
            existing.length_ft = length_ft
            existing.axles = axles
            existing.type_code = type_code
            existing.has_plate = True if plate else False
            db.session.add(existing)
            updated += 1
        else:
            ch = Chassis(
                site_id=site_id,
                chassis_number=chassis_number,
                plate=plate,
                length_ft=length_ft,
                axles=axles,
                type_code=type_code,
                has_plate=True if plate else False,
                is_in_yard=True,
            )
            db.session.add(ch)
            imported += 1

    db.session.commit()

    if errors:
        flash(f"Importado: {imported} | Actualizado: {updated} | Errores: {len(errors)}", "warning")
        session["chassis_import_errors"] = errors[:200]
    else:
        flash(f"Importado: {imported} | Actualizado: {updated}", "success")
        session.pop("chassis_import_errors", None)

    return redirect(url_for("yard.chassis_list"))

@yard_bp.get("/chassis/export")
@login_required
def chassis_export():
    site_id = _ensure_active_site()

    try:
        from openpyxl import Workbook
    except Exception:
        flash("Falta openpyxl en requirements.txt", "danger")
        return redirect(url_for("yard.chassis_list"))

    # Trae lo que ya existe en este predio
    rows = (
        Chassis.query
        .filter_by(site_id=site_id)
        .order_by(Chassis.chassis_number.asc())
        .all()
    )

    wb = Workbook()
    ws = wb.active
    ws.title = "Chassis"

    # Encabezados con explicación en español (como pediste)
    headers = [
        "chassis_number (número de chasis 5 dígitos)",
        "plate (placa) [opcional]",
        "length_ft (largo en pies: 20/40/45) [opcional]",
        "axles (ejes: 2/3) [opcional]",
        "type_code (tipo: 20FT_2AX/20FT_3AX/40FT_2AX/40FT_3AX) [opcional]",
    ]
    ws.append(headers)

    # Congelar encabezado
    ws.freeze_panes = "A2"

    # Cargar data existente como "plantilla con lo que hay"
    for ch in rows:
        ws.append([
            ch.chassis_number,
            ch.plate or "",
            ch.length_ft or "",
            ch.axles or "",
            ch.type_code or "",
        ])

    # Ajuste simple de anchos
    ws.column_dimensions["A"].width = 38
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 30
    ws.column_dimensions["D"].width = 22
    ws.column_dimensions["E"].width = 45

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = "chassis_import_template.xlsx"
    return send_file(
        bio,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@yard_bp.get("/chassis/<int:chassis_id>")
@login_required
def chassis_detail(chassis_id: int):
    site_id = _ensure_active_site()
    ch = Chassis.query.get_or_404(chassis_id)

    if ch.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    axles = int(getattr(ch, "axles", 2) or 2)
    length_ft = int(getattr(ch, "length_ft", 40) or 40)
    return render_template("yard/chassis_detail.html", ch=ch, axles=axles, length_ft=length_ft)


# =========================
# Chassis tires API
# =========================

@yard_bp.get("/api/chassis/<int:chassis_id>/tires")
@login_required
def api_chassis_tires_get(chassis_id: int):
    site_id = _ensure_active_site()
    ch = Chassis.query.get_or_404(chassis_id)

    if ch.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    axles = int(getattr(ch, "axles", 2) or 2)
    allowed = set(allowed_positions_for(axles))

    rows = ChassisTire.query.filter_by(chassis_id=ch.id).all()
    positions = {p: None for p in allowed}

    for r in rows:
        if r.position_code not in allowed:
            continue
        positions[r.position_code] = {
            "marchamo": r.marchamo,
            "tire_state": r.tire_state,
            "tire_number": r.tire.tire_number if r.tire else None,
            "brand": r.tire.brand if r.tire else None,
        }

    return jsonify({"ok": True, "positions": positions})


@yard_bp.post("/api/chassis/<int:chassis_id>/tires")
@login_required
def api_chassis_tires_set(chassis_id: int):
    site_id = _ensure_active_site()
    ch = Chassis.query.get_or_404(chassis_id)

    if ch.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    axles = int(getattr(ch, "axles", 2) or 2)
    allowed = set(allowed_positions_for(axles))

    data = request.get_json(silent=True) or {}
    pos = (data.get("position_code") or "").strip().upper()
    if pos not in allowed:
        return jsonify({"ok": False, "error": "INVALID_POSITION"}), 400

    marchamo = (data.get("marchamo") or "").strip()
    tire_number = (data.get("tire_number") or "").strip()
    brand = (data.get("brand") or "").strip()
    tire_state = (data.get("tire_state") or "OK").strip().upper()

    if tire_state not in TIRE_STATES:
        return jsonify({"ok": False, "error": "INVALID_TIRE_STATE"}), 400

    tire = None
    if tire_number:
        tire = Tire.query.filter_by(tire_number=tire_number).first()
        if not tire:
            tire = Tire(tire_number=tire_number, brand=brand or None)
            db.session.add(tire)
            db.session.flush()
        else:
            if brand and (tire.brand != brand):
                tire.brand = brand
                db.session.add(tire)

    row = ChassisTire.query.filter_by(chassis_id=ch.id, position_code=pos).first()
    if not row:
        row = ChassisTire(chassis_id=ch.id, position_code=pos)

    row.marchamo = marchamo or None
    row.tire_state = tire_state
    row.tire_id = tire.id if tire else None
    row.updated_at = datetime.utcnow()

    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True})

