# app/blueprints/inventory/routes.py

import os
from io import BytesIO

from flask import render_template, request, send_file, session, abort
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



