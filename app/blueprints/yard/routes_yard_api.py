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
# Helpers
# =========================

def _get_vertical_blockers(*, bay_id: int, depth_row: int, tier: int, site_id: int):
    """
    Retorna los contenedores que están encima de una posición específica
    dentro de la misma estiba y misma fila.

    Regla:
    Si quiero mover un contenedor en F01/N2,
    primero deben estar libres N3, N4, etc.
    """

    blockers = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay_id,
            ContainerPosition.depth_row == depth_row,
            ContainerPosition.tier > tier,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
        .order_by(ContainerPosition.tier.asc())
        .all()
    )

    return [
        {
            "container_id": c.id,
            "container_code": c.code,
            "depth_row": p.depth_row,
            "tier": p.tier,
        }
        for c, p in blockers
    ]

def _has_support_below(*, bay_id: int, depth_row: int, tier: int):
    """
    Verifica que exista soporte debajo.

    Reglas:

    N1 = siempre válido.
    N2 requiere N1.
    N3 requiere N2.
    N4 requiere N3.
    """

    if tier <= 1:
        return True

    below = ContainerPosition.query.filter_by(
        bay_id=bay_id,
        depth_row=depth_row,
        tier=tier - 1
    ).first()

    return below is not None

def _get_sidepick_access_blockers(*, bay_id: int, depth_row: int, tier: int, site_id: int):
    """
    Detecta contenedores que bloquean el acceso horizontal de la sidepick.

    Regla actual asumida:
    - La sidepick entra desde la fila más externa/mayor.
    - Para llegar a F01, deben estar libres F02, F03, F04...
    - Para llegar a F02, deben estar libres F03, F04...
    - Para llegar a F03, debe estar libre F04...
    - F04 es la fila más accesible.

    Importante:
    Este helper NO revisa contenedores encima.
    Eso ya lo hace _get_vertical_blockers().
    """

    bay = YardBay.query.get(bay_id)
    if not bay:
        return []

    max_depth_rows = int(bay.max_depth_rows or 1)

    # Si está en la fila más externa, no hay bloqueo horizontal.
    if depth_row >= max_depth_rows:
        return []

    blockers = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay_id,
            ContainerPosition.depth_row > depth_row,
            ContainerPosition.tier >= 1,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
        .order_by(ContainerPosition.depth_row.asc(), ContainerPosition.tier.asc())
        .all()
    )

    return [
        {
            "container_id": c.id,
            "container_code": c.code,
            "depth_row": p.depth_row,
            "tier": p.tier,
        }
        for c, p in blockers
    ]

def _validate_container_can_be_removed(*, container_id: int, site_id: int):
    """
    Valida si un contenedor puede ser retirado/movido desde su posición actual.

    Reglas:
    1. Debe existir posición actual.
    2. No debe tener contenedores encima.
    3. La sidepick debe poder acceder a su fila.
    """

    current_pos = ContainerPosition.query.filter_by(
        container_id=container_id
    ).first()

    if not current_pos:
        return {
            "ok": False,
            "error": "POSITION_NOT_FOUND",
            "message": "El contenedor no tiene posición registrada en patio.",
            "blockers": [],
        }

    vertical_blockers = _get_vertical_blockers(
        bay_id=current_pos.bay_id,
        depth_row=current_pos.depth_row,
        tier=current_pos.tier,
        site_id=site_id,
    )

    access_blockers = _get_sidepick_access_blockers(
        bay_id=current_pos.bay_id,
        depth_row=current_pos.depth_row,
        tier=current_pos.tier,
        site_id=site_id,
    )

    blockers = []

    for item in vertical_blockers:
        blockers.append({
            **item,
            "reason": "VERTICAL",
            "message": "Contenedor encima",
        })

    for item in access_blockers:
        blockers.append({
            **item,
            "reason": "ACCESS",
            "message": "Bloquea acceso de sidepick",
        })

    if blockers:
        return {
            "ok": False,
            "error": "CONTAINER_BLOCKED",
            "message": "El contenedor no puede moverse porque está bloqueado.",
            "blockers": blockers,
            "current_position": {
                "bay_id": current_pos.bay_id,
                "depth_row": current_pos.depth_row,
                "tier": current_pos.tier,
            },
        }

    return {
        "ok": True,
        "blockers": [],
        "current_position": {
            "bay_id": current_pos.bay_id,
            "depth_row": current_pos.depth_row,
            "tier": current_pos.tier,
        },
    }


