from datetime import datetime, date, timedelta

from flask import jsonify, request
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.movement import Movement
from app.services.audit import audit_log
from app.services.yard_logic import find_first_free_slot
from zoneinfo import ZoneInfo

from app.models.dispatch import DispatchAssignment, DispatchRequestLine, DispatchRequest

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

def _has_support_below(
    *,
    bay_id: int,
    depth_row: int,
    tier: int,
    exclude_container_id: int | None = None,
):
    if tier <= 1:
        return True

    query = ContainerPosition.query.filter_by(
        bay_id=bay_id,
        depth_row=depth_row,
        tier=tier - 1,
    )

    if exclude_container_id:
        query = query.filter(ContainerPosition.container_id != int(exclude_container_id))

    below = query.first()

    return below is not None

def _get_sidepick_access_blockers(
    *,
    bay_id: int,
    depth_row: int,
    tier: int,
    site_id: int,
    exclude_container_id: int | None = None,
):
    bay = YardBay.query.get(bay_id)
    if not bay:
        return []

    max_depth_rows = int(bay.max_depth_rows or 1)

    if depth_row >= max_depth_rows:
        return []

    query = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay_id,
            ContainerPosition.depth_row > depth_row,
            ContainerPosition.tier >= 1,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
    )

    if exclude_container_id:
        query = query.filter(Container.id != int(exclude_container_id))

    blockers = (
        query
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
    exclude_container_id: int | None = None,
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

    occupied_query = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay_id,
            ContainerPosition.depth_row == depth_row,
            ContainerPosition.tier == tier,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
    )

    if exclude_container_id:
        occupied_query = occupied_query.filter(
            Container.id != int(exclude_container_id)
        )

    occupied = occupied_query.first()

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
        exclude_container_id=exclude_container_id,
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
        exclude_container_id=exclude_container_id,
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
    Para bandeja/mapa:
    Retorna contenedores en patio con su posición actual, estado operativo
    y si pertenece a la prelista del mapa.

    Regla prelista mapa:
    - Hoy completo.
    - Mañana completo.
    - Si se monta, NO desaparece; solo cambia de estado/color.
    """
    site_id = _ensure_active_site()

    cr_tz = ZoneInfo("America/Costa_Rica")
    today_cr = datetime.now(cr_tz).date()
    tomorrow_cr = today_cr + timedelta(days=1)

    rows = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(
            Container.is_in_yard == True,  # noqa: E712
            Container.site_id == site_id,
        )
        .order_by(
            YardBay.code.asc(),
            ContainerPosition.depth_row.asc(),
            ContainerPosition.tier.asc(),
        )
        .all()
    )

    container_ids = [c.id for c, _, _ in rows]

    prelist_by_container = {}

    if container_ids:
        prelist_rows = (
            db.session.query(
                DispatchAssignment.container_id,
                DispatchRequest.id.label("request_id"),
                DispatchRequest.request_type,
                DispatchRequest.booking,
                DispatchRequest.shipping_line,
                DispatchRequest.client_name,
                DispatchRequest.product_name,
                DispatchRequest.chassis_type,
                DispatchRequest.port_out,
                DispatchRequestLine.load_date,
                DispatchRequestLine.load_time,
                DispatchRequestLine.container_size,
                DispatchRequestLine.condition_type,
            )
            .join(
                DispatchRequestLine,
                DispatchRequestLine.id == DispatchAssignment.request_line_id,
            )
            .join(
                DispatchRequest,
                DispatchRequest.id == DispatchRequestLine.request_id,
            )
            .filter(
                DispatchAssignment.container_id.in_(container_ids),
                DispatchRequest.site_id == site_id,
                DispatchRequest.status != "CANCELADA",
                DispatchRequestLine.load_date.in_([today_cr, tomorrow_cr]),
            )
            .order_by(
                DispatchRequestLine.load_date.asc(),
                DispatchRequestLine.load_time.asc().nulls_last(),
                DispatchAssignment.id.asc(),
            )
            .all()
        )

        for row in prelist_rows:
            if row.container_id not in prelist_by_container:
                prelist_by_container[row.container_id] = {
                    "request_id": row.request_id,
                    "request_type": row.request_type,
                    "booking": row.booking,
                    "shipping_line": row.shipping_line,
                    "client_name": row.client_name,
                    "product_name": row.product_name,
                    "chassis_type": row.chassis_type,
                    "port_out": row.port_out,
                    "load_date": row.load_date.isoformat() if row.load_date else None,
                    "load_time": row.load_time.strftime("%H:%M") if row.load_time else None,
                    "container_size": row.container_size,
                    "condition_type": row.condition_type,
                }

    payload = []

    for c, p, bay in rows:
        dispatch_status = (c.dispatch_status or "NORMAL").strip().upper()
        prelist_info = prelist_by_container.get(c.id)

        payload.append(
            {
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "year": c.year,
                "status_notes": c.status_notes,
                "dispatch_status": dispatch_status,

                # True solo si el contenedor está en prelista de hoy o mañana.
                "is_prelist_visible": bool(prelist_info),
                "prelist": prelist_info,

                "position": {
                    "bay_code": bay.code,
                    "depth_row": p.depth_row,
                    "tier": p.tier,
                },
            }
        )

    return jsonify({"rows": payload})

@yard_bp.get("/api/yard/mounted-containers")
@login_required
def api_mounted_containers():

    site_id = _ensure_active_site()

    rows = (
        Container.query
        .filter(
            Container.site_id == site_id,
            Container.is_in_yard == True,
            Container.dispatch_status.in_([
                "DESPACHO_MONTADO",
                "EVACUACION_MONTADA",
            ])
        )
        .order_by(Container.code.asc())
        .all()
    )

    payload = []

    for c in rows:
        payload.append({
            "id": c.id,
            "code": c.code,
            "size": c.size,
            "dispatch_status": c.dispatch_status,
            "mounted_at": (
                c.mounted_at.isoformat()
                if getattr(c, "mounted_at", None)
                else None
            )
        })

    return jsonify({
        "rows": payload
    })

@yard_bp.post("/api/yard/mount-container")
@login_required
def api_mount_container():
    """
    Marca un contenedor como montado.

    Reglas:
    - Debe existir y estar en patio.
    - Debe pertenecer al predio activo.
    - Debe poder salir físicamente según reglas sidepick.
    - Solo se puede montar si está:
        * PARA_DESPACHO
        * EVACUAR_SOLICITADO
    - Al montar:
        * cambia dispatch_status a estado montado
        * registra movimiento MOUNT con la posición anterior
        * elimina ContainerPosition para liberar la posición física del mapa
        * mantiene is_in_yard=True hasta que se haga Gate Out/salida real
    """

    site_id = _ensure_active_site()

    data = request.get_json(silent=True) or {}
    container_id = data.get("container_id")

    if not container_id:
        return jsonify({
            "ok": False,
            "error": "CONTAINER_REQUIRED",
            "message": "Debe indicar el contenedor a montar.",
        }), 400

    c = Container.query.get(container_id)

    if not c or not c.is_in_yard or c.site_id != site_id:
        return jsonify({
            "ok": False,
            "error": "CONTAINER_NOT_FOUND",
            "message": "El contenedor no existe o no está en patio en el predio actual.",
        }), 404

    validation = _validate_container_can_be_removed(
        container_id=c.id,
        site_id=site_id,
    )

    if not validation.get("ok"):
        return _yard_validation_error_response(validation, 409)

    old_status = (c.dispatch_status or "NORMAL").strip().upper()

    if old_status == "PARA_DESPACHO":
        new_status = "DESPACHO_MONTADO"
    elif old_status == "EVACUAR_SOLICITADO":
        new_status = "EVACUACION_MONTADA"
    else:
        return jsonify({
            "ok": False,
            "error": "INVALID_STATUS",
            "message": "Este contenedor no está en un estado válido para montar.",
            "dispatch_status": old_status,
        }), 409

    current_pos = ContainerPosition.query.filter_by(
        container_id=c.id
    ).first()

    bay_code = None
    depth_row = None
    tier = None

    if current_pos:
        bay = YardBay.query.get(current_pos.bay_id)
        bay_code = bay.code if bay else None
        depth_row = current_pos.depth_row
        tier = current_pos.tier

    c.dispatch_status = new_status
    c.dispatch_marked_at = datetime.utcnow()
    c.dispatch_marked_by_user_id = current_user.id

    db.session.add(c)

    db.session.add(
        Movement(
            site_id=site_id,
            container_id=c.id,
            movement_type="MOUNT",
            occurred_at=datetime.utcnow(),
            bay_code=bay_code,
            depth_row=depth_row,
            tier=tier,
            created_by_user_id=current_user.id,
            notes=f"CONTAINER_MOUNTED_FROM_{old_status}_TO_{new_status}_POSITION_RELEASED",
        )
    )

    if current_pos:
        db.session.delete(current_pos)

    audit_log(
        current_user.id,
        "CONTAINER_MOUNTED",
        "container",
        c.id,
        {
            "container_code": c.code,
            "old_status": old_status,
            "new_status": new_status,
            "position_released": True,
            "previous_position": {
                "bay_code": bay_code,
                "depth_row": depth_row,
                "tier": tier,
            },
            "site_id": site_id,
        },
    )

    db.session.commit()

    return jsonify({
        "ok": True,
        "container_id": c.id,
        "container_code": c.code,
        "old_status": old_status,
        "dispatch_status": new_status,
        "position_released": True,
        "previous_position": {
            "bay_code": bay_code,
            "depth_row": depth_row,
            "tier": tier,
        },
    })


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

@yard_bp.get("/api/yard/valid-destinations")
@login_required
def api_yard_valid_destinations():
    """
    Devuelve slots realmente válidos para el contenedor seleccionado
    dentro de un bloque completo, sin hacer consultas por cada slot.

    Reglas:
    - slot libre
    - no flotar: N2 requiere N1, N3 requiere N2, etc.
    - acceso sidepick al destino
    - ignora el mismo contenedor seleccionado como bloqueador
    - si el movimiento es hacia afuera o hacia otra estiba, valida salida del origen
    - si el contenedor tiene otro encima, no devuelve destinos
    """

    site_id = _ensure_active_site()

    container_id_raw = request.args.get("container_id")
    block_code = (request.args.get("block") or "").strip().upper()

    if not container_id_raw:
        return jsonify({
            "ok": False,
            "error": "CONTAINER_REQUIRED",
            "message": "Debe indicar el contenedor seleccionado.",
            "destinations": [],
        }), 400

    if not block_code:
        return jsonify({
            "ok": False,
            "error": "BLOCK_REQUIRED",
            "message": "Debe indicar el bloque.",
            "destinations": [],
        }), 400

    try:
        container_id = int(container_id_raw)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "INVALID_CONTAINER_ID",
            "message": "El ID del contenedor no es válido.",
            "destinations": [],
        }), 400

    container = Container.query.get(container_id)

    if not container or not container.is_in_yard or container.site_id != site_id:
        return jsonify({
            "ok": False,
            "error": "CONTAINER_NOT_FOUND",
            "message": "El contenedor no existe o no está en el predio actual.",
            "destinations": [],
        }), 404

    current_pos = ContainerPosition.query.filter_by(
        container_id=container.id
    ).first()

    # =========================
    # VALIDAR BLOQUEO VERTICAL DEL ORIGEN
    # =========================
    if current_pos:
        vertical_blockers = _get_vertical_blockers(
            bay_id=current_pos.bay_id,
            depth_row=current_pos.depth_row,
            tier=current_pos.tier,
            site_id=site_id,
        )

        if vertical_blockers:
            return jsonify({
                "ok": True,
                "container_id": container.id,
                "block": block_code,
                "destinations": [],
                "blocked_origin": True,
                "error": "CONTAINER_BLOCKED_VERTICAL",
                "message": "El contenedor no puede moverse porque tiene otro contenedor encima.",
                "blockers": [
                    {
                        **item,
                        "reason": "VERTICAL",
                        "message": "Contenedor encima",
                    }
                    for item in vertical_blockers
                ],
            })

    block = YardBlock.query.filter_by(
        site_id=site_id,
        code=block_code,
    ).first()

    if not block:
        return jsonify({
            "ok": False,
            "error": "BLOCK_NOT_FOUND",
            "message": "El bloque indicado no existe en el predio actual.",
            "destinations": [],
        }), 404

    bays = (
        YardBay.query
        .filter_by(
            site_id=site_id,
            block_id=block.id,
            is_active=True,
        )
        .order_by(YardBay.bay_number.asc())
        .all()
    )

    bay_ids = [b.id for b in bays]

    if not bay_ids:
        return jsonify({
            "ok": True,
            "container_id": container.id,
            "block": block_code,
            "destinations": [],
        })

    origin_bay_id = int(current_pos.bay_id) if current_pos else None
    all_needed_bay_ids = set(bay_ids)

    if origin_bay_id:
        all_needed_bay_ids.add(origin_bay_id)

    occupied_rows = (
        db.session.query(Container, ContainerPosition, YardBay)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(
            YardBay.id.in_(list(all_needed_bay_ids)),
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
            Container.id != container.id,
        )
        .all()
    )

    occupied = {}

    for c, p, bay in occupied_rows:
        occupied.setdefault(int(bay.id), {})
        occupied[int(bay.id)][(int(p.depth_row), int(p.tier))] = {
            "container_id": c.id,
            "container_code": c.code,
            "depth_row": int(p.depth_row),
            "tier": int(p.tier),
        }

    destinations = []

    for bay in bays:
        max_rows = int(bay.max_depth_rows or 0)
        max_tiers = int(bay.max_tiers or 0)
        bay_occ = occupied.get(int(bay.id), {})

        for depth_row in range(1, max_rows + 1):
            for tier in range(1, max_tiers + 1):

                # 1. Slot ocupado
                if (depth_row, tier) in bay_occ:
                    continue

                # 2. No colocar flotando
                if tier > 1 and (depth_row, tier - 1) not in bay_occ:
                    continue

                # 3. Acceso sidepick al destino
                access_blocked = False

                for (occ_row, occ_tier), item in bay_occ.items():
                    if occ_row > depth_row:
                        access_blocked = True
                        break

                if access_blocked:
                    continue

                # 4. Validar salida del origen cuando corresponde
                if current_pos:
                    moving_to_other_bay = int(current_pos.bay_id) != int(bay.id)
                    moving_outward_same_bay = (
                        int(current_pos.bay_id) == int(bay.id)
                        and depth_row > int(current_pos.depth_row)
                    )

                    if moving_to_other_bay or moving_outward_same_bay:
                        origin_occ = occupied.get(int(current_pos.bay_id), {})
                        origin_blocked = False

                        for (occ_row, occ_tier), item in origin_occ.items():
                            if occ_row > int(current_pos.depth_row):
                                origin_blocked = True
                                break

                        if origin_blocked:
                            continue

                destinations.append({
                    "bay_id": bay.id,
                    "bay_code": bay.code,
                    "depth_row": depth_row,
                    "tier": tier,
                })

    return jsonify({
        "ok": True,
        "container_id": container.id,
        "block": block_code,
        "destinations": destinations,
    })

@yard_bp.post("/api/yard/place")
@login_required
def api_place_container():
    """
    Coloca/mueve un contenedor en una estiba.

    Validaciones:
    - contenedor existe y pertenece al predio actual
    - estiba destino existe y pertenece al predio actual
    - no tiene contenedor encima
    - si se mueve hacia afuera o a otra estiba, valida salida del origen
    - destino válido ignorando el mismo contenedor seleccionado
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

    old_pos = ContainerPosition.query.filter_by(
        container_id=c.id
    ).first()

    # =========================
    # VALIDAR BLOQUEO VERTICAL
    # =========================
    if old_pos:
        vertical_blockers = _get_vertical_blockers(
            bay_id=old_pos.bay_id,
            depth_row=old_pos.depth_row,
            tier=old_pos.tier,
            site_id=site_id,
        )

        if vertical_blockers:
            validation = {
                "ok": False,
                "error": "CONTAINER_BLOCKED_VERTICAL",
                "message": "El contenedor no puede moverse porque tiene otro contenedor encima.",
                "blockers": [
                    {
                        **item,
                        "reason": "VERTICAL",
                        "message": "Contenedor encima",
                    }
                    for item in vertical_blockers
                ],
                "current_position": {
                    "bay_id": old_pos.bay_id,
                    "depth_row": old_pos.depth_row,
                    "tier": old_pos.tier,
                },
            }
            return _yard_validation_error_response(validation, 409)

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
    # VALIDAR SALIDA HACIA AFUERA / OTRA ESTIBA
    # =========================
    if old_pos:
        moving_to_other_bay = int(old_pos.bay_id) != int(to_bay.id)
        moving_outward_same_bay = (
            int(old_pos.bay_id) == int(to_bay.id)
            and int(depth_row) > int(old_pos.depth_row)
        )

        if moving_to_other_bay or moving_outward_same_bay:
            origin_access_blockers = _get_sidepick_access_blockers(
                bay_id=old_pos.bay_id,
                depth_row=old_pos.depth_row,
                tier=old_pos.tier,
                site_id=site_id,
                exclude_container_id=c.id,
            )

            if origin_access_blockers:
                validation = {
                    "ok": False,
                    "error": "ORIGIN_NOT_ACCESSIBLE",
                    "message": "La sidepick no puede sacar el contenedor porque hay contenedores bloqueando la salida.",
                    "blockers": [
                        {
                            **item,
                            "reason": "ACCESS",
                            "message": "Bloquea salida del contenedor",
                        }
                        for item in origin_access_blockers
                    ],
                    "current_position": {
                        "bay_id": old_pos.bay_id,
                        "depth_row": old_pos.depth_row,
                        "tier": old_pos.tier,
                    },
                }
                return _yard_validation_error_response(validation, 409)

    # =========================
    # VALIDAR DESTINO
    # =========================
    destination_validation = _validate_container_can_be_placed_at(
        bay_id=to_bay.id,
        depth_row=depth_row,
        tier=tier,
        site_id=site_id,
        exclude_container_id=c.id,
    )

    if not destination_validation.get("ok"):
        return _yard_validation_error_response(destination_validation, 409)

    old = None
    if old_pos:
        old_bay = YardBay.query.get(old_pos.bay_id)
        old = {
            "bay_code": old_bay.code if old_bay else None,
            "depth_row": old_pos.depth_row,
            "tier": old_pos.tier,
        }

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
            "rule": "AUTO_LAST_AVAILABLE_VALIDATED"
            if (to_depth_row is None or to_tier is None)
            else "MANUAL_EXACT_VALIDATED",
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
        exclude_container_id=c.id,
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

