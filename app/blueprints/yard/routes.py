# app/blueprints/yard/routes.py
import re
from datetime import datetime
import pytz
from sqlalchemy import text, bindparam
import json

from flask import (
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort,
)
from flask_login import login_required, current_user

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.site import Site, UserSite
from app.models.tire import Tire
from app.models.tire_retread_event import TireRetreadEvent


CR_TZ = pytz.timezone("America/Costa_Rica")
UTC_TZ = pytz.utc

CONTAINER_RE = re.compile(r"^[A-Z]{4}-\d{6}-\d$")

SIZES = [
    "40HC",
    "40ST",
    "40RF",
    "40OT",
    "20ST",
    "20OT",
    "20RF",
    "20TQ",
    "45HC",
]

APP_NAME = "Yard Gate Álamo"

REPORT_TYPES = {"GATE_IN", "GATE_OUT", "MOVE"}

CHASSIS_NUM_RE = re.compile(r"^\d{5}$")

TIRE_STATES = {"OK", "GASTADA", "PINCHADA", "CAMBIAR", "NO_APTA"}

PREDIO_CODES = {"COYOL", "CALDERA", "LIMON"}

CHASSIS_STATUSES = {"BUENO", "DAÑADO", "FUERA_DE_SERVICIO", "ATADO"}
CHASSIS_KINDS = {"CHASIS", "LOW_BOY", "TANQUETA", "PLANA", "CARRETA"}

MARCHAMO_CHECK = {"OK", "DISTINTO", "NO_TIENE", "ILEGIBLE"}

SIDE_TO_POSITION = {
    "AX1_L": "AX1_L_OUT",
    "AX1_R": "AX1_R_OUT",
    "AX2_L": "AX2_L_OUT",
    "AX2_R": "AX2_R_OUT",
    "AX3_L": "AX3_L_OUT",
    "AX3_R": "AX3_R_OUT",
}

POSITION_TO_SIDE = {v: k for k, v in SIDE_TO_POSITION.items()}


# =========================
# Multi-predio helpers
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

def _get_axle_seals_from_chassis_tires(chassis_id: int):
    sql = text("""
        SELECT
            position_code,
            marchamo
        FROM yard_gate_alamo.chassis_tires
        WHERE chassis_id = :chassis_id
          AND marchamo IS NOT NULL
          AND TRIM(marchamo) <> ''
        ORDER BY position_code ASC
    """)

    rows = db.session.execute(
        sql,
        {"chassis_id": chassis_id}
    ).mappings().all()

    grouped = {}

    for r in rows:
        pos = (r["position_code"] or "").strip().upper()
        marchamo = _normalize_seal_value(r["marchamo"])

        if not marchamo:
            continue

        side_code = None

        if pos.startswith("AX1_L_"):
            side_code = "AX1_L"
        elif pos.startswith("AX1_R_"):
            side_code = "AX1_R"
        elif pos.startswith("AX2_L_"):
            side_code = "AX2_L"
        elif pos.startswith("AX2_R_"):
            side_code = "AX2_R"
        elif pos.startswith("AX3_L_"):
            side_code = "AX3_L"
        elif pos.startswith("AX3_R_"):
            side_code = "AX3_R"

        if not side_code:
            continue

        grouped.setdefault(side_code, []).append(marchamo)

    result = {}

    for side_code, seals in grouped.items():
        result[side_code] = {
            "seal_1": seals[0] if len(seals) > 0 else "",
            "seal_2": seals[1] if len(seals) > 1 else "",
        }

    return result

@yard_bp.app_context_processor
def inject_active_site():
    return {"active_site_key": _active_site_key()}


def _is_predio_site(site_id: int) -> bool:
    s = Site.query.get(site_id)
    return bool(s and (s.code or "").upper() in PREDIO_CODES)


# =========================
# Rutas base / predios / mapa
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
    else:
        _ensure_active_site()

    return render_template("home.html")


@yard_bp.get("/map")
@login_required
def map_view():
    site_id = _ensure_active_site()

    blocks = (
        YardBlock.query
        .filter_by(
            site_id=site_id,
            is_active=True,
        )
        .order_by(YardBlock.code.asc())
        .all()
    )

    selected_block = (request.args.get("block") or "").strip().upper()

    valid_block_codes = {b.code for b in blocks}

    if not selected_block or selected_block not in valid_block_codes:
        selected_block = blocks[0].code if blocks else ""

    blocks_json = [
        {
            "id": b.id,
            "code": b.code,
            "site_id": b.site_id,
        }
        for b in blocks
    ]

    return render_template(
        "yard/map.html",
        blocks=blocks,
        blocks_json=blocks_json,
        selected_block=selected_block,
    )


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
# Helpers generales
# =========================
def _norm_enum(val):
    return (val or "").strip().upper().replace(" ", "_")


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


def _calc_tire_state_from_data(estrias_mm, is_flat=False):
    if is_flat:
        return "PINCHADA"

    if estrias_mm in (None, ""):
        return "OK"

    try:
        mm = int(estrias_mm)
    except Exception:
        return "OK"

    if 9 <= mm <= 12:
        return "OK"
    if 4 <= mm <= 8:
        return "GASTADA"
    if 1 <= mm <= 3:
        return "NO_APTA"

    return "OK"