def _validate_container_can_be_placed_at(
    *,
    bay_id: int,
    depth_row: int,
    tier: int,
    site_id: int,
):
    """
    Valida si se puede colocar un contenedor en una posición específica.

    Reglas:
    1. La estiba debe existir.
    2. La fila y nivel deben estar dentro del rango.
    3. El slot debe estar libre.
    4. No se puede colocar flotando: debe tener soporte debajo.
    5. La sidepick debe poder acceder a esa fila.
    """

    bay = YardBay.query.filter_by(
        id=bay_id,
        site_id=site_id,
        is_active=True,
    ).first()

    if not bay:
        return {
            "ok": False,
            "error": "BAY_NOT_FOUND",
            "message": "La estiba destino no existe o no pertenece al predio actual.",
            "blockers": [],
        }

    if depth_row < 1 or depth_row > int(bay.max_depth_rows or 0):
        return {
            "ok": False,
            "error": "ROW_OUT_OF_RANGE",
            "message": "La fila destino está fuera del rango permitido.",
            "blockers": [],
        }

    if tier < 1 or tier > int(bay.max_tiers or 0):
        return {
            "ok": False,
            "error": "TIER_OUT_OF_RANGE",
            "message": "El nivel destino está fuera del rango permitido.",
            "blockers": [],
        }

    occupied = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay_id,
            ContainerPosition.depth_row == depth_row,
            ContainerPosition.tier == tier,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
        .first()
    )

    if occupied:
        c, p = occupied
        return {
            "ok": False,
            "error": "SLOT_OCCUPIED",
            "message": "La posición destino ya está ocupada.",
            "blockers": [
                {
                    "container_id": c.id,
                    "container_code": c.code,
                    "depth_row": p.depth_row,
                    "tier": p.tier,
                    "reason": "OCCUPIED",
                    "message": "Ocupa el slot destino",
                }
            ],
        }

    if not _has_support_below(
        bay_id=bay_id,
        depth_row=depth_row,
        tier=tier,
    ):
        return {
            "ok": False,
            "error": "NO_SUPPORT_BELOW",
            "message": "No se puede colocar el contenedor en el aire. Debe existir un contenedor debajo.",
            "blockers": [],
        }

    access_blockers = _get_sidepick_access_blockers(
        bay_id=bay_id,
        depth_row=depth_row,
        tier=tier,
        site_id=site_id,
    )

    if access_blockers:
        return {
            "ok": False,
            "error": "DESTINATION_NOT_ACCESSIBLE",
            "message": "La sidepick no puede acceder a la fila destino porque hay contenedores bloqueando el paso.",
            "blockers": [
                {
                    **item,
                    "reason": "ACCESS",
                    "message": "Bloquea acceso a la fila destino",
                }
                for item in access_blockers
            ],
        }

    return {
        "ok": True,
        "message": "Destino válido.",
        "blockers": [],
        "destination": {
            "bay_id": bay.id,
            "bay_code": bay.code,
            "depth_row": depth_row,
            "tier": tier,
        },
    }

def _yard_validation_error_response(validation: dict, status_code: int = 409):
    """
    Convierte una validación operativa del patio en una respuesta JSON uniforme.

    Sirve para:
    - contenedor bloqueado por otro encima
    - acceso bloqueado por sidepick
    - destino sin soporte
    - slot ocupado
    - fila/nivel inválido
    """

    return jsonify({
        "ok": False,
        "error": validation.get("error") or "YARD_OPERATION_NOT_ALLOWED",
        "message": validation.get("message") or "La operación no está permitida por las reglas del patio.",
        "blockers": validation.get("blockers") or [],
        "current_position": validation.get("current_position"),
        "destination": validation.get("destination"),
    }), status_code

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
      - Viejo: { "container_id": 123, "to_bay_code": "A07" } -> AUTO
      - Nuevo: { "container_id": 123, "to_bay_code": "A07", "to_depth_row": 10, "to_tier": 2 } -> EXACTO

    Validaciones nuevas:
      - si el contenedor ya tiene posición, valida que pueda salir
      - valida destino con reglas sidepick
      - valida soporte inferior
      - valida slot ocupado
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

    to_bay = YardBay.query.filter_by(
        code=to_bay_code,
        is_active=True,
        site_id=site_id,
    ).first()

    if not to_bay:
        return jsonify({"error": "Estiba destino inválida"}), 400

    db.session.query(YardBay).filter(
        YardBay.id == to_bay.id
    ).with_for_update().one()

    # =========================
    # VALIDAR ORIGEN SOLO SI YA TIENE POSICIÓN
    # =========================
    old_pos = ContainerPosition.query.filter_by(
        container_id=c.id
    ).first()

    if old_pos:
        origin_validation = _validate_container_can_be_removed(
            container_id=c.id,
            site_id=site_id,
        )

        if not origin_validation.get("ok"):
            return _yard_validation_error_response(origin_validation, 409)

    # =========================
    # RESOLVER DESTINO
    # =========================
    to_depth_row = data.get("to_depth_row")
    to_tier = data.get("to_tier")

    if to_depth_row is not None and to_tier is not None:
        try:
            depth_row = int(to_depth_row)
            tier = int(to_tier)
        except Exception:
            return jsonify({"error": "Fila/Nivel inválidos"}), 400
    else:
        slot = find_first_free_slot(to_bay.id)
        if not slot:
            return jsonify({"error": "Estiba llena"}), 409

        depth_row, tier = slot

    # =========================
    # VALIDAR DESTINO
    # =========================
    destination_validation = _validate_container_can_be_placed_at(
        bay_id=to_bay.id,
        depth_row=depth_row,
        tier=tier,
        site_id=site_id,
    )

    if not destination_validation.get("ok"):
        return _yard_validation_error_response(destination_validation, 409)

    # =========================
    # GUARDAR POSICIÓN ANTERIOR
    # =========================
    old = None
    if old_pos:
        old_bay = YardBay.query.get(old_pos.bay_id)
        old = {
            "bay_code": old_bay.code if old_bay else None,
            "depth_row": old_pos.depth_row,
            "tier": old_pos.tier,
        }

    # =========================
    # COLOCAR CONTENEDOR
    # =========================
    ContainerPosition.query.filter_by(
        container_id=c.id
    ).delete()

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
        notes="PLACED_BY_BLOCK_UI_VALIDATED_SIDEPICK_RULES",
    )
    db.session.add(mv)

    audit_log(
        current_user.id,
        "CONTAINER_PLACED",
        "container",
        c.id,
        {
            "from": old,
            "to": {
                "bay_code": to_bay.code,
                "depth_row": depth_row,
                "tier": tier,
            },
            "rule": "AUTO_LAST_AVAILABLE_VALIDATED" if (to_depth_row is None or to_tier is None) else "MANUAL_EXACT_VALIDATED",
            "site_id": site_id,
        },
    )

    db.session.commit()

    return jsonify({
        "ok": True,
        "bay_code": to_bay.code,
        "depth_row": depth_row,
        "tier": tier,
    })