@yard_bp.get("/api/yard/free-slots")
@login_required
def api_yard_free_slots():
    """
    Devuelve espacios disponibles reales para Gate In manual.

    Reglas:
    - Estiba activa.
    - Slot libre.
    - No flotar: N2 requiere N1 ocupado.
    - Sidepick debe poder llegar: no debe haber contenedores en filas más hacia afuera.
    - Orden: más adentro primero.
    """
    site_id = _ensure_active_site()

    block_code = (request.args.get("block") or "").strip().upper()
    bay_number_raw = (request.args.get("bay_number") or "").strip()

    if not block_code:
        return jsonify({
            "ok": False,
            "error": "BLOCK_REQUIRED",
            "slots": [],
        }), 400

    try:
        bay_number = int(bay_number_raw)
    except Exception:
        return jsonify({
            "ok": False,
            "error": "INVALID_BAY_NUMBER",
            "slots": [],
        }), 400

    block = YardBlock.query.filter_by(
        site_id=site_id,
        code=block_code,
    ).first()

    if not block:
        return jsonify({
            "ok": False,
            "error": "BLOCK_NOT_FOUND",
            "slots": [],
        }), 404

    bay = YardBay.query.filter_by(
        site_id=site_id,
        block_id=block.id,
        bay_number=bay_number,
        is_active=True,
    ).first()

    if not bay:
        return jsonify({
            "ok": False,
            "error": "BAY_NOT_FOUND",
            "slots": [],
        }), 404

    max_rows = int(bay.max_depth_rows or 0)
    max_tiers = int(bay.max_tiers or 0)

    if max_rows < 1 or max_tiers < 1:
        return jsonify({
            "ok": True,
            "bay_code": bay.code,
            "slots": [],
        })

    occupied_rows = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            ContainerPosition.bay_id == bay.id,
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
        )
        .all()
    )

    occupied = {}

    for c, p in occupied_rows:
        occupied[(int(p.depth_row), int(p.tier))] = {
            "container_id": c.id,
            "container_code": c.code,
            "depth_row": int(p.depth_row),
            "tier": int(p.tier),
        }

    slots = []

    for depth_row in range(max_rows, 0, -1):
        for tier in range(1, max_tiers + 1):

            if (depth_row, tier) in occupied:
                continue

            if tier > 1 and (depth_row, tier - 1) not in occupied:
                continue

            access_blocked = False

            for (occ_row, occ_tier), item in occupied.items():
                if occ_row > depth_row:
                    access_blocked = True
                    break

            if access_blocked:
                continue

            slots.append({
                "bay_id": bay.id,
                "bay_code": bay.code,
                "depth_row": depth_row,
                "tier": tier,
                "label": f"{bay.code} · F{str(depth_row).zfill(2)} · N{tier}",
            })

    return jsonify({
        "ok": True,
        "bay_id": bay.id,
        "bay_code": bay.code,
        "max_depth_rows": max_rows,
        "max_tiers": max_tiers,
        "slots": slots,
    })