def _calc_tire_state_from_mm(estrias_mm, is_flat=False):
    return _calc_tire_state_from_data(estrias_mm, is_flat)


def _normalize_tire_status(value: str | None) -> str:
    v = (value or "").strip().upper()
    allowed = {"ASIGNADA", "EN_TALLER_BODEGA", "RECAUCHE", "DESECHADA"}
    return v if v in allowed else "EN_TALLER_BODEGA"


def _translate_tire_position(position_code: str | None) -> str:
    value = (position_code or "").strip().upper()

    mapping = {
        "AX1_L_IN": "Eje 1 izq int",
        "AX1_L_OUT": "Eje 1 izq ext",
        "AX1_R_IN": "Eje 1 der int",
        "AX1_R_OUT": "Eje 1 der ext",
        "AX2_L_IN": "Eje 2 izq int",
        "AX2_L_OUT": "Eje 2 izq ext",
        "AX2_R_IN": "Eje 2 der int",
        "AX2_R_OUT": "Eje 2 der ext",
        "AX3_L_IN": "Eje 3 izq int",
        "AX3_L_OUT": "Eje 3 izq ext",
        "AX3_R_IN": "Eje 3 der int",
        "AX3_R_OUT": "Eje 3 der ext",
    }
    return mapping.get(value, value)


def _normalize_structure_status_for_db(value: str | None) -> str | None:
    v = (value or "").strip().upper()

    mapping = {
        "OK": "OK",
        "DANO_LEVE": "GOLPE",
        "DAÑO_LEVE": "GOLPE",
        "DANO_GRAVE": "DOBLADO",
        "DAÑO_GRAVE": "DOBLADO",
        "GOLPE": "GOLPE",
        "DOBLADO": "DOBLADO",
        "SOLDADURA": "SOLDADURA",
        "DAÑADO": "DOBLADO",
        "DANADO": "DOBLADO",
        "FUERA_DE_SERVICIO": "DOBLADO",
        "ATADO": "SOLDADURA",
    }

    return mapping.get(v, "OK" if v else None)


def _normalize_twistlocks_status_for_db(value: str | None) -> str | None:
    v = (value or "").strip().upper()

    mapping = {
        "OK": "BIEN",
        "BIEN": "BIEN",
        "DANO_LEVE": "DANADOS",
        "DAÑO_LEVE": "DANADOS",
        "DANO_GRAVE": "DANADOS",
        "DAÑO_GRAVE": "DANADOS",
        "DAÑADO": "DANADOS",
        "DANADO": "DANADOS",
        "FUERA_DE_SERVICIO": "DANADOS",
        "ATADO": "DANADOS",
        "DANADOS": "DANADOS",
        "DAÑADOS": "DANADOS",
    }

    return mapping.get(v, "BIEN" if v else None)


def _normalize_landing_gear_status_for_db(value: str | None) -> str | None:
    v = (value or "").strip().upper()

    mapping = {
        "OK": "OK",
        "DANADAS": "DANADAS",
        "DAÑADAS": "DANADAS",
        "DANO_LEVE": "DANADAS",
        "DAÑO_LEVE": "DANADAS",
        "DANO_GRAVE": "DANADAS",
        "DAÑO_GRAVE": "DANADAS",
        "DAÑADO": "DANADAS",
        "DANADO": "DANADAS",
        "FUERA_DE_SERVICIO": "DANADAS",
        "ATADO": "DANADAS",
    }

    return mapping.get(v, "OK" if v else None)


def _normalize_lights_status_for_db(value: str | None) -> str | None:
    v = (value or "").strip().upper()

    mapping = {
        "OK": "OK",
        "UNA_DANADA": "IZQ_DANADA",
        "UNA_DAÑADA": "IZQ_DANADA",
        "AMBAS_DANADAS": "IZQ_DANADA",
        "AMBAS_DAÑADAS": "IZQ_DANADA",
        "IZQ_DANADA": "IZQ_DANADA",
        "IZQ_DAÑADA": "IZQ_DANADA",
        "DER_DANADA": "DER_DANADA",
        "DER_DAÑADA": "DER_DANADA",
    }

    return mapping.get(v, "OK" if v else None)


def _normalize_mudflap_status_for_db(value: str | None) -> str | None:
    v = (value or "").strip().upper()

    mapping = {
        "OK": "OK",
        "NO_TRAE": "NO_TRAE",
        "DANADO": "NO_TRAE",
        "DAÑADO": "NO_TRAE",
        "FUERA_DE_SERVICIO": "NO_TRAE",
        "ATADO": "NO_TRAE",
    }

    return mapping.get(v, "OK" if v else None)


def _normalize_position_for_tire_master(pos: str) -> str | None:
    value = (pos or "").strip().upper()

    m_simple = re.match(r"^A([1-3])_(IN|OUT)$", value)
    if m_simple:
        return value

    m = re.match(r"^AX([1-3])_(L|R)_(IN|OUT)$", value)
    if not m:
        return None

    axle = m.group(1)
    inout = m.group(3)
    return f"A{axle}_{inout}"


