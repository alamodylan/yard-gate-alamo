from datetime import datetime

from flask import jsonify, request
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.movement import Movement
from app.services.audit import audit_log
from app.services.yard_logic import find_first_free_slot

from .routes import _ensure_active_site


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

    db.session.query(YardBay).filter(YardBay.id == to_bay.id).with_for_update().one()

    if mode == "manual":
        try:
            depth_row = int(data.get("depth_row"))
            tier = int(data.get("tier"))
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
        notes=None,
    )
    db.session.add(mv)

    audit_log(
        current_user.id,
        "CONTAINER_MOVED",
        "container",
        c.id,
        {
            "from": old,
            "to": {"bay_code": to_bay.code, "depth_row": depth_row, "tier": tier},
            "site_id": site_id,
        },
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