@yard_bp.post("/api/yard/move")
@login_required
def api_move_container():
    """
    Drag & drop / movimiento de contenedor.

    Valida:
    - que el contenedor exista y esté en el predio actual
    - que pueda salir de su posición actual
    - que el destino exista
    - que fila/nivel sean válidos
    - que el slot esté libre
    - que no quede flotando
    - que la sidepick pueda acceder al destino
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

    # =========================
    # VALIDAR ORIGEN
    # =========================
    origin_validation = _validate_container_can_be_removed(
        container_id=c.id,
        site_id=site_id,
    )

    if not origin_validation.get("ok"):
        return _yard_validation_error_response(origin_validation, 409)

    to_bay = YardBay.query.filter_by(
        code=to_bay_code,
        is_active=True,
        site_id=site_id,
    ).first()

    if not to_bay:
        return jsonify({"error": "Estiba destino inválida"}), 400

    db.session.query(YardBay).filter(
        YardBay.id == to_bay.id
    ).with_for_update().one()

    # =========================
    # RESOLVER DESTINO
    # =========================
    if mode == "manual":
        try:
            depth_row = int(data.get("depth_row"))
            tier = int(data.get("tier"))
        except Exception:
            return jsonify({"error": "Fila/Nivel inválidos"}), 400

    else:
        slot = find_first_free_slot(to_bay.id)
        if not slot:
            return jsonify({"error": "Estiba llena"}), 409

        depth_row, tier = slot

    # =========================
    # VALIDAR DESTINO
    # =========================
    destination_validation = _validate_container_can_be_placed_at(
        bay_id=to_bay.id,
        depth_row=depth_row,
        tier=tier,
        site_id=site_id,
    )

    if not destination_validation.get("ok"):
        return _yard_validation_error_response(destination_validation, 409)

    # =========================
    # GUARDAR POSICIÓN ANTERIOR
    # =========================
    old_pos = ContainerPosition.query.filter_by(
        container_id=c.id
    ).first()

    old = None
    if old_pos:
        old_bay = YardBay.query.get(old_pos.bay_id)
        old = {
            "bay_code": old_bay.code if old_bay else None,
            "depth_row": old_pos.depth_row,
            "tier": old_pos.tier,
        }

    # =========================
    # MOVER CONTENEDOR
    # =========================
    ContainerPosition.query.filter_by(
        container_id=c.id
    ).delete()

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
        notes="MOVE_VALIDATED_SIDEPICK_RULES",
    )
    db.session.add(mv)

    audit_log(
        current_user.id,
        "CONTAINER_MOVED",
        "container",
        c.id,
        {
            "from": old,
            "to": {
                "bay_code": to_bay.code,
                "depth_row": depth_row,
                "tier": tier,
            },
            "mode": mode,
            "rule": "SIDE_PICK_VALIDATED",
            "site_id": site_id,
        },
    )

    db.session.commit()

    return jsonify({
        "ok": True,
        "bay_code": to_bay.code,
        "depth_row": depth_row,
        "tier": tier,
    })


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