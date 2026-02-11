# app/blueprints/inventory/routes.py
import os
from io import BytesIO

from flask import render_template, request, send_file
from flask_login import login_required

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.styles import Font, Alignment

from app.extensions import db
from app.blueprints.inventory import inventory_bp
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay
from app.models.movement import Movement, MovementPhoto


def _normalize_public_url(raw: str) -> str | None:
    """
    Devuelve un URL navegable para imágenes.

    - Si raw NO parece URL http(s) (ej: texto de error "SSL validation failed..."), retorna None.
    - Si existe R2_PUBLIC_BASE_URL/PUBLIC_BASE_URL, intenta convertir URLs tipo endpoint/bucket/key
      a public_base/key (para que sí abran en el navegador).
    """
    if not raw:
        return None

    raw = str(raw).strip()

    # Si es texto de error u otro string que no es URL, no sirve para <img> ni <a>
    if not (raw.startswith("http://") or raw.startswith("https://")):
        return None

    public_base = (os.environ.get("R2_PUBLIC_BASE_URL") or os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not public_base:
        return raw  # no podemos normalizar, devolvemos lo guardado

    # Intento de convertir endpoint/bucket/key -> public_base/key
    bucket = os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET") or ""
    if bucket and f"/{bucket}/" in raw:
        key = raw.split(f"/{bucket}/", 1)[1]
        return f"{public_base}/{key}"

    return raw


def _inventory_query(in_yard: str | None, qtext: str):
    """
    Fuente de verdad para inventario/export (misma query, mismos filtros).
    """
    q = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(ContainerPosition, ContainerPosition.container_id == Container.id)
        .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
    )

    if in_yard == "1":
        q = q.filter(Container.is_in_yard == True)  # noqa: E712
    elif in_yard == "0":
        q = q.filter(Container.is_in_yard == False)  # noqa: E712

    if qtext:
        q = q.filter(db.func.upper(Container.code).like(f"%{qtext}%"))

    return q.order_by(Container.updated_at.desc())


@inventory_bp.get("/inventory")
@login_required
def inventory_index():
    """
    Inventario con filtro:
      - in_yard="" => todos
      - in_yard="1" => solo en patio
      - in_yard="0" => solo fuera de patio
      - q => búsqueda por código
    """
    in_yard = request.args.get("in_yard")  # "1" / "0" / "" / None
    qtext = (request.args.get("q") or "").strip().upper()

    rows = _inventory_query(in_yard, qtext).all()

    items = []
    for c, pos, bay in rows:
        items.append(
            {
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "year": c.year,
                "is_in_yard": bool(c.is_in_yard),
                "status_notes": c.status_notes,
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


@inventory_bp.get("/inventory/export")
@login_required
def inventory_export():
    """
    Exporta inventario a Excel (xlsx) respetando filtros:
      - in_yard: "1" / "0" / "" / None
      - q: texto (código contenedor)
    """
    in_yard = request.args.get("in_yard")
    qtext = (request.args.get("q") or "").strip().upper()

    rows = _inventory_query(in_yard, qtext).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = [
        "ID",
        "CONTENEDOR",
        "TAMAÑO",
        "AÑO",
        "EN_PATIO",
        "ESTIBA",
        "FILA",
        "NIVEL",
        "NOTAS",
    ]
    ws.append(headers)

    # Header style
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    for c, pos, bay in rows:
        ws.append([
            c.id,
            c.code or "",
            c.size or "",
            c.year or "",
            "SI" if c.is_in_yard else "NO",
            (bay.code if (pos and bay) else "") or "",
            (pos.depth_row if pos else "") or "",
            (pos.tier if pos else "") or "",
            c.status_notes or "",
        ])

    # Auto ancho (simple y efectivo)
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


@inventory_bp.get("/inventory/<int:container_id>")
@login_required
def inventory_detail(container_id: int):
    """
    Detalle de un contenedor:
      - datos del contenedor
      - posición actual (si está en patio)
      - movimientos + fotos (solo URLs válidos)
    """
    c = Container.query.get_or_404(container_id)

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
        .filter(Movement.container_id == c.id)
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



