# app/blueprints/inventory/routes.py

import os
from io import BytesIO
from flask_login import login_required, current_user

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment

from sqlalchemy import text, bindparam

from app.extensions import db
from app.blueprints.inventory import inventory_bp
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay
from app.models.movement import Movement, MovementPhoto
from app.models.site import Site, UserSite
from flask import render_template, request, send_file, session, abort, redirect, url_for, flash
from datetime import datetime
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import PatternFill
from app.models.container_classification import ContainerClassification
from app.services.audit import audit_log


# =========================================================
# Utilidades
# =========================================================

def _normalize_public_url(raw: str) -> str | None:
    if not raw:
        return None

    raw = str(raw).strip()

    if not (raw.startswith("http://") or raw.startswith("https://")):
        return None

    public_base = (os.environ.get("R2_PUBLIC_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not public_base:
        return raw

    bucket = os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET") or ""
    if bucket and f"/{bucket}/" in raw:
        key = raw.split(f"/{bucket}/", 1)[1]
        return f"{public_base}/{key}"

    return raw


# =========================================================
# Multi-predio helpers
# =========================================================

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


# =========================================================
# Query principal de inventario
# =========================================================

def _inventory_query(site_id: int, in_yard: str | None, qtext: str):

    # Inventario por defecto = solo lo que está en patio
    in_yard = (in_yard or "1").strip()

    q = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(ContainerPosition, ContainerPosition.container_id == Container.id)
        .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(Container.site_id == site_id)
    )

    if in_yard == "1":
        q = q.filter(Container.is_in_yard == True)  # noqa: E712

    elif in_yard == "0":
        q = q.filter(Container.is_in_yard == False)  # noqa: E712

    if qtext:
        q = q.filter(db.func.upper(Container.code).like(f"%{qtext}%"))

    return q.order_by(Container.updated_at.desc())


# =========================================================
# Clasificación más reciente por contenedor
# =========================================================

def _last_classification_by_container_ids(container_ids: list[int]) -> dict[int, dict]:

    if not container_ids:
        return {}

    sql = (
        text(
            """
            SELECT DISTINCT ON (container_id)
                container_id,
                shipping_line,
                max_gross_kg,
                manufacture_year,
                summary_text,
                classified_at
            FROM yard_gate_alamo.container_classifications
            WHERE container_id IN :ids
            ORDER BY container_id, classified_at DESC
            """
        )
        .bindparams(bindparam("ids", expanding=True))
    )

    rows = db.session.execute(sql, {"ids": container_ids}).mappings().all()

    return {int(r["container_id"]): dict(r) for r in rows}


# =========================================================
# Último EIR por contenedor
# =========================================================

def _last_eir_trip_date_by_container_ids(container_ids: list[int]) -> dict[int, dict]:

    if not container_ids:
        return {}

    sql = (
        text(
            """
            SELECT DISTINCT ON (container_id)
                container_id,
                trip_date
            FROM yard_gate_alamo.eirs
            WHERE container_id IN :ids
              AND trip_date IS NOT NULL
            ORDER BY container_id, id DESC
            """
        )
        .bindparams(bindparam("ids", expanding=True))
    )

    rows = db.session.execute(sql, {"ids": container_ids}).mappings().all()

    return {int(r["container_id"]): dict(r) for r in rows}


# =========================================================
# Pantalla principal inventario
# =========================================================

@inventory_bp.get("/inventory")
@login_required
def inventory_index():

    site_id = _ensure_active_site()

    # Inventario por defecto = solo en patio
    in_yard = (request.args.get("in_yard") or "1").strip()

    qtext = (request.args.get("q") or "").strip().upper()

    rows = _inventory_query(site_id, in_yard, qtext).all()

    container_ids = [c.id for c, _, _ in rows]

    cls_by_container = _last_classification_by_container_ids(container_ids)
    eir_by_container = _last_eir_trip_date_by_container_ids(container_ids)

    items = []

    for c, pos, bay in rows:

        cls = cls_by_container.get(c.id)
        eir = eir_by_container.get(c.id)

        items.append(
            {
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "year": (cls.get("manufacture_year") if cls else c.year),
                "shipping_line": (cls.get("shipping_line") if cls else ""),
                "max_gross_kg": (cls.get("max_gross_kg") if cls else ""),
                "eir_trip_date": (eir.get("trip_date") if eir else None),
                "is_in_yard": bool(c.is_in_yard),
                "evacuation_destination": c.evacuation_destination,
                "evacuation_type": c.evacuation_type,
                "evacuation_notes": c.evacuation_notes,

                # Estado operativo real del contenedor
                "dispatch_status": c.dispatch_status or "NORMAL",

                "status_notes": (cls.get("summary_text") if cls else (c.status_notes or "")),
                "position": None
                if not pos
                else {
                    "bay_code": bay.code if bay else None,
                    "depth_row": pos.depth_row,
                    "tier": pos.tier,
                },
            }
        )

    return render_template(
        "inventory/index.html",
        items=items,
        in_yard=in_yard,
        q=qtext,
    )


# =========================================================
# Exportar inventario
# =========================================================

@inventory_bp.get("/inventory/export")
@login_required
def inventory_export():

    site_id = _ensure_active_site()

    in_yard = (request.args.get("in_yard") or "1").strip()

    qtext = (request.args.get("q") or "").strip().upper()

    rows = _inventory_query(site_id, in_yard, qtext).all()

    container_ids = [c.id for c, _, _ in rows]

    cls_by_container = _last_classification_by_container_ids(container_ids)
    eir_by_container = _last_eir_trip_date_by_container_ids(container_ids)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = [
        "ID",
        "CONTENEDOR",
        "TAMAÑO",
        "NAVIERA",
        "AÑO",
        "MAX_GROSS_KG",
        "FECHA_SALIDA_EIR",
        "EN_PATIO",
        "ESTIBA",
        "FILA",
        "NIVEL",
        "NOTAS",
    ]

    ws.append(headers)

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for c, pos, bay in rows:

        cls = cls_by_container.get(c.id)
        eir = eir_by_container.get(c.id)

        naviera = (cls.get("shipping_line") if cls else "") or ""
        year = (cls.get("manufacture_year") if cls else c.year) or ""
        max_gross = (cls.get("max_gross_kg") if cls else "") or ""

        trip_date = eir.get("trip_date") if eir else None
        trip_date_str = trip_date.strftime("%Y-%m-%d") if trip_date else ""

        notes = (cls.get("summary_text") if cls else (c.status_notes or "")) or ""

        ws.append(
            [
                c.id,
                c.code or "",
                c.size or "",
                naviera,
                year,
                max_gross,
                trip_date_str,
                "SI" if c.is_in_yard else "NO",
                (bay.code if (pos and bay) else "") or "",
                (pos.depth_row if pos else "") or "",
                (pos.tier if pos else "") or "",
                notes,
            ]
        )

    for col_idx in range(1, len(headers) + 1):

        col_letter = get_column_letter(col_idx)
        max_len = 0

        for cell in ws[col_letter]:
            v = "" if cell.value is None else str(cell.value)
            if len(v) > max_len:
                max_len = len(v)

        ws.column_dimensions[col_letter].width = min(max_len + 2, 45)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    tag = "ALL"

    if in_yard == "1":
        tag = "EN_PATIO"
    elif in_yard == "0":
        tag = "FUERA_PATIO"

    fname = f"inventario_{tag}.xlsx"

    return send_file(
        bio,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# =========================================================
# Detalle contenedor
# =========================================================

@inventory_bp.get("/inventory/<int:container_id>")
@login_required
def inventory_detail(container_id: int):

    site_id = _ensure_active_site()

    c = Container.query.get_or_404(container_id)

    if c.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    pos = (
        db.session.query(ContainerPosition, YardBay)
        .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(ContainerPosition.container_id == c.id)
        .first()
    )

    current_pos = None

    if pos:
        p, bay = pos

        current_pos = {
            "bay_code": bay.code if bay else None,
            "depth_row": p.depth_row,
            "tier": p.tier,
        }

    movements = (
        Movement.query
        .filter(Movement.container_id == c.id, Movement.site_id == site_id)
        .order_by(Movement.occurred_at.desc())
        .all()
    )

    mv_ids = [m.id for m in movements]

    photos_by_mv: dict[int, list[dict]] = {mid: [] for mid in mv_ids}

    if mv_ids:

        photos = (
            MovementPhoto.query
            .filter(MovementPhoto.movement_id.in_(mv_ids))
            .order_by(MovementPhoto.uploaded_at.asc())
            .all()
        )

        for ph in photos:

            if (ph.photo_type or "").upper() == "UPLOAD_ERROR":
                continue

            url_ok = _normalize_public_url(ph.url)

            if not url_ok:
                continue

            photos_by_mv.setdefault(ph.movement_id, []).append(
                {
                    "photo_type": ph.photo_type,
                    "url": url_ok,
                    "uploaded_at": ph.uploaded_at,
                }
            )

    return render_template(
        "inventory/detail.html",
        c=c,
        current_pos=current_pos,
        movements=movements,
        photos_by_mv=photos_by_mv,
    )

@inventory_bp.post("/inventory/<int:container_id>/mark-evacuation")
@login_required
def mark_container_evacuation(container_id: int):
    site_id = _ensure_active_site()

    c = Container.query.get_or_404(container_id)

    if c.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    if not c.is_in_yard:
        flash("Solo se pueden marcar contenedores que están en patio.", "warning")
        return redirect(url_for("inventory.inventory_index"))

    if (c.dispatch_status or "NORMAL") != "NORMAL":
        flash("Solo se pueden marcar como evacuar contenedores disponibles.", "warning")
        return redirect(url_for("inventory.inventory_index"))

    destination = (request.form.get("evacuation_destination") or "").strip().upper()
    custom_destination = (request.form.get("evacuation_destination_other") or "").strip().upper()
    evacuation_type = (request.form.get("evacuation_type") or "").strip().upper()
    evacuation_notes = (request.form.get("evacuation_notes") or "").strip().upper()

    if destination == "OTRO":
        destination = custom_destination

    if not destination:
        flash("Debe indicar el destino de evacuación.", "danger")
        return redirect(url_for("inventory.inventory_index"))

    if evacuation_type not in {"RT", "BARCO", "EVACUACION"}:
        flash("Debe indicar el tipo de evacuación.", "danger")
        return redirect(url_for("inventory.inventory_index"))

    c.dispatch_status = "PARA_EVACUAR"
    c.dispatch_marked_at = datetime.utcnow()
    c.dispatch_marked_by_user_id = current_user.id
    c.evacuation_destination = destination
    c.evacuation_type = evacuation_type
    c.evacuation_notes = evacuation_notes or None

    db.session.commit()

    flash(f"Contenedor {c.code} marcado para evacuar hacia {destination}.", "success")
    return redirect(url_for("inventory.inventory_index"))


@inventory_bp.post("/inventory/<int:container_id>/unmark-evacuation")
@login_required
def unmark_container_evacuation(container_id: int):
    site_id = _ensure_active_site()

    c = Container.query.get_or_404(container_id)

    if c.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    if (c.dispatch_status or "NORMAL") != "PARA_EVACUAR":
        flash("Solo se puede quitar evacuación a contenedores en estado Evacuar.", "warning")
        return redirect(url_for("inventory.inventory_index"))

    c.dispatch_status = "NORMAL"
    c.dispatch_marked_at = None
    c.dispatch_marked_by_user_id = None
    c.evacuation_destination = None
    c.evacuation_type = None
    c.evacuation_notes = None

    db.session.commit()

    flash(f"Contenedor {c.code} volvió a Disponible.", "success")
    return redirect(url_for("inventory.inventory_index"))

@inventory_bp.get("/inventory/evacuation-list")
@login_required
def evacuation_list():
    site_id = _ensure_active_site()

    rows = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(ContainerPosition, ContainerPosition.container_id == Container.id)
        .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
        .filter(
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
            db.func.coalesce(Container.dispatch_status, "NORMAL") == "PARA_EVACUAR",
        )
        .order_by(Container.updated_at.desc())
        .all()
    )

    items = []

    for c, pos, bay in rows:
        items.append({
            "id": c.id,
            "code": c.code,
            "size": c.size,
            "dispatch_status": c.dispatch_status or "NORMAL",
            "dispatch_marked_at": c.dispatch_marked_at,
            "evacuation_destination": c.evacuation_destination,
            "evacuation_type": c.evacuation_type,
            "evacuation_notes": c.evacuation_notes,
            "position": None if not pos else {
                "bay_code": bay.code if bay else None,
                "depth_row": pos.depth_row,
                "tier": pos.tier,
            },
        })

    return render_template(
        "inventory/evacuation_list.html",
        items=items,
    )

# =========================================================
# Carga masiva de contenedores
# =========================================================

BULK_REQUIRED_HEADERS = {
    "CONTENEDOR",
    "TAMAÑO",
    "NAVIERA",
    "ESTADO",
}

BULK_HEADERS = [
    "CONTENEDOR",
    "TAMAÑO",
    "NAVIERA",
    "ESTADO",
    "AÑO",
    "MAX_GROSS",
    "TARA",
    "NOTAS",
    "ESTIBA",
    "FILA",
    "NIVEL",
    "DESTINO_EVACUACION",
    "TIPO_EVACUACION",
    "OBS_EVACUACION",
]

BULK_STATUS_MAP = {
    "DISPONIBLE": "NORMAL",
    "NORMAL": "NORMAL",

    "ASIGNADO": "PARA_DESPACHO",
    "PARA_DESPACHO": "PARA_DESPACHO",

    "EVACUAR": "PARA_EVACUAR",
    "PARA_EVACUAR": "PARA_EVACUAR",

    "ASIGNADO_EVACUAR": "EVACUAR_SOLICITADO",
    "EVACUAR_SOLICITADO": "EVACUAR_SOLICITADO",

    "DESPACHO_MONTADO": "DESPACHO_MONTADO",
    "EVACUACION_MONTADA": "EVACUACION_MONTADA",
}

BULK_VALID_SIZES = {
    "20ST",
    "20OT",
    "20RF",
    "20TQ",
    "40ST",
    "40HC",
    "40RF",
    "40OT",
    "45HC",
}

BULK_VALID_EVAC_TYPES = {
    "RT",
    "BARCO",
    "EVACUACION",
}


def _bulk_clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _bulk_upper(value):
    return _bulk_clean(value).upper()


def _bulk_int(value):
    value = _bulk_clean(value)
    if not value:
        return None

    try:
        return int(float(value))
    except Exception:
        return None


def _bulk_normalize_container_code(value):
    raw = _bulk_upper(value)
    raw = raw.replace(" ", "").replace("\t", "")

    if not raw:
        return ""

    if "-" in raw:
        return raw

    # Formato sin guiones: CSNU0012888 -> CSNU-001288-8
    if len(raw) == 11 and raw[:4].isalpha() and raw[4:].isdigit():
        return f"{raw[:4]}-{raw[4:10]}-{raw[10]}"

    return raw


def _bulk_headers_from_sheet(ws):
    headers = {}

    for idx, cell in enumerate(ws[1], start=1):
        name = _bulk_upper(cell.value)
        if name:
            headers[name] = idx

    return headers


def _bulk_get(row, headers, name):
    idx = headers.get(name)
    if not idx:
        return ""
    return _bulk_clean(row[idx - 1].value)


def _bulk_validate_position(
    *,
    site_id: int,
    container_size: str,
    bay_code: str,
    depth_row,
    tier,
):
    bay_code = _bulk_upper(bay_code)

    if not bay_code and not depth_row and not tier:
        return {
            "ok": True,
            "has_position": False,
            "bay": None,
            "depth_row": None,
            "tier": None,
        }

    if not bay_code or not depth_row or not tier:
        return {
            "ok": False,
            "message": "Si se indica ubicación, ESTIBA, FILA y NIVEL deben venir completos.",
        }

    bay = YardBay.query.filter_by(
        site_id=site_id,
        code=bay_code,
        is_active=True,
    ).first()

    if not bay:
        return {
            "ok": False,
            "message": f"La estiba {bay_code} no existe o está inactiva en este predio.",
        }

    try:
        depth_row = int(depth_row)
        tier = int(tier)
    except Exception:
        return {
            "ok": False,
            "message": "FILA y NIVEL deben ser numéricos.",
        }

    if depth_row < 1 or depth_row > int(bay.max_depth_rows or 0):
        return {
            "ok": False,
            "message": f"La fila {depth_row} está fuera del rango permitido para {bay.code}.",
        }

    if tier < 1 or tier > int(bay.max_tiers or 0):
        return {
            "ok": False,
            "message": f"El nivel {tier} está fuera del rango permitido para {bay.code}.",
        }

    size = _bulk_upper(container_size)
    bay_size = _bulk_upper(bay.container_size_type or "40")

    if bay_size == "20" and not size.startswith("20"):
        return {
            "ok": False,
            "message": f"La estiba {bay.code} solo acepta contenedores de 20 pies.",
        }

    if bay_size == "40" and not (size.startswith("40") or size.startswith("45")):
        return {
            "ok": False,
            "message": f"La estiba {bay.code} solo acepta contenedores de 40/45 pies.",
        }

    occupied = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
            ContainerPosition.bay_id == bay.id,
            ContainerPosition.depth_row == depth_row,
            ContainerPosition.tier == tier,
        )
        .first()
    )

    if occupied:
        c, _ = occupied
        return {
            "ok": False,
            "message": f"La posición {bay.code} F{depth_row:02d} N{tier} ya está ocupada por {c.code}.",
        }

    if tier > 1:
        support = (
            db.session.query(ContainerPosition)
            .join(Container, Container.id == ContainerPosition.container_id)
            .filter(
                Container.site_id == site_id,
                Container.is_in_yard == True,  # noqa: E712
                ContainerPosition.bay_id == bay.id,
                ContainerPosition.depth_row == depth_row,
                ContainerPosition.tier == tier - 1,
            )
            .first()
        )

        if not support:
            return {
                "ok": False,
                "message": f"No se puede colocar en N{tier} sin soporte debajo en N{tier - 1}.",
            }

    access_blocker = (
        db.session.query(Container, ContainerPosition)
        .join(ContainerPosition, ContainerPosition.container_id == Container.id)
        .filter(
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
            ContainerPosition.bay_id == bay.id,
            ContainerPosition.depth_row > depth_row,
        )
        .first()
    )

    if access_blocker:
        c, p = access_blocker
        return {
            "ok": False,
            "message": f"La sidepick no puede acceder a F{depth_row:02d}; bloquea {c.code} en F{p.depth_row:02d} N{p.tier}.",
        }

    return {
        "ok": True,
        "has_position": True,
        "bay": bay,
        "depth_row": depth_row,
        "tier": tier,
    }