def _resolve_tire_position_id(axles: int, pos: str) -> int:
    master_pos = _normalize_position_for_tire_master(pos)
    if not master_pos:
        raise ValueError(f"Posición inválida para tire_positions: {pos}")

    sql = text("""
        SELECT id
        FROM yard_gate_alamo.tire_positions
        WHERE axle_count = :axles
          AND position_code = :position_code
        LIMIT 1
    """)
    row = db.session.execute(sql, {
        "axles": int(axles),
        "position_code": master_pos,
    }).mappings().first()

    if not row:
        raise ValueError(
            f"No existe tire_positions para axle_count={axles}, position_code={master_pos}"
        )

    return int(row["id"])


def _condition_from_tire_states(states: list[str]) -> str:
    normalized = {(x or "").strip().upper() for x in states if x}

    if normalized & {"PINCHADA", "CAMBIAR", "NO_APTA"}:
        return "REPARABLE"
    if "GASTADA" in normalized:
        return "DESGASTADA"
    return "OK"


def _pick_valid_pressure(values: list) -> float | None:
    for v in values:
        try:
            num = float(v)
            if num > 0:
                return num
        except Exception:
            continue
    return None


def _get_table_columns(schema: str, table: str) -> set[str]:
    sql = text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = :schema AND table_name = :table
    """)
    rows = db.session.execute(sql, {"schema": schema, "table": table}).fetchall()
    return {r[0] for r in rows}


def _insert_dynamic(schema: str, table: str, values: dict) -> int | None:
    cols = _get_table_columns(schema, table)
    payload = {k: v for k, v in values.items() if k in cols}

    if not payload:
        return None

    col_list = ", ".join(payload.keys())
    param_list = ", ".join([f":{k}" for k in payload.keys()])

    if "id" in cols:
        sql = text(f"""
            INSERT INTO {schema}.{table} ({col_list})
            VALUES ({param_list})
            RETURNING id
        """)
        new_id = db.session.execute(sql, payload).scalar()
        return int(new_id) if new_id is not None else None

    sql = text(f"""
        INSERT INTO {schema}.{table} ({col_list})
        VALUES ({param_list})
    """)
    db.session.execute(sql, payload)
    return None


def _insert_tire_reading_row(
    *,
    site_id: int,
    chassis_id: int,
    axles: int,
    pos: str,
    event_type: str,
    event_id,
    seal_1,
    seal_2,
    pressure_psi,
    condition: str | None,
    comments: str | None,
    user_id: int,
):
    allowed_event_types = {"GATE_IN", "EIR_OUT", "EIR_IN", "CHASSIS_DETAIL"}
    resolved_event_type = event_type if event_type in allowed_event_types else "GATE_IN"

    tire_position_id = _resolve_tire_position_id(axles, pos)

    resolved_pressure = pressure_psi
    try:
        if resolved_pressure not in (None, ""):
            resolved_pressure = float(resolved_pressure)
        else:
            resolved_pressure = None
    except Exception:
        resolved_pressure = None

    if resolved_event_type == "GATE_IN" and (resolved_pressure is None or resolved_pressure <= 0):
        resolved_pressure = 1.0

    payload = {
        "site_id": site_id,
        "chassis_id": chassis_id,
        "event_type": resolved_event_type,
        "event_id": event_id,
        "tire_position_id": tire_position_id,
        "seal_1": seal_1,
        "seal_2": seal_2,
        "pressure_psi": resolved_pressure,
        "condition": condition,
        "comments": comments,
        "recorded_at": datetime.utcnow(),
        "recorded_by_user_id": user_id,
    }

    _insert_dynamic("yard_gate_alamo", "tire_readings", payload)


def _save_grouped_tire_readings(
    *,
    site_id: int,
    chassis_id: int,
    axles: int,
    grouped_readings: dict,
    user_id: int,
    event_type: str,
    event_id=None,
):
    for master_pos, group in grouped_readings.items():
        states = group.get("states", [])
        pressures = group.get("pressures", [])
        comments_list = group.get("comments", [])
        seal_issue = bool(group.get("seal_issue"))
        second_seal = group.get("seal_2")

        condition = _condition_from_tire_states(states)
        pressure = _pick_valid_pressure(pressures)

        _insert_tire_reading_row(
            site_id=site_id,
            chassis_id=chassis_id,
            axles=axles,
            pos=master_pos,
            event_type=event_type,
            event_id=event_id,
            seal_1="DISTINTO" if seal_issue else None,
            seal_2=second_seal,
            pressure_psi=pressure,
            condition=condition,
            comments=" || ".join(comments_list) if comments_list else None,
            user_id=user_id,
        )


def _get_open_tire_retread_event(tire_id: int):
    return (
        TireRetreadEvent.query
        .filter(
            TireRetreadEvent.tire_id == tire_id,
            TireRetreadEvent.event_status == "SENT",
            TireRetreadEvent.returned_at.is_(None),
        )
        .order_by(TireRetreadEvent.id.desc())
        .first()
    )


def _open_tire_retread_event(
    *,
    tire_id: int,
    previous_estrias_mm,
    previous_marchamo: str | None,
    user_id: int,
    notes: str | None = None,
):
    existing = _get_open_tire_retread_event(tire_id)
    if existing:
        return existing

    event = TireRetreadEvent(
        tire_id=tire_id,
        previous_estrias_mm=previous_estrias_mm,
        previous_marchamo=previous_marchamo or None,
        created_by=user_id,
        created_at=datetime.utcnow(),
        sent_at=datetime.utcnow(),
        sent_by=user_id,
        event_status="SENT",
        notes=notes or None,
    )
    db.session.add(event)
    return event


def _close_tire_retread_event(
    *,
    tire_id: int,
    new_estrias_mm,
    new_marchamo: str | None,
    user_id: int,
    final_status: str,
):
    event = _get_open_tire_retread_event(tire_id)
    if not event:
        return None

    event.new_estrias_mm = new_estrias_mm
    event.new_marchamo = new_marchamo or None
    event.returned_at = datetime.utcnow()
    event.returned_by = user_id
    event.event_status = "SCRAPPED" if final_status == "DESECHADA" else "RETURNED"
    db.session.add(event)
    return event


def _sync_tire_master_state(
    tire: Tire | None,
    *,
    marchamo: str | None,
    estrias_mm,
    is_flat: bool,
    tire_state: str | None,
):
    if not tire:
        return

    tire.last_marchamo = marchamo or None
    tire.last_estrias_mm = estrias_mm
    tire.last_is_flat = bool(is_flat)
    tire.last_tire_state = tire_state or "OK"
    db.session.add(tire)


def _maybe_register_tire_retread(
    *,
    tire_id: int | None,
    previous_estrias_mm,
    new_estrias_mm,
    previous_marchamo: str | None,
    new_marchamo: str | None,
    created_by: int | None,
):
    if not tire_id:
        return

    try:
        old_mm = int(previous_estrias_mm) if previous_estrias_mm not in (None, "") else None
    except Exception:
        old_mm = None

    try:
        new_mm = int(new_estrias_mm) if new_estrias_mm not in (None, "") else None
    except Exception:
        new_mm = None

    if old_mm is None or new_mm is None:
        return

    if not (old_mm <= 4 and new_mm == 12 and old_mm != 12):
        return

    event = TireRetreadEvent(
        tire_id=tire_id,
        previous_estrias_mm=old_mm,
        new_estrias_mm=new_mm,
        previous_marchamo=previous_marchamo or None,
        new_marchamo=new_marchamo or None,
        created_by=created_by,
    )
    db.session.add(event)


def _fetch_last_final_eir_for_chassis(chassis_id: int):
    cols = _get_table_columns("yard_gate_alamo", "eirs")
    status_col = "status" if "status" in cols else None
    updated_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else None)

    where_status = ""
    if status_col:
        where_status = (
            f"AND COALESCE(e.{status_col}, '') "
            f"IN ('CONFIRMED','FINAL','CERRADO','POR COBRAR','PENDIENTE COBRO','ABIERTO','ASIGNADO')"
        )

    order_by = f"ORDER BY e.{updated_col} DESC NULLS LAST, e.id DESC" if updated_col else "ORDER BY e.id DESC"

    sql = text(f"""
        SELECT e.*
        FROM yard_gate_alamo.eirs e
        WHERE e.chassis_id = :cid
        {where_status}
        {order_by}
        LIMIT 1
    """)
    row = db.session.execute(sql, {"cid": chassis_id}).mappings().first()
    return row


def _last_confirmed_eir_destination_by_chassis_ids(chassis_ids: list[int]) -> dict[int, str]:
    if not chassis_ids:
        return {}

    sql = (
        text(
            """
            SELECT DISTINCT ON (chassis_id)
                chassis_id,
                destination
            FROM yard_gate_alamo.eirs
            WHERE chassis_id IN :ids
              AND status = 'CONFIRMED'
            ORDER BY chassis_id, id DESC
            """
        )
        .bindparams(bindparam("ids", expanding=True))
    )

    rows = db.session.execute(sql, {"ids": chassis_ids}).mappings().all()

    out = {}
    for r in rows:
        out[int(r["chassis_id"])] = (r["destination"] or "").strip()

    return out


def _build_workshop_ticket_text(
    chassis_number: str,
    axles: int,
    structure_lines: list[str],
    tire_lines: list[str],
    eir_prev_id: int | None
) -> str:
    out = []
    out.append(f"CHASIS: {chassis_number}")
    out.append(f"EJES: {axles}")

    if eir_prev_id:
        out.append(f"CONCILIAR CONTRA EIR ANTERIOR: #{eir_prev_id}")

    if structure_lines:
        out.append("")
        out.append("DAÑOS / OBSERVACIONES (ESTRUCTURA):")
        out.extend([f"- {x}" for x in structure_lines])

    if tire_lines:
        out.append("")
        out.append("LLANTAS / MARCHAMOS:")
        out.extend([f"- {x}" for x in tire_lines])

    return "\n".join(out).strip()


def _build_chassis_gate_in_ticket_text(
    *,
    site_name: str,
    username: str,
    occurred_at: datetime,
    chassis_number: str,
    plate: str | None,
    structure_status: str | None,
    twistlocks_status: str | None,
    landing_gear_status: str | None,
    lights_status: str | None,
    mudflap_status: str | None,
    plate_validation_status: str | None,
    damage_summary: str | None,
    comments: str | None,
    driver_comments: str | None,
    tire_rows: list,
    alert_lines: list,
):
    def side_label(pos: str) -> str:
        pos = (pos or "").upper()

        if pos.startswith("AX1_L_"):
            return "Eje 1 Izq."
        if pos.startswith("AX1_R_"):
            return "Eje 1 Der."
        if pos.startswith("AX2_L_"):
            return "Eje 2 Izq."
        if pos.startswith("AX2_R_"):
            return "Eje 2 Der."
        if pos.startswith("AX3_L_"):
            return "Eje 3 Izq."
        if pos.startswith("AX3_R_"):
            return "Eje 3 Der."

        return pos or "—"

    def status_txt(value):
        return value or "OK"

    try:
        dt_local = occurred_at.replace(tzinfo=UTC_TZ).astimezone(CR_TZ)
    except Exception:
        dt_local = occurred_at

    grouped_tires = {}

    for row in tire_rows or []:
        pos = (row.get("pos") or "").upper()
        label = side_label(pos)
        estrias = row.get("estrias_mm")

        if estrias in (None, ""):
            continue

        grouped_tires.setdefault(label, []).append(str(estrias))

    lines = []
    lines.append("================================")
    lines.append("YARD GATE ALAMO")
    lines.append("GATE IN CHASIS")
    lines.append(dt_local.strftime("%d/%m/%Y %I:%M %p"))
    if site_name:
        lines.append(site_name)
    lines.append("================================")
    lines.append(f"CHASIS: {chassis_number}")
    lines.append(f"PLACA : {plate or '—'}")
    lines.append("--------------------------------")
    lines.append("INSPECCION")
    lines.append(f"ESTR : {status_txt(structure_status)}")
    lines.append(f"TWIST: {status_txt(twistlocks_status)}")
    lines.append(f"PATAS: {status_txt(landing_gear_status)}")
    lines.append(f"LUCES: {status_txt(lights_status)}")
    lines.append(f"FALD : {status_txt(mudflap_status)}")

    if plate_validation_status:
        lines.append(f"PLACA: {plate_validation_status}")

    if grouped_tires:
        lines.append("--------------------------------")
        lines.append("LLANTAS / ESTRIAS")

        for label in sorted(grouped_tires.keys()):
            values = grouped_tires[label]
            lines.append(f"{label}: {'/'.join(values)} mm")

    if alert_lines:
        lines.append("--------------------------------")
        lines.append("DIFERENCIAS / ALERTAS")
        for item in alert_lines:
            lines.append(str(item)[:36])
    else:
        lines.append("--------------------------------")
        lines.append("SIN DIFERENCIAS")

    if damage_summary:
        lines.append("--------------------------------")
        lines.append("DANOS:")
        lines.append(damage_summary[:80])

    if comments:
        lines.append("--------------------------------")
        lines.append("OBS:")
        lines.append(comments[:80])

    if driver_comments:
        lines.append("--------------------------------")
        lines.append("CHOFER:")
        lines.append(driver_comments[:80])

    lines.append("--------------------------------")
    lines.append(f"USR: {username or '—'}")
    lines.append("================================")

    return "\n".join(lines)

def _normalize_seal_value(value):
    if value is None:
        return ""

    return (
        str(value)
        .strip()
        .upper()
        .replace(" ", "")
        .replace("-", "")
        .replace("\t", "")
        .replace("\n", "")
        .replace("\r", "")
    )


def _normalize_seal_pair(seal_1, seal_2):
    values = [
        _normalize_seal_value(seal_1),
        _normalize_seal_value(seal_2),
    ]
    values = [v for v in values if v]
    return sorted(values)


def _parse_axle_seals_payload(raw_value):
    """
    Espera JSON tipo:
    {
        "AX1_L": {"seal_1": "ABC", "seal_2": "XYZ"},
        "AX1_R": {"seal_1": "123", "seal_2": "456"}
    }
    """
    if not raw_value:
        return {}

    try:
        data = json.loads(raw_value)
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    result = {}

    for side_code, item in data.items():
        side_code = (side_code or "").strip().upper()

        if side_code not in SIDE_TO_POSITION:
            continue

        item = item or {}
        seal_1 = _normalize_seal_value(item.get("seal_1"))
        seal_2 = _normalize_seal_value(item.get("seal_2"))

        if not seal_1 and not seal_2:
            continue

        result[side_code] = {
            "seal_1": seal_1,
            "seal_2": seal_2,
        }

    return result


def _get_axle_seals_for_event(*, chassis_id: int, event_type: str, event_id=None):
    where_event_id = ""
    params = {
        "chassis_id": chassis_id,
        "event_type": event_type,
    }

    if event_id is None:
        where_event_id = "AND tr.event_id IS NULL"
    else:
        where_event_id = "AND tr.event_id = :event_id"
        params["event_id"] = event_id

    sql = text(f"""
        SELECT
            tp.position_code,
            tr.seal_1,
            tr.seal_2,
            tr.comments,
            tr.recorded_at,
            tr.id
        FROM yard_gate_alamo.tire_readings tr
        JOIN yard_gate_alamo.tire_positions tp
          ON tp.id = tr.tire_position_id
        WHERE tr.chassis_id = :chassis_id
          AND tr.event_type = :event_type
          {where_event_id}
        ORDER BY tr.recorded_at DESC NULLS LAST,
                 tr.id DESC
    """)

    rows = db.session.execute(sql, params).mappings().all()

    result = {}

    for r in rows:

        comments = (r["comments"] or "").strip().upper()

        side_code = None

        m = re.search(r"(AX[1-3]_[LR])", comments)

        if m:
            side_code = m.group(1)

        if not side_code:
            continue

        if side_code in result:
            continue

        result[side_code] = {
            "seal_1": _normalize_seal_value(r["seal_1"]),
            "seal_2": _normalize_seal_value(r["seal_2"]),
        }

    return result


def _save_axle_seals_for_event(
    *,
    site_id: int,
    chassis_id: int,
    axles: int,
    seals_payload: dict,
    event_type: str,
    event_id,
    user_id: int,
):
    """
    Guarda marchamos por eje/lado usando tire_readings.
    Borra lecturas anteriores del mismo evento para evitar duplicados.
    """
    if not seals_payload:
        return

    delete_event_id = "IS NULL" if event_id is None else "= :event_id"
    params = {
        "chassis_id": chassis_id,
        "event_type": event_type,
    }
    if event_id is not None:
        params["event_id"] = event_id

    db.session.execute(text(f"""
        DELETE FROM yard_gate_alamo.tire_readings
        WHERE chassis_id = :chassis_id
          AND event_type = :event_type
          AND event_id {delete_event_id}
    """), params)

    for side_code, item in seals_payload.items():
        side_code = (side_code or "").strip().upper()
        if side_code not in SIDE_TO_POSITION:
            continue

        position_code = SIDE_TO_POSITION[side_code]

        _insert_tire_reading_row(
            site_id=site_id,
            chassis_id=chassis_id,
            axles=axles,
            pos=position_code,
            event_type=event_type,
            event_id=event_id,
            seal_1=_normalize_seal_value(item.get("seal_1")),
            seal_2=_normalize_seal_value(item.get("seal_2")),
            pressure_psi=None,
            condition="OK",
            comments=f"MARCHAMOS POR EJE/LADO {side_code}",
            user_id=user_id,
        )


def _compare_axle_seals(expected: dict, scanned: dict):
    """
    Compara marchamos por eje/lado sin importar orden, espacios, guiones
    ni diferencias de formato.

    Solo marca diferencia si el conjunto real de números cambia.
    """
    differences = []

    expected = expected or {}
    scanned = scanned or {}

    for side in sorted(set(expected.keys()) | set(scanned.keys())):
        exp_row = expected.get(side) or {}
        scn_row = scanned.get(side) or {}

        expected_values = []
        scanned_values = []

        for key in ("seal_1", "seal_2"):
            v = _normalize_seal_value(exp_row.get(key))
            if v:
                expected_values.append(v)

        for key in ("seal_1", "seal_2"):
            v = _normalize_seal_value(scn_row.get(key))
            if v:
                scanned_values.append(v)

        expected_set = set(expected_values)
        scanned_set = set(scanned_values)

        if expected_set != scanned_set:
            differences.append({
                "side": side,
                "expected": sorted(expected_set),
                "scanned": sorted(scanned_set),
                "missing": sorted(expected_set - scanned_set),
                "extra": sorted(scanned_set - expected_set),
            })

    return differences


def _format_axle_seal_difference_lines(differences):
    labels = {
        "AX1_L": "Eje 1 Izq",
        "AX1_R": "Eje 1 Der",
        "AX2_L": "Eje 2 Izq",
        "AX2_R": "Eje 2 Der",
        "AX3_L": "Eje 3 Izq",
        "AX3_R": "Eje 3 Der",
    }

    lines = []

    for d in differences:
        side = d.get("side") or d.get("side_code")
        label = labels.get(side, side or "Eje desconocido")

        scanned = d.get("scanned") or []

        scanned_clean = [
            _normalize_seal_value(x)
            for x in scanned
            if _normalize_seal_value(x)
        ]

        scanned_txt = (
            " / ".join(scanned_clean)
            if scanned_clean
            else "NO INGRESADO"
        )

        lines.append(f"! {label}")
        lines.append("MARCHAMOS NO COINCIDEN")

        if scanned_txt:
            lines.append(f"ESCANEADO:")
            lines.append(scanned_txt)

    return lines

def _enqueue_print_job(
    *,
    payload_text: str,
    requested_by: str | None = None,
    request_origin: str = "GATE_IN",
    ticket_id: int | None = None,
):
    """
    Inserta un trabajo pendiente en yard_gate_alamo.print_jobs.
    El agente local de impresión debe tomar los registros PENDING.
    """
    return _insert_dynamic("yard_gate_alamo", "print_jobs", {
        "created_at": datetime.utcnow(),
        "status": "PENDING",
        "ticket_id": ticket_id,
        "payload_text": payload_text,
        "requested_by": requested_by or None,
        "request_origin": request_origin,
        "attempts": 0,
    })


def _build_merchant_gate_in_ticket_text(
    *,
    site_name: str,
    username: str,
    occurred_at: datetime,
    container_code: str,
    container_size: str | None,
    bay_code: str | None,
    depth_row,
    tier,
    driver_name: str,
    driver_id_doc: str,
    truck_plate: str,
    shipping_line: str | None,
    max_gross_kg,
    tare_kg,
    manufacture_year,
    summary_text: str | None,
    classification_notes: str | None,
):
    """
    Ticket compacto para ingreso Merchant:
    contenedor sin chasis ATM.
    """
    try:
        dt_local = occurred_at.replace(tzinfo=UTC_TZ).astimezone(CR_TZ)
    except Exception:
        dt_local = occurred_at

    location = "—"
    if bay_code:
        location = f"{bay_code} F{str(depth_row).zfill(2) if depth_row else '—'} N{tier or '—'}"

    lines = []
    lines.append("================================")
    lines.append("YARD GATE ALAMO")
    lines.append("INGRESO MERCHANT")
    lines.append(dt_local.strftime("%d/%m/%Y %I:%M %p"))
    if site_name:
        lines.append(site_name)
    lines.append("================================")
    lines.append(f"CONT: {container_code}")
    lines.append(f"TAM : {container_size or '—'}")
    lines.append(f"NAV : {shipping_line or '—'}")
    lines.append(f"UBIC: {location}")

    if max_gross_kg is not None:
        lines.append(f"MG  : {max_gross_kg} KG")

    if tare_kg is not None:
        lines.append(f"TARA: {tare_kg} KG")

    if manufacture_year:
        lines.append(f"ANIO: {manufacture_year}")

    lines.append("--------------------------------")
    lines.append("CHOFER")
    lines.append(f"NOM : {driver_name}")
    lines.append(f"ID  : {driver_id_doc}")
    lines.append(f"PLACA: {truck_plate}")

    if summary_text:
        lines.append("--------------------------------")
        lines.append("CLASIF:")
        lines.append(summary_text[:180])

    if classification_notes:
        lines.append("--------------------------------")
        lines.append("OBS:")
        lines.append(classification_notes[:180])

    lines.append("--------------------------------")
    lines.append(f"USR : {username or '—'}")
    lines.append("================================")
    lines.append("FIRMA CHOFER")
    lines.append("")
    lines.append("________________________")
    lines.append("")
    lines.append("NOMBRE CHOFER")
    lines.append("")
    lines.append("________________________")
    lines.append("")
    lines.append("FIRMA ATM")
    lines.append("")
    lines.append("________________________")
    lines.append("================================")

    return "\n".join(lines)

def _send_ticket_to_print_agent(payload_text: str) -> bool:
    import requests

    try:
        resp = requests.post(
            "http://192.168.80.123:9109/print",
            json={"payload": payload_text},
            timeout=3,
        )
        return resp.status_code == 200
    except Exception:
        return False

def _is_admin_user():
    return current_user.is_authenticated and (current_user.role or "").lower() == "admin"


def _require_admin():
    if not _is_admin_user():
        abort(403)


def _bay_has_positions_outside_limits(bay_id: int, max_depth_rows: int, max_tiers: int):
    return (
        ContainerPosition.query
        .filter(
            ContainerPosition.bay_id == bay_id,
            db.or_(
                ContainerPosition.depth_row > max_depth_rows,
                ContainerPosition.tier > max_tiers,
            )
        )
        .first()
    )


@yard_bp.get("/map/config")
@login_required
def map_config_view():
    _require_admin()
    site_id = _ensure_active_site()

    blocks = (
        YardBlock.query
        .filter_by(site_id=site_id)
        .order_by(YardBlock.code.asc())
        .all()
    )

    bays = (
        YardBay.query
        .filter_by(site_id=site_id)
        .order_by(YardBay.code.asc(), YardBay.bay_number.asc())
        .all()
    )

    counts = dict(
        db.session.query(
            ContainerPosition.bay_id,
            db.func.count(ContainerPosition.container_id)
        )
        .join(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(YardBay.site_id == site_id)
        .group_by(ContainerPosition.bay_id)
        .all()
    )

    return render_template(
        "yard/map_config.html",
        blocks=blocks,
        bays=bays,
        counts=counts,
    )

@yard_bp.post("/map/config/block/<int:block_id>/toggle")
@login_required
def map_config_toggle_block(block_id):
    _require_admin()
    site_id = _ensure_active_site()

    block = YardBlock.query.filter_by(
        id=block_id,
        site_id=site_id,
    ).first_or_404()

    if block.is_active:
        has_containers = (
            db.session.query(ContainerPosition)
            .join(YardBay, YardBay.id == ContainerPosition.bay_id)
            .filter(
                YardBay.block_id == block.id,
                YardBay.site_id == site_id,
            )
            .first()
        )

        if has_containers:
            flash(
                f"No se puede desactivar el bloque {block.code} porque tiene contenedores ubicados.",
                "danger",
            )
            return redirect(url_for("yard.map_config_view"))

    block.is_active = not block.is_active
    db.session.commit()

    estado = "activado" if block.is_active else "desactivado"
    flash(f"Bloque {block.code} {estado} correctamente.", "success")
    return redirect(url_for("yard.map_config_view"))

@yard_bp.post("/map/config/block/create")
@login_required
def map_config_create_block():
    _require_admin()
    site_id = _ensure_active_site()

    code = (request.form.get("code") or "").strip().upper()

    if not code or len(code) > 1:
        flash("El código del bloque debe ser una sola letra. Ejemplo: A, B, C.", "danger")
        return redirect(url_for("yard.map_config_view"))

    exists = YardBlock.query.filter_by(site_id=site_id, code=code).first()
    if exists:
        flash(f"El bloque {code} ya existe en este predio.", "warning")
        return redirect(url_for("yard.map_config_view"))

    block = YardBlock(
        site_id=site_id,
        code=code,
    )
    db.session.add(block)
    db.session.commit()

    flash(f"Bloque {code} creado correctamente.", "success")
    return redirect(url_for("yard.map_config_view"))


@yard_bp.post("/map/config/bay/create")
@login_required
def map_config_create_bay():
    _require_admin()
    site_id = _ensure_active_site()

    block_id_raw = request.form.get("block_id")
    bay_number_raw = request.form.get("bay_number")
    bay_side = (request.form.get("bay_side") or "").strip().upper()
    container_size_type = (request.form.get("container_size_type") or "40").strip()
    max_depth_rows_raw = request.form.get("max_depth_rows")
    max_tiers_raw = request.form.get("max_tiers")

    try:
        block_id = int(block_id_raw)
        bay_number = int(bay_number_raw)
        max_depth_rows = int(max_depth_rows_raw)
        max_tiers = int(max_tiers_raw)
    except Exception:
        flash("Datos inválidos para crear la estiba.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if bay_number < 1:
        flash("El número de estiba debe ser mayor a 0.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if bay_side not in {"", "E", "S"}:
        flash("El lado de estiba debe ser Entrada, Salida o Sin lado.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if container_size_type not in {"20", "40"}:
        flash("El tipo de estiba debe ser 20 o 40.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if max_depth_rows < 1 or max_tiers < 1:
        flash("Filas y niveles deben ser mayores a 0.", "danger")
        return redirect(url_for("yard.map_config_view"))

    block = YardBlock.query.filter_by(
        id=block_id,
        site_id=site_id,
    ).first()

    if not block:
        flash("Bloque inválido para este predio.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if bay_side:
        bay_code = f"{block.code}{bay_number}{bay_side}"
    else:
        bay_code = f"{block.code}{str(bay_number).zfill(2)}"

    if len(bay_code) > 3:
        flash(
            "El código de estiba supera el máximo permitido de 3 caracteres. "
            "Para usar Entrada/Salida, utiliza números de estiba del 1 al 9.",
            "danger",
        )
        return redirect(url_for("yard.map_config_view"))

    exists = YardBay.query.filter_by(
        site_id=site_id,
        code=bay_code,
    ).first()

    if exists:
        flash(f"La estiba {bay_code} ya existe en este predio.", "warning")
        return redirect(url_for("yard.map_config_view"))

    bay = YardBay(
        site_id=site_id,
        block_id=block.id,
        bay_number=bay_number,
        code=bay_code,
        max_depth_rows=max_depth_rows,
        max_tiers=max_tiers,
        container_size_type=container_size_type,
        x=0,
        y=0,
        w=50,
        h=50,
        is_active=True,
    )

    db.session.add(bay)
    db.session.commit()

    flash(f"Estiba {bay_code} para contenedores {container_size_type} creada correctamente.", "success")
    return redirect(url_for("yard.map_config_view"))

@yard_bp.post("/map/config/bay/<int:bay_id>/update")
@login_required
def map_config_update_bay(bay_id):
    _require_admin()
    site_id = _ensure_active_site()

    bay = YardBay.query.filter_by(
        id=bay_id,
        site_id=site_id,
    ).first_or_404()

    container_size_type = (request.form.get("container_size_type") or bay.container_size_type or "40").strip()

    try:
        max_depth_rows = int(request.form.get("max_depth_rows"))
        max_tiers = int(request.form.get("max_tiers"))
    except Exception:
        flash("Filas o niveles inválidos.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if container_size_type not in {"20", "40"}:
        flash("El tipo de estiba debe ser 20 o 40.", "danger")
        return redirect(url_for("yard.map_config_view"))

    if max_depth_rows < 1 or max_tiers < 1:
        flash("Filas y niveles deben ser mayores a 0.", "danger")
        return redirect(url_for("yard.map_config_view"))

    blocked_position = _bay_has_positions_outside_limits(
        bay.id,
        max_depth_rows,
        max_tiers,
    )

    if blocked_position:
        flash(
            f"No se puede reducir {bay.code}. Hay contenedores ubicados fuera del nuevo rango permitido.",
            "danger",
        )
        return redirect(url_for("yard.map_config_view"))

    has_containers = ContainerPosition.query.filter_by(
        bay_id=bay.id
    ).first()

    if has_containers and container_size_type != bay.container_size_type:
        flash(
            f"No se puede cambiar el tipo de {bay.code} porque ya tiene contenedores posicionados.",
            "danger",
        )
        return redirect(url_for("yard.map_config_view"))

    bay.max_depth_rows = max_depth_rows
    bay.max_tiers = max_tiers
    bay.container_size_type = container_size_type

    db.session.commit()

    flash(f"Estiba {bay.code} actualizada correctamente.", "success")
    return redirect(url_for("yard.map_config_view"))


@yard_bp.post("/map/config/bay/<int:bay_id>/toggle")
@login_required
def map_config_toggle_bay(bay_id):
    _require_admin()
    site_id = _ensure_active_site()

    bay = YardBay.query.filter_by(
        id=bay_id,
        site_id=site_id,
    ).first_or_404()

    if bay.is_active:
        has_containers = ContainerPosition.query.filter_by(
            bay_id=bay.id
        ).first()

        if has_containers:
            flash(f"No se puede desactivar {bay.code} porque tiene contenedores posicionados.", "danger")
            return redirect(url_for("yard.map_config_view"))

    bay.is_active = not bay.is_active
    db.session.commit()

    estado = "activada" if bay.is_active else "desactivada"
    flash(f"Estiba {bay.code} {estado} correctamente.", "success")
    return redirect(url_for("yard.map_config_view"))