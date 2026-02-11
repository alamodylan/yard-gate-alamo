# app/blueprints/yard/routes.py
import re
from datetime import datetime
import os
import requests
import io

from flask import render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.movement import Movement, MovementPhoto
from app.models.ticket import TicketPrint
from app.services.audit import audit_log
from app.services.yard_logic import find_first_free_slot
from app.services.storage import get_storage, build_photo_key
from app.services.ticketing import build_ticket_payload, register_ticket_print

CONTAINER_RE = re.compile(r"^[A-Z]{4}-\d{6}-\d$")
SIZES = ["20ST", "40ST", "40HC", "45ST"]
APP_NAME = "Yard Gate Álamo"

REPORT_TYPES = {"GATE_IN", "GATE_OUT", "MOVE"}


@yard_bp.get("/")
@login_required
def home():
    return redirect(url_for("yard.map_view"))


@yard_bp.get("/map")
@login_required
def map_view():
    blocks = YardBlock.query.order_by(YardBlock.code.asc()).all()
    selected_block = (request.args.get("block") or "A").upper()
    if selected_block not in {"A", "B", "C", "D"}:
        selected_block = "A"
    return render_template("yard/map.html", blocks=blocks, selected_block=selected_block)


@yard_bp.get("/bay/<string:bay_code>")
@login_required
def bay_detail_view(bay_code: str):
    bay_code = bay_code.upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True).first_or_404()

    rows = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(ContainerPosition.bay_id == bay.id)
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
    rows = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(Container.is_in_yard == True)  # noqa: E712
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
    block_code = (request.args.get("block") or "").upper()
    block = YardBlock.query.filter_by(code=block_code).first()
    if not block:
        return jsonify({"bays": []})

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True)
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
    block_code = (request.args.get("block") or "A").upper()
    block = YardBlock.query.filter_by(code=block_code).first()
    if not block:
        return jsonify({"error": "Bloque inválido"}), 400

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True)
        .order_by(YardBay.bay_number.asc())
        .all()
    )

    counts = dict(
        db.session.query(ContainerPosition.bay_id, db.func.count(ContainerPosition.container_id))
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
    block_code = (block_code or "").upper()
    block = YardBlock.query.filter_by(code=block_code).first()
    if not block:
        return jsonify({"error": "Bloque inválido"}), 400

    bays = (
        YardBay.query.filter_by(block_id=block.id, is_active=True)
        .order_by(YardBay.bay_number.asc())
        .all()
    )

    counts = dict(
        db.session.query(ContainerPosition.bay_id, db.func.count(ContainerPosition.container_id))
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
    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True).first()
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
    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True).first()
    if not bay:
        return jsonify({"error": "Estiba inválida"}), 400

    max_levels = int(bay.max_tiers or 4)

    counts_by_row = dict(
        db.session.query(ContainerPosition.depth_row, db.func.count(ContainerPosition.container_id))
        .filter(ContainerPosition.bay_id == bay.id)
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
                .filter(ContainerPosition.bay_id == bay.id, ContainerPosition.depth_row == row_num)
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
    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True).first()
    if not bay:
        return jsonify({"error": "Estiba inválida"}), 400

    if row_number < 1 or row_number > int(bay.max_depth_rows or 0):
        return jsonify({"ok": False, "error": "ROW_OUT_OF_RANGE"}), 400

    occupied = (
        db.session.query(ContainerPosition.tier)
        .filter(ContainerPosition.bay_id == bay.id, ContainerPosition.depth_row == row_number)
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
    data = request.get_json(silent=True) or {}
    container_id = data.get("container_id")
    to_bay_code = (data.get("to_bay_code") or "").upper()

    if not container_id or not to_bay_code:
        return jsonify({"error": "Datos incompletos"}), 400

    c = Container.query.get(container_id)
    if not c or not c.is_in_yard:
        return jsonify({"error": "Contenedor no existe o no está en patio"}), 400

    to_bay = YardBay.query.filter_by(code=to_bay_code, is_active=True).first()
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
    data = request.get_json(silent=True) or {}
    container_id = data.get("container_id")
    to_bay_code = (data.get("to_bay_code") or "").upper()
    mode = (data.get("mode") or "auto").lower()

    if not container_id or not to_bay_code:
        return jsonify({"error": "Datos incompletos"}), 400

    c = Container.query.get(container_id)
    if not c or not c.is_in_yard:
        return jsonify({"error": "Contenedor no existe o no está en patio"}), 400

    to_bay = YardBay.query.filter_by(code=to_bay_code, is_active=True).first()
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
        {"from": old, "to": {"bay_code": to_bay.code, "depth_row": depth_row, "tier": tier}},
    )

    db.session.commit()
    return jsonify({"ok": True, "bay_code": to_bay.code, "depth_row": depth_row, "tier": tier})