@inventory_bp.get("/inventory/bulk-upload/template")
@login_required
def inventory_bulk_upload_template():
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "DATOS"

    ws.append(BULK_HEADERS)

    header_fill = PatternFill("solid", fgColor="0F3B63")
    header_font = Font(bold=True, color="FFFFFF")

    for col_idx, header in enumerate(BULK_HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

        ws.column_dimensions[get_column_letter(col_idx)].width = max(len(header) + 4, 18)

    example = [
        "CSNU-001288-8",
        "40HC",
        "ONE",
        "DISPONIBLE",
        2015,
        32500,
        3800,
        "Piso OK",
        "A1E",
        1,
        1,
        "",
        "",
        "",
    ]

    ws.append(example)

    dv_size = DataValidation(
        type="list",
        formula1='"20ST,20OT,20RF,20TQ,40ST,40HC,40RF,40OT,45HC"',
        allow_blank=False,
    )

    dv_status = DataValidation(
        type="list",
        formula1='"DISPONIBLE,ASIGNADO,EVACUAR,ASIGNADO_EVACUAR,DESPACHO_MONTADO,EVACUACION_MONTADA"',
        allow_blank=False,
    )

    dv_dest = DataValidation(
        type="list",
        formula1='"LIMON,CALDERA,OTRO"',
        allow_blank=True,
    )

    dv_type = DataValidation(
        type="list",
        formula1='"RT,BARCO,EVACUACION"',
        allow_blank=True,
    )

    ws.add_data_validation(dv_size)
    ws.add_data_validation(dv_status)
    ws.add_data_validation(dv_dest)
    ws.add_data_validation(dv_type)

    dv_size.add("B2:B5000")
    dv_status.add("D2:D5000")
    dv_dest.add("L2:L5000")
    dv_type.add("M2:M5000")

    ws2 = wb.create_sheet("INSTRUCCIONES")

    instructions = [
        ["CARGA MASIVA DE CONTENEDORES", ""],
        ["", ""],
        ["Campos obligatorios", "CONTENEDOR, TAMAÑO, NAVIERA, ESTADO"],
        ["Campos opcionales", "AÑO, MAX_GROSS, TARA, NOTAS, ESTIBA, FILA, NIVEL, DESTINO_EVACUACION, TIPO_EVACUACION, OBS_EVACUACION"],
        ["", ""],
        ["Regla de ubicación", "Si ESTIBA, FILA y NIVEL vienen completos, se ubicará automáticamente."],
        ["Regla de ubicación", "Si falta alguno de los tres, quedará en patio pendiente de ubicar."],
        ["", ""],
        ["ESTADO EXCEL", "ESTADO SISTEMA"],
        ["DISPONIBLE", "NORMAL"],
        ["ASIGNADO", "PARA_DESPACHO"],
        ["EVACUAR", "PARA_EVACUAR"],
        ["ASIGNADO_EVACUAR", "EVACUAR_SOLICITADO"],
        ["DESPACHO_MONTADO", "DESPACHO_MONTADO"],
        ["EVACUACION_MONTADA", "EVACUACION_MONTADA"],
        ["", ""],
        ["Tamaños permitidos", "20ST, 20OT, 20RF, 20TQ, 40ST, 40HC, 40RF, 40OT, 45HC"],
        ["Tipos evacuación", "RT, BARCO, EVACUACION"],
        ["Destinos evacuación", "LIMON, CALDERA, OTRO"],
    ]

    for row in instructions:
        ws2.append(row)

    for col in range(1, 3):
        ws2.column_dimensions[get_column_letter(col)].width = 38

    ws2["A1"].font = Font(bold=True, size=14)
    ws2["A9"].font = Font(bold=True)
    ws2["B9"].font = Font(bold=True)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)

    return send_file(
        bio,
        as_attachment=True,
        download_name="plantilla_carga_masiva_contenedores.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@inventory_bp.get("/inventory/bulk-upload")
@login_required
def inventory_bulk_upload_view():
    _ensure_active_site()

    return render_template(
        "inventory/bulk_upload.html",
        result=None,
        errors=[],
    )


@inventory_bp.post("/inventory/bulk-upload")
@login_required
def inventory_bulk_upload_post():
    site_id = _ensure_active_site()

    file = request.files.get("file")

    if not file or not file.filename:
        flash("Debe seleccionar un archivo Excel.", "warning")
        return redirect(url_for("inventory.inventory_bulk_upload_view"))

    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        flash("El archivo debe ser .xlsx o .xlsm.", "danger")
        return redirect(url_for("inventory.inventory_bulk_upload_view"))

    try:
        wb = openpyxl.load_workbook(file, data_only=True)
    except Exception:
        flash("No se pudo leer el archivo Excel.", "danger")
        return redirect(url_for("inventory.inventory_bulk_upload_view"))

    ws = wb["DATOS"] if "DATOS" in wb.sheetnames else wb.active

    headers = _bulk_headers_from_sheet(ws)
    missing = sorted(BULK_REQUIRED_HEADERS - set(headers.keys()))

    if missing:
        return render_template(
            "inventory/bulk_upload.html",
            result=None,
            errors=[
                {
                    "row": 1,
                    "container": "—",
                    "message": f"Faltan columnas obligatorias: {', '.join(missing)}",
                }
            ],
        )

    errors = []
    parsed_rows = []
    seen_codes = set()

    for row_number, row in enumerate(ws.iter_rows(min_row=2), start=2):
        raw_values = [cell.value for cell in row]

        if all(v is None or str(v).strip() == "" for v in raw_values):
            continue

        code = _bulk_normalize_container_code(_bulk_get(row, headers, "CONTENEDOR"))
        size = _bulk_upper(_bulk_get(row, headers, "TAMAÑO"))
        shipping_line = _bulk_upper(_bulk_get(row, headers, "NAVIERA"))
        status_excel = _bulk_upper(_bulk_get(row, headers, "ESTADO"))

        year = _bulk_int(_bulk_get(row, headers, "AÑO"))
        max_gross_kg = _bulk_int(_bulk_get(row, headers, "MAX_GROSS"))
        tare_kg = _bulk_int(_bulk_get(row, headers, "TARA"))
        notes = _bulk_clean(_bulk_get(row, headers, "NOTAS"))

        bay_code = _bulk_upper(_bulk_get(row, headers, "ESTIBA"))
        depth_row = _bulk_int(_bulk_get(row, headers, "FILA"))
        tier = _bulk_int(_bulk_get(row, headers, "NIVEL"))

        evac_destination = _bulk_upper(_bulk_get(row, headers, "DESTINO_EVACUACION"))
        evac_type = _bulk_upper(_bulk_get(row, headers, "TIPO_EVACUACION"))
        evac_notes = _bulk_clean(_bulk_get(row, headers, "OBS_EVACUACION"))

        row_errors = []

        if not code:
            row_errors.append("CONTENEDOR es obligatorio.")

        if not size:
            row_errors.append("TAMAÑO es obligatorio.")
        elif size not in BULK_VALID_SIZES:
            row_errors.append(f"Tamaño inválido: {size}.")

        if not shipping_line:
            row_errors.append("NAVIERA es obligatoria.")

        if not status_excel:
            row_errors.append("ESTADO es obligatorio.")

        dispatch_status = BULK_STATUS_MAP.get(status_excel)

        if not dispatch_status:
            row_errors.append(f"Estado inválido: {status_excel}.")

        if code in seen_codes:
            row_errors.append(f"El contenedor {code} está duplicado dentro del Excel.")

        if code:
            seen_codes.add(code)

            exists = Container.query.filter_by(
                site_id=site_id,
                code=code,
            ).first()

            if exists:
                row_errors.append(f"El contenedor {code} ya existe en este predio.")

        if year is not None and (year < 1980 or year > 2100):
            row_errors.append(f"Año inválido: {year}.")

        if max_gross_kg is not None and max_gross_kg <= 0:
            row_errors.append("MAX_GROSS debe ser mayor a 0.")

        if tare_kg is not None and tare_kg <= 0:
            row_errors.append("TARA debe ser mayor a 0.")

        if evac_type and evac_type not in BULK_VALID_EVAC_TYPES:
            row_errors.append(f"TIPO_EVACUACION inválido: {evac_type}.")

        is_mounted_status = dispatch_status in {
            "DESPACHO_MONTADO",
            "EVACUACION_MONTADA",
        }

        position_result = {
            "ok": True,
            "has_position": False,
            "bay": None,
            "depth_row": None,
            "tier": None,
        }

        if not is_mounted_status:
            position_result = _bulk_validate_position(
                site_id=site_id,
                container_size=size,
                bay_code=bay_code,
                depth_row=depth_row,
                tier=tier,
            )

            if not position_result.get("ok"):
                row_errors.append(position_result.get("message") or "Ubicación inválida.")

        if row_errors:
            for msg in row_errors:
                errors.append({
                    "row": row_number,
                    "container": code or "—",
                    "message": msg,
                })
            continue

        parsed_rows.append({
            "row_number": row_number,
            "code": code,
            "size": size,
            "shipping_line": shipping_line,
            "dispatch_status": dispatch_status,
            "year": year,
            "max_gross_kg": max_gross_kg,
            "tare_kg": tare_kg,
            "notes": notes,
            "position": position_result,
            "evac_destination": evac_destination,
            "evac_type": evac_type,
            "evac_notes": evac_notes,
            "is_mounted_status": is_mounted_status,
        })

    if errors:
        db.session.rollback()

        return render_template(
            "inventory/bulk_upload.html",
            result={
                "ok": False,
                "created": 0,
                "validated": len(parsed_rows),
                "errors_count": len(errors),
            },
            errors=errors,
        )

    created_count = 0
    positioned_count = 0
    pending_location_count = 0
    mounted_count = 0

    try:
        for item in parsed_rows:
            position = item["position"]
            has_position = bool(position.get("has_position"))

            status_notes = None

            if not has_position:
                status_notes = "PENDIENTE_UBICAR_EN_PATIO"

            c = Container(
                site_id=site_id,
                code=item["code"],
                size=item["size"],
                year=item["year"],
                status_notes=status_notes,
                is_in_yard=True,
                dispatch_status=item["dispatch_status"],
            )

            if item["dispatch_status"] == "PARA_EVACUAR":
                c.dispatch_marked_at = datetime.utcnow()
                c.dispatch_marked_by_user_id = current_user.id
                c.evacuation_destination = item["evac_destination"] or None
                c.evacuation_type = item["evac_type"] or None
                c.evacuation_notes = item["evac_notes"] or None

            if item["dispatch_status"] in {"DESPACHO_MONTADO", "EVACUACION_MONTADA"}:
                c.mounted_at = datetime.utcnow()
                c.mounted_by_user_id = current_user.id

            db.session.add(c)
            db.session.flush()

            db.session.add(
                ContainerClassification(
                    site_id=site_id,
                    container_id=c.id,
                    classified_at=datetime.utcnow(),
                    classified_by_user_id=current_user.id,
                    shipping_line=item["shipping_line"],
                    max_gross_kg=item["max_gross_kg"],
                    tare_kg=item["tare_kg"],
                    manufacture_year=item["year"],
                    summary_text=item["notes"] or None,
                    notes=item["notes"] or None,
                )
            )

            if item["is_mounted_status"]:
                mounted_count += 1

                db.session.add(
                    Movement(
                        site_id=site_id,
                        container_id=c.id,
                        movement_type="MOVE",
                        occurred_at=datetime.utcnow(),
                        bay_code=None,
                        depth_row=None,
                        tier=None,
                        created_by_user_id=current_user.id,
                        notes=f"BULK_IMPORT_{item['dispatch_status']}_WITHOUT_POSITION",
                    )
                )

            elif has_position:
                bay = position["bay"]
                depth_row = position["depth_row"]
                tier = position["tier"]

                db.session.add(
                    ContainerPosition(
                        container_id=c.id,
                        bay_id=bay.id,
                        depth_row=depth_row,
                        tier=tier,
                        placed_by_user_id=current_user.id,
                    )
                )

                db.session.add(
                    Movement(
                        site_id=site_id,
                        container_id=c.id,
                        movement_type="MOVE",
                        occurred_at=datetime.utcnow(),
                        bay_code=bay.code,
                        depth_row=depth_row,
                        tier=tier,
                        created_by_user_id=current_user.id,
                        notes="BULK_IMPORT_PLACED_WITH_POSITION",
                    )
                )

                positioned_count += 1

            else:
                db.session.add(
                    Movement(
                        site_id=site_id,
                        container_id=c.id,
                        movement_type="MOVE",
                        occurred_at=datetime.utcnow(),
                        bay_code=None,
                        depth_row=None,
                        tier=None,
                        created_by_user_id=current_user.id,
                        notes="BULK_IMPORT_PENDING_LOCATION",
                    )
                )

                pending_location_count += 1

            created_count += 1

        db.session.commit()

    except Exception as exc:
        db.session.rollback()

        return render_template(
            "inventory/bulk_upload.html",
            result=None,
            errors=[
                {
                    "row": "—",
                    "container": "—",
                    "message": f"Error general importando archivo: {str(exc)}",
                }
            ],
        )

    flash(f"Carga masiva completada. {created_count} contenedores creados.", "success")

    return render_template(
        "inventory/bulk_upload.html",
        result={
            "ok": True,
            "created": created_count,
            "positioned": positioned_count,
            "pending_location": pending_location_count,
            "mounted": mounted_count,
            "errors_count": 0,
        },
        errors=[],
    )

@inventory_bp.post("/inventory/<int:container_id>/update-gate-in-origin")
@login_required
def update_gate_in_origin(container_id: int):
    c = Container.query.get_or_404(container_id)

    gate_in_origin_port = (
        request.form.get("gate_in_origin_port") or ""
    ).strip().upper()

    if gate_in_origin_port not in {"", "LIMON", "CALDERA"}:
        flash("Origen de ingreso inválido.", "danger")
        return redirect(url_for("inventory.inventory_detail", container_id=c.id))

    c.gate_in_origin_port = gate_in_origin_port or None
    c.updated_at = datetime.utcnow()

    db.session.add(c)

    audit_log(
        current_user.id,
        "CONTAINER_GATE_IN_ORIGIN_UPDATED",
        "container",
        c.id,
        {
            "container_code": c.code,
            "gate_in_origin_port": c.gate_in_origin_port,
        },
    )

    db.session.commit()

    flash("Origen de ingreso actualizado.", "success")
    return redirect(url_for("inventory.inventory_detail", container_id=c.id))