@yard_bp.get("/api/yard/bays/<string:bay_code>/row/<int:row_number>/containers")
@login_required
def api_bay_row_containers(bay_code: str, row_number: int):
    bay_code = (bay_code or "").upper()
    bay = YardBay.query.filter_by(code=bay_code, is_active=True).first()
    if not bay:
        return jsonify({"ok": False, "error": "BAY_NOT_FOUND"}), 404

    if row_number < 1 or row_number > int(bay.max_depth_rows or 0):
        return jsonify({"ok": False, "error": "ROW_OUT_OF_RANGE"}), 400

    rows = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay.id,
            ContainerPosition.depth_row == row_number
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
    blocks = YardBlock.query.order_by(YardBlock.code.asc()).all()
    return render_template("yard/gate_in.html", blocks=blocks, sizes=SIZES)


@yard_bp.post("/gate-in")
@login_required
def gate_in_post():
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

    block = YardBlock.query.filter_by(code=block_code).first()
    bay = YardBay.query.filter_by(block_id=block.id, bay_number=bay_number, is_active=True).first() if block else None
    if not bay:
        flash("Estiba no encontrada.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    # Lock de estiba durante asignación
    db.session.query(YardBay).filter(YardBay.id == bay.id).with_for_update().one()

    existing = Container.query.filter_by(code=code).first()
    if existing and existing.is_in_yard:
        flash("Este contenedor ya está en patio.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    if not existing:
        c = Container(code=code, size=size, year=year, status_notes=status_notes, is_in_yard=True)
        db.session.add(c)
        db.session.flush()
    else:
        c = existing
        c.size = size
        c.year = year
        c.status_notes = status_notes
        c.is_in_yard = True
        db.session.add(c)
        db.session.flush()

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
            # 🔒 No tumbamos el Gate In por error de fotos
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
        {"container_code": c.code, "bay": bay.code, "depth_row": depth_row, "tier": tier},
    )

    db.session.commit()
    flash(f"Gate In registrado: {c.code} en {bay.code} F{depth_row:02d} N{tier}.", "success")
    return redirect(url_for("yard.ticket_view", movement_id=mv.id))


@yard_bp.get("/gate-out")
@login_required
def gate_out_view():
    containers = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(Container.is_in_yard == True)  # noqa: E712
        .order_by(YardBay.code.asc(), ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
        .all()
    )
    return render_template("yard/gate_out.html", rows=containers)


@yard_bp.post("/gate-out")
@login_required
def gate_out_post():
    container_id = request.form.get("container_id")
    driver_name = (request.form.get("driver_name") or "").strip()
    driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
    truck_plate = (request.form.get("truck_plate") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    if not container_id or not str(container_id).isdigit():
        flash("Selecciona un contenedor.", "danger")
        return redirect(url_for("yard.gate_out_view"))

    c = Container.query.get(int(container_id))
    if not c or not c.is_in_yard:
        flash("Contenedor no válido o ya salió.", "danger")
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
        {"container_code": c.code, "from_bay": bay_code, "depth_row": depth_row, "tier": tier},
    )

    db.session.commit()
    flash(f"Gate Out registrado: {c.code}", "success")
    return redirect(url_for("yard.ticket_view", movement_id=mv.id))


# =========================
# Reportes (respetar filtros + export Excel)
# =========================

def _parse_report_filters(args):
    movement_type = (args.get("movement_type") or "").strip().upper()
    if movement_type and movement_type not in REPORT_TYPES:
        movement_type = ""

    date_from = args.get("date_from")
    date_to = args.get("date_to")

    if not date_from or not date_to:
        return None, None, None, "Indica rango de fechas."

    try:
        d1 = datetime.fromisoformat(date_from + "T00:00:00")
        d2 = datetime.fromisoformat(date_to + "T23:59:59")
    except Exception:
        return None, None, None, "Formato de fecha inválido (usa YYYY-MM-DD)."

    if d2 < d1:
        return None, None, None, "El rango de fechas es inválido (Hasta < Desde)."

    return movement_type, d1, d2, None


def _query_report_rows(movement_type, d1, d2):
    q = (
        db.session.query(Movement, Container)
        .join(Container, Container.id == Movement.container_id)
        .filter(Movement.occurred_at >= d1, Movement.occurred_at <= d2)
    )

    if movement_type:
        q = q.filter(Movement.movement_type == movement_type)

    return q.order_by(Movement.occurred_at.asc()).all()


@yard_bp.get("/reports")
@login_required
def reports_view():
    # Vista “limpia” sin resultados (pero con campos disponibles)
    return render_template("yard/reports.html", rows=None, movement_type="", date_from="", date_to="")


@yard_bp.get("/reports/run")
@login_required
def reports_run():
    movement_type, d1, d2, err = _parse_report_filters(request.args)
    if err:
        flash(err, "danger")
        return redirect(url_for("yard.reports_view"))

    rows = _query_report_rows(movement_type, d1, d2)

    audit_log(
        current_user.id,
        "REPORT_RUN",
        "report",
        None,
        {"from": request.args.get("date_from"), "to": request.args.get("date_to"), "movement_type": movement_type or "ALL"},
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
    movement_type, d1, d2, err = _parse_report_filters(request.args)
    if err:
        flash(err, "danger")
        return redirect(url_for("yard.reports_view"))

    rows = _query_report_rows(movement_type, d1, d2)

    # Import local para no romper la app si falta la dependencia
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

    # Auto ancho de columnas (simple y efectivo)
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
        {"from": request.args.get("date_from"), "to": request.args.get("date_to"), "movement_type": movement_type or "ALL", "rows": len(rows)},
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
    mv = Movement.query.get_or_404(movement_id)
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

    register_ticket_print(mv.id, current_user.id, payload)
    audit_log(current_user.id, "TICKET_PRINTED_AGENT", "movement", mv.id, {"container": c.code})
    db.session.commit()

    return jsonify({"ok": True})


@yard_bp.get("/ticket/<int:movement_id>")
@login_required
def ticket_view(movement_id: int):
    mv = Movement.query.get_or_404(movement_id)
    c = Container.query.get_or_404(mv.container_id)

    payload = build_ticket_payload(APP_NAME, mv, c)
    register_ticket_print(mv.id, current_user.id, payload)
    audit_log(current_user.id, "TICKET_PRINTED", "movement", mv.id, {"container": c.code})
    db.session.commit()

    return render_template("yard/ticket.html", mv=mv, c=c, payload=payload)


@yard_bp.get("/ticket/reprint/<int:print_id>")
@login_required
def ticket_reprint(print_id: int):
    tp = TicketPrint.query.get_or_404(print_id)
    mv = Movement.query.get_or_404(tp.movement_id)
    c = Container.query.get_or_404(mv.container_id)

    audit_log(
        current_user.id,
        "TICKET_REPRINTED",
        "ticket_print",
        tp.id,
        {"movement_id": mv.id, "container": c.code},
    )
    db.session.commit()

    return render_template("yard/ticket.html", mv=mv, c=c, payload=tp.ticket_payload, is_reprint=True)





