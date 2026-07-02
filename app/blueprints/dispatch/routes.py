# app/blueprints/dispatch/routes.py

from datetime import datetime, date, time

from flask import render_template, request, redirect, url_for, flash, session, abort
from flask_login import login_required, current_user
from app.models.container import Container, ContainerPosition
from app.models.container_classification import ContainerClassification
from app.models.dispatch import DispatchAssignment, UserNotification
from app.blueprints.dispatch import dispatch_bp
from app.models.yard import YardBay
from app.extensions import db
from sqlalchemy.orm import selectinload, joinedload
from app.models.site import Site, UserSite
from app.models.dispatch import (
    DispatchContainerSize,
    ShippingLine,
    DispatchRequest,
    DispatchRequestLine,
)
from io import BytesIO
from datetime import datetime, date, time, timedelta
from flask import send_file
from app.models.eir import EIR
from app.models.chassis import Chassis

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, legal, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


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


def _parse_date(value: str):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_time(value: str):
    value = (value or "").strip()
    if not value:
        return None
    return datetime.strptime(value, "%H:%M").time()

def _recompute_dispatch_status(req: DispatchRequest):
    lines = DispatchRequestLine.query.filter_by(request_id=req.id).all()

    any_assigned = False
    all_assigned = True

    for line in lines:
        assigned_count = len(line.assignments)
        qty = int(line.quantity or 0)

        if assigned_count <= 0:
            line.status = "PENDIENTE"
            all_assigned = False
        elif assigned_count >= qty:
            line.status = "ASIGNADA"
            any_assigned = True
        else:
            line.status = "PARCIAL"
            any_assigned = True
            all_assigned = False

    if not lines:
        req.status = "PENDIENTE"
    elif all_assigned:
        req.status = "ASIGNADA"
    elif any_assigned:
        req.status = "PARCIAL"
    else:
        req.status = "PENDIENTE"

    req.updated_at = datetime.utcnow()



@dispatch_bp.get("/")
@login_required
def index():
    return redirect(url_for("dispatch.pending_requests"))


@dispatch_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_request():
    site_id = _ensure_active_site()

    sizes = (
        DispatchContainerSize.query
        .filter_by(is_active=True)
        .order_by(DispatchContainerSize.sort_order.asc())
        .all()
    )

    shipping_lines = (
        ShippingLine.query
        .filter_by(is_active=True)
        .order_by(ShippingLine.sort_order.asc())
        .all()
    )

    if request.method == "GET":
        return render_template(
            "dispatch/new_request.html",
            sizes=sizes,
            shipping_lines=shipping_lines,
        )

    request_type = (request.form.get("request_type") or "DESPACHO").strip().upper()
    booking = (request.form.get("booking") or "").strip().upper() or None
    shipping_line = (request.form.get("shipping_line") or "").strip().upper()

    client_name = (request.form.get("client_name") or "").strip().upper() or None
    product_name = (request.form.get("product_name") or "").strip().upper() or None

    chassis_type = (request.form.get("chassis_type") or "").strip().upper() or None
    port_out = (request.form.get("port_out") or "").strip().upper() or None
    special_instructions = (request.form.get("special_instructions") or "").strip().upper() or None

    line_sizes = request.form.getlist("line_size[]")
    line_quantities = request.form.getlist("line_quantity[]")
    line_dates = request.form.getlist("line_date[]")
    line_times = request.form.getlist("line_time[]")
    line_conditions = request.form.getlist("line_condition[]")

    if request_type not in {"DESPACHO", "VACIO"}:
        flash("Tipo de solicitud inválido.", "danger")
        return redirect(url_for("dispatch.new_request"))

    if not shipping_line:
        flash("Debe seleccionar una naviera.", "danger")
        return redirect(url_for("dispatch.new_request"))

    valid_sizes = {s.code for s in sizes}
    valid_shipping_lines = {s.code for s in shipping_lines}

    if shipping_line not in valid_shipping_lines:
        flash("Naviera inválida.", "danger")
        return redirect(url_for("dispatch.new_request"))

    clean_lines = []

    for idx, size_code in enumerate(line_sizes):
        size_code = (size_code or "").strip().upper()

        if not size_code:
            continue

        try:
            quantity = int(line_quantities[idx])
        except Exception:
            quantity = 0

        load_date = _parse_date(line_dates[idx] if idx < len(line_dates) else "")
        load_time = _parse_time(line_times[idx] if idx < len(line_times) else "")
        condition = (
            line_conditions[idx]
            if idx < len(line_conditions)
            else ("VACIO" if request_type == "VACIO" else "CARGADO")
        )
        condition = (condition or "").strip().upper()

        if size_code not in valid_sizes:
            flash(f"Tamaño inválido: {size_code}", "danger")
            return redirect(url_for("dispatch.new_request"))

        if quantity < 1 or quantity > 20:
            flash("La cantidad debe estar entre 1 y 20.", "danger")
            return redirect(url_for("dispatch.new_request"))

        if not load_date:
            flash("Cada línea debe tener fecha de carga.", "danger")
            return redirect(url_for("dispatch.new_request"))

        if condition not in {"CARGADO", "VACIO"}:
            condition = "VACIO" if request_type == "VACIO" else "CARGADO"

        clean_lines.append({
            "container_size": size_code,
            "quantity": quantity,
            "load_date": load_date,
            "load_time": load_time,
            "condition_type": condition,
        })

    if not clean_lines:
        flash("Debe agregar al menos una línea de solicitud.", "danger")
        return redirect(url_for("dispatch.new_request"))

    req = DispatchRequest(
        site_id=site_id,
        request_type=request_type,
        booking=booking,
        shipping_line=shipping_line,
        client_name=client_name,
        product_name=product_name,
        chassis_type=chassis_type,
        port_out=port_out,
        special_instructions=special_instructions,
        status="PENDIENTE",
        requested_by_user_id=current_user.id,
    )

    db.session.add(req)
    db.session.flush()

    for line in clean_lines:
        db.session.add(
            DispatchRequestLine(
                request_id=req.id,
                container_size=line["container_size"],
                quantity=line["quantity"],
                load_date=line["load_date"],
                load_time=line["load_time"],
                condition_type=line["condition_type"],
                status="PENDIENTE",
            )
        )

    db.session.commit()

    flash(f"Solicitud #{req.id} creada correctamente.", "success")
    return redirect(url_for("dispatch.pending_requests"))


@dispatch_bp.get("/pending")
@login_required
def pending_requests():
    site_id = _ensure_active_site()

    q = (request.args.get("q") or "").strip().upper()

    next_move_subq = (
        db.session.query(
            DispatchRequestLine.request_id.label("request_id"),
            db.func.min(DispatchRequestLine.load_date).label("next_load_date"),
            db.func.min(DispatchRequestLine.load_time).label("next_load_time"),
        )
        .group_by(DispatchRequestLine.request_id)
        .subquery()
    )

    query = (
        DispatchRequest.query
        .options(
            selectinload(DispatchRequest.lines)
            .selectinload(DispatchRequestLine.assignments)
        )
        .outerjoin(
            next_move_subq,
            next_move_subq.c.request_id == DispatchRequest.id,
        )
        .filter(
            DispatchRequest.site_id == site_id,
            DispatchRequest.status.in_(["PENDIENTE", "PARCIAL"]),
        )
    )

    if q:
        query = query.filter(
            db.func.upper(
                db.func.coalesce(DispatchRequest.booking, "")
            ).like(f"%{q}%")
        )

    requests = (
        query
        .order_by(
            next_move_subq.c.next_load_date.asc().nulls_last(),
            next_move_subq.c.next_load_time.asc().nulls_last(),
            DispatchRequest.requested_at.asc(),
        )
        .all()
    )

    return render_template(
        "dispatch/pending_requests.html",
        requests=requests,
        q=q,
    )
@dispatch_bp.get("/request/<int:request_id>")
@login_required
def request_detail(request_id: int):
    site_id = _ensure_active_site()

    req = (
        DispatchRequest.query
        .options(
            selectinload(DispatchRequest.lines)
            .selectinload(DispatchRequestLine.assignments)
        )
        .filter(DispatchRequest.id == request_id)
        .first_or_404()
    )

    if req.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    latest_classification_subquery = (
        db.session.query(
            ContainerClassification.container_id,
            db.func.max(ContainerClassification.classified_at).label("max_classified_at")
        )
        .filter(ContainerClassification.site_id == site_id)
        .group_by(ContainerClassification.container_id)
        .subquery()
    )

    line_data = []

    for line in req.lines:
        assigned_count = len(line.assignments)
        pending_count = max(int(line.quantity or 0) - assigned_count, 0)

        if req.request_type == "VACIO":
            status_filter = "PARA_EVACUAR"
        else:
            status_filter = "NORMAL"

        available_rows = (
            db.session.query(Container, ContainerPosition, YardBay, ContainerClassification)
            .outerjoin(ContainerPosition, ContainerPosition.container_id == Container.id)
            .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
            .outerjoin(
                latest_classification_subquery,
                latest_classification_subquery.c.container_id == Container.id
            )
            .outerjoin(
                ContainerClassification,
                db.and_(
                    ContainerClassification.container_id == Container.id,
                    ContainerClassification.classified_at == latest_classification_subquery.c.max_classified_at,
                )
            )
            .filter(
                Container.site_id == site_id,
                Container.is_in_yard == True,  # noqa: E712
                Container.size == line.container_size,
                db.func.coalesce(Container.dispatch_status, "NORMAL") == status_filter,
            )
            .order_by(Container.code.asc())
            .all()
        )

        available = []

        for c, pos, bay, cls in available_rows:
            shipping_line = ((cls.shipping_line if cls else "") or "").strip().upper()

            if req.shipping_line and shipping_line and shipping_line != req.shipping_line:
                continue

            available.append({
                "id": c.id,
                "code": c.code,
                "size": c.size,
                "shipping_line": shipping_line or "SIN NAVIERA",
                "gate_in_origin_port": c.gate_in_origin_port or "",
                "classification": ((cls.final_classification if cls else "") or "").strip().upper(),
                "notes": ((cls.summary_text if cls else "") or c.status_notes or "").strip(),
                "dispatch_status": c.dispatch_status or "NORMAL",
                "position": None if not pos else {
                    "bay_code": bay.code if bay else None,
                    "depth_row": pos.depth_row,
                    "tier": pos.tier,
                },
            })

        line_data.append({
            "line": line,
            "assigned_count": assigned_count,
            "pending_count": pending_count,
            "available": available,
            "status_filter": status_filter,
        })

    return render_template(
        "dispatch/request_detail.html",
        req=req,
        line_data=line_data,
    )

@dispatch_bp.post("/request/<int:request_id>/assign/<int:line_id>")
@login_required
def assign_containers(request_id: int, line_id: int):
    site_id = _ensure_active_site()

    req = DispatchRequest.query.get_or_404(request_id)

    if req.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    line = DispatchRequestLine.query.get_or_404(line_id)

    if line.request_id != req.id:
        abort(404)

    selected_ids = request.form.getlist("container_ids[]")

    if not selected_ids:
        flash("Debe seleccionar al menos un contenedor.", "warning")
        return redirect(url_for("dispatch.request_detail", request_id=req.id))

    already_assigned = len(line.assignments)
    pending_count = max(int(line.quantity or 0) - already_assigned, 0)

    if pending_count <= 0:
        flash("Esta línea ya está completamente asignada.", "info")
        return redirect(url_for("dispatch.request_detail", request_id=req.id))

    selected_ids = [int(x) for x in selected_ids if str(x).isdigit()]
    selected_ids = selected_ids[:pending_count]

    request_type = (req.request_type or "DESPACHO").strip().upper()

    if request_type == "VACIO":
        allowed_status = "PARA_EVACUAR"
        next_status = "EVACUAR_SOLICITADO"
    else:
        allowed_status = "NORMAL"
        next_status = "PARA_DESPACHO"

    containers = (
        Container.query
        .filter(
            Container.id.in_(selected_ids),
            Container.site_id == site_id,
            Container.is_in_yard == True,  # noqa: E712
            Container.size == line.container_size,
            db.func.coalesce(Container.dispatch_status, "NORMAL") == allowed_status,
        )
        .all()
    )

    if not containers:
        if request_type == "VACIO":
            flash("No se encontraron contenedores en estado Evacuar para asignar a esta solicitud de vacío.", "danger")
        else:
            flash("No se encontraron contenedores disponibles para asignar a esta solicitud de despacho.", "danger")

        return redirect(url_for("dispatch.request_detail", request_id=req.id))

    assigned_codes = []

    for c in containers:
        assignment_notes = (
            request.form.get(f"assignment_notes_{c.id}") or ""
        ).strip()

        assignment = DispatchAssignment(
            request_line_id=line.id,
            container_id=c.id,
            assigned_by_user_id=current_user.id,
            status="ASIGNADO",
            assignment_notes=assignment_notes or None,
        )

        db.session.add(assignment)

        c.dispatch_status = next_status
        c.dispatch_marked_at = datetime.utcnow()
        c.dispatch_marked_by_user_id = current_user.id

        assigned_codes.append(c.code)

    db.session.flush()

    total_assigned_after = already_assigned + len(containers)

    if total_assigned_after >= int(line.quantity or 0):
        line.status = "ASIGNADA"
    else:
        line.status = "PARCIAL"

    all_lines = DispatchRequestLine.query.filter_by(request_id=req.id).all()
    assigned_lines = [l for l in all_lines if l.status == "ASIGNADA"]
    partial_lines = [l for l in all_lines if l.status == "PARCIAL"]

    if len(assigned_lines) == len(all_lines):
        req.status = "ASIGNADA"
    elif assigned_lines or partial_lines:
        req.status = "PARCIAL"
    else:
        req.status = "PENDIENTE"

    req.updated_at = datetime.utcnow()

    notification = UserNotification(
        site_id=site_id,
        user_id=req.requested_by_user_id,
        title=f"Solicitud #{req.id} actualizada",
        message="Se asignaron contenedores: " + ", ".join(assigned_codes),
        related_type="DISPATCH_REQUEST",
        related_id=req.id,
    )
    db.session.add(notification)

    db.session.commit()

    flash(f"Se asignaron {len(containers)} contenedores correctamente.", "success")
    return redirect(url_for("dispatch.request_detail", request_id=req.id))

@dispatch_bp.get("/assigned")
@login_required
def assigned_requests():
    site_id = _ensure_active_site()

    q = (request.args.get("q") or "").strip().upper()

    query = (
        DispatchRequest.query
        .options(
            selectinload(DispatchRequest.lines)
            .selectinload(DispatchRequestLine.assignments)
            .joinedload(DispatchAssignment.container)
        )
        .filter(
            DispatchRequest.site_id == site_id,
            DispatchRequest.status.in_(["PARCIAL", "ASIGNADA"])
        )
    )

    if q:
        query = query.filter(
            db.func.upper(
                db.func.coalesce(DispatchRequest.booking, "")
            ).like(f"%{q}%")
        )

    requests = (
        query
        .order_by(
            DispatchRequest.updated_at.desc(),
            DispatchRequest.requested_at.desc()
        )
        .all()
    )

    return render_template(
        "dispatch/assigned_requests.html",
        requests=requests,
        q=q,
    )

@dispatch_bp.get("/agenda")
@login_required
def agenda():
    site_id = _ensure_active_site()

    import pytz

    cr_tz = pytz.timezone("America/Costa_Rica")
    today = datetime.now(cr_tz).date()

    lines = (
        DispatchRequestLine.query
        .options(
            joinedload(DispatchRequestLine.request),
            selectinload(DispatchRequestLine.assignments)
            .joinedload(DispatchAssignment.container)
        )
        .join(
            DispatchRequest,
            DispatchRequest.id == DispatchRequestLine.request_id
        )
        .filter(
            DispatchRequest.site_id == site_id,
            DispatchRequest.status != "CANCELADA",
            DispatchRequestLine.load_date >= today
        )
        .order_by(
            DispatchRequestLine.load_date.asc(),
            DispatchRequestLine.load_time.asc()
        )
        .all()
    )

    return render_template(
        "dispatch/agenda.html",
        lines=lines,
    )

@dispatch_bp.get("/prelist")
@login_required
def prelist():
    site_id = _ensure_active_site()

    from datetime import timedelta
    import pytz

    cr_tz = pytz.timezone("America/Costa_Rica")
    now_cr = datetime.now(cr_tz)

    today = now_cr.date()
    tomorrow = today + timedelta(days=1)
    current_time = now_cr.time()

    lines = (
        DispatchRequestLine.query
        .options(
            joinedload(DispatchRequestLine.request),
            selectinload(DispatchRequestLine.assignments)
            .joinedload(DispatchAssignment.container)
        )
        .join(
            DispatchRequest,
            DispatchRequest.id == DispatchRequestLine.request_id
        )
        .filter(
            DispatchRequest.site_id == site_id,
            DispatchRequest.status != "CANCELADA",
            DispatchRequestLine.load_date.in_([today, tomorrow])
        )
        .order_by(
            DispatchRequestLine.load_date.asc(),
            DispatchRequestLine.load_time.asc()
        )
        .all()
    )

    prelist_lines = []

    for line in lines:
        if line.load_date == tomorrow:
            prelist_lines.append(line)
            continue

        if line.load_date == today:
            if line.load_time is None or line.load_time > current_time:
                prelist_lines.append(line)

    prelist_lines.sort(
        key=lambda x: (
            x.load_date,
            x.load_time or datetime.min.time()
        )
    )

    dispatch_lines = [
        line for line in prelist_lines
        if ((line.request.request_type or "").strip().upper() == "DESPACHO")
    ]

    empty_lines = [
        line for line in prelist_lines
        if ((line.request.request_type or "").strip().upper() == "VACIO")
    ]

    return render_template(
        "dispatch/prelist.html",
        lines=prelist_lines,
        dispatch_lines=dispatch_lines,
        empty_lines=empty_lines,
    )

@dispatch_bp.post("/assignment/<int:assignment_id>/release")
@login_required
def release_assignment(assignment_id: int):
    site_id = _ensure_active_site()

    assignment = DispatchAssignment.query.get_or_404(assignment_id)
    line = DispatchRequestLine.query.get_or_404(assignment.request_line_id)
    req = DispatchRequest.query.get_or_404(line.request_id)

    if req.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    container = Container.query.get_or_404(assignment.container_id)

    request_type = (req.request_type or "DESPACHO").strip().upper()

    if request_type == "VACIO":
        container.dispatch_status = "PARA_EVACUAR"
    else:
        container.dispatch_status = "NORMAL"

    container.dispatch_marked_at = None
    container.dispatch_marked_by_user_id = None
    container.mounted_at = None
    container.mounted_by_user_id = None

    if hasattr(container, "is_mounted"):
        container.is_mounted = False

    db.session.delete(assignment)
    db.session.flush()

    _recompute_dispatch_status(req)

    db.session.commit()

    flash(f"Contenedor {container.code} liberado correctamente.", "success")
    return redirect(url_for("dispatch.assigned_requests"))

@dispatch_bp.post("/line/<int:line_id>/release-pending")
@login_required
def release_pending_line(line_id: int):
    site_id = _ensure_active_site()

    line = DispatchRequestLine.query.get_or_404(line_id)
    req = DispatchRequest.query.get_or_404(line.request_id)

    if req.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    assigned_count = len(line.assignments)
    qty = int(line.quantity or 0)
    pending_count = max(qty - assigned_count, 0)

    if pending_count <= 0:
        flash("Esta línea no tiene pendientes por liberar.", "info")
        return redirect(url_for("dispatch.assigned_requests"))

    line.quantity = qty - 1

    if line.quantity <= 0:
        db.session.delete(line)
        db.session.flush()

    _recompute_dispatch_status(req)

    db.session.commit()

    flash("Pendiente liberado correctamente.", "success")
    return redirect(url_for("dispatch.assigned_requests"))

@dispatch_bp.post("/assignment/<int:assignment_id>/reschedule")
@login_required
def reschedule_assignment(assignment_id: int):
    site_id = _ensure_active_site()

    assignment = DispatchAssignment.query.get_or_404(assignment_id)
    line = DispatchRequestLine.query.get_or_404(assignment.request_line_id)
    req = DispatchRequest.query.get_or_404(line.request_id)

    if req.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    new_date = _parse_date(request.form.get("load_date") or "")
    new_time = _parse_time(request.form.get("load_time") or "")

    if not new_date:
        flash("Debe indicar la nueva fecha.", "danger")
        return redirect(url_for("dispatch.assigned_requests"))

    assigned_count = len(line.assignments)

    if assigned_count <= 1 and int(line.quantity or 0) <= 1:
        line.load_date = new_date
        line.load_time = new_time
    else:
        line.quantity = max(int(line.quantity or 1) - 1, 1)

        new_line = DispatchRequestLine(
            request_id=req.id,
            container_size=line.container_size,
            quantity=1,
            load_date=new_date,
            load_time=new_time,
            condition_type=line.condition_type,
            status="ASIGNADA",
        )

        db.session.add(new_line)
        db.session.flush()

        assignment.request_line_id = new_line.id

    req.updated_at = datetime.utcnow()

    _recompute_dispatch_status(req)

    db.session.commit()

    flash("Asignación reagendada correctamente.", "success")
    return redirect(url_for("dispatch.assigned_requests"))

@dispatch_bp.get("/prelist/pdf")
@login_required
def prelist_pdf():
    site_id = _ensure_active_site()

    import pytz

    cr_tz = pytz.timezone("America/Costa_Rica")
    now_cr = datetime.now(cr_tz)

    today = now_cr.date()
    tomorrow = today + timedelta(days=1)
    current_time = now_cr.time()

    active_site = Site.query.get(site_id)
    site_name = active_site.name if active_site else f"Predio {site_id}"

    lines = (
        DispatchRequestLine.query
        .options(
            joinedload(DispatchRequestLine.request),
            selectinload(DispatchRequestLine.assignments)
            .joinedload(DispatchAssignment.container)
        )
        .join(
            DispatchRequest,
            DispatchRequest.id == DispatchRequestLine.request_id
        )
        .filter(
            DispatchRequest.site_id == site_id,
            DispatchRequest.status != "CANCELADA",
            DispatchRequestLine.load_date.in_([today, tomorrow])
        )
        .order_by(
            DispatchRequestLine.load_date.asc(),
            DispatchRequestLine.load_time.asc().nulls_last(),
            DispatchRequest.shipping_line.asc(),
            DispatchRequest.booking.asc().nulls_last(),
        )
        .all()
    )

    prelist_lines = []

    for line in lines:
        if line.load_date == tomorrow:
            prelist_lines.append(line)
            continue

        if line.load_date == today:
            if line.load_time is None or line.load_time > current_time:
                prelist_lines.append(line)

    container_ids = []
    chassis_ids = set()

    for line in prelist_lines:
        for a in line.assignments:
            if a.container_id:
                container_ids.append(a.container_id)

            if a.chassis_id:
                chassis_ids.add(a.chassis_id)

    eirs = []

    if container_ids:
        eirs = (
            EIR.query
            .filter(
                EIR.site_id == site_id,
                EIR.container_id.in_(container_ids),
                EIR.chassis_id.isnot(None),
                EIR.status.in_(["PENDING", "CONFIRMED"]),
            )
            .order_by(EIR.container_id.asc(), EIR.id.desc())
            .all()
        )

    eir_by_container = {}

    for eir in eirs:
        if eir.container_id not in eir_by_container:
            eir_by_container[eir.container_id] = eir

        if eir.chassis_id:
            chassis_ids.add(eir.chassis_id)

    chassis_by_id = {}

    if chassis_ids:
        chassis_rows = (
            Chassis.query
            .filter(Chassis.id.in_(list(chassis_ids)))
            .all()
        )

        chassis_by_id = {
            ch.id: ch
            for ch in chassis_rows
        }

    def _container_type(size_value):
        size_value = (size_value or "").strip().upper()

        if size_value.startswith("20"):
            return "20"

        if size_value.startswith("45"):
            return "45"

        if size_value.startswith("40"):
            return "40"

        return size_value or ""

    def _format_date_no_year(value):
        if not value:
            return ""

        return value.strftime("%d/%m")

    def _format_time(value):
        if not value:
            return ""

        return value.strftime("%I:%M %p")

    def _short_site(value):
        value = (value or "").strip().upper()

        if "COYOL" in value:
            return "COYOL"

        if "CALDERA" in value:
            return "CALDERA"

        if "LIMON" in value or "LIMÓN" in value:
            return "LIMON"

        return value[:10]

    data = [[
        "PREDIO",
        "NAVIERA",
        "CONTENEDOR",
        "CHASIS",
        "TIPO",
        "FORMATO",
        "FECHA",
        "HORA",
        "CLIENTE / PLANTA",
        "PRODUCTO",
        "DESTINO",
        "COMENTARIO",
        "SIEMPRE CARGA",
    ]]

    tomorrow_after_11_rows = []

    for line in prelist_lines:
        req = line.request
        assignments = list(line.assignments or [])

        for a in assignments:
            container = a.container
            eir = eir_by_container.get(a.container_id)

            chassis_id = a.chassis_id or (eir.chassis_id if eir else None)
            chassis = chassis_by_id.get(chassis_id) if chassis_id else None

            container_size = container.size if container else line.container_size
            tipo = _container_type(container_size)

            formato = ""
            if chassis and getattr(chassis, "axles", None):
                formato = f"{tipo}x{chassis.axles}"

            row = [
                _short_site(site_name),
                req.shipping_line or "",
                container.code if container else "",
                chassis.chassis_number if chassis else "",
                container_size or "",
                formato,
                _format_date_no_year(line.load_date),
                _format_time(line.load_time),
                req.client_name or "",
                req.product_name or "",
                req.port_out or "",
                a.assignment_notes or "",
                "☐",
            ]

            data.append(row)

            is_tomorrow_after_11 = (
                line.load_date == tomorrow
                and line.load_time is not None
                and line.load_time >= time(11, 0)
            )

            if is_tomorrow_after_11:
                tomorrow_after_11_rows.append(len(data) - 1)

        assigned_count = len(assignments)
        pending_count = max(int(line.quantity or 0) - assigned_count, 0)

        for _ in range(pending_count):
            row = [
                _short_site(site_name),
                req.shipping_line or "",
                "",
                "",
                line.container_size or "",
                "",
                _format_date_no_year(line.load_date),
                _format_time(line.load_time),
                req.client_name or "",
                req.product_name or "",
                req.port_out or "",
                "PENDIENTE DE ASIGNAR",
                "☐",
            ]

            data.append(row)

            is_tomorrow_after_11 = (
                line.load_date == tomorrow
                and line.load_time is not None
                and line.load_time >= time(11, 0)
            )

            if is_tomorrow_after_11:
                tomorrow_after_11_rows.append(len(data) - 1)

    if len(data) == 1:
        data.append([
            _short_site(site_name),
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "—",
            "Sin registros",
            "—",
            "—",
            "—",
            "☐",
        ])

    bio = BytesIO()

    doc = SimpleDocTemplate(
        bio,
        pagesize=landscape(legal),
        rightMargin=10,
        leftMargin=10,
        topMargin=10,
        bottomMargin=10,
    )

    styles = getSampleStyleSheet()
    title_style = styles["Normal"]
    title_style.fontName = "Helvetica-Bold"
    title_style.fontSize = 8
    title_style.leading = 9

    small_style = styles["Normal"]
    small_style.fontSize = 6
    small_style.leading = 7

    elements = []

    printed_at = now_cr.strftime("%d/%m/%Y %I:%M %p")

    elements.append(Paragraph("<b>PRELISTA OPERATIVA</b>", title_style))
    elements.append(Paragraph(f"Impreso: {printed_at}", small_style))
    elements.append(Spacer(1, 5))

    table = Table(
        data,
        repeatRows=1,
        colWidths=[
            45,   # Predio
            42,   # Naviera
            70,   # Contenedor
            62,   # Chasis
            38,   # Tipo
            42,   # Formato
            36,   # Fecha
            50,   # Hora
            108,  # Cliente / Planta
            105,  # Producto
            108,  # Destino
            105,  # Comentario
            55,   # Siempre carga
        ],
    )

    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.black),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 5.5),

        ("FONTSIZE", (0, 1), (-1, -1), 5.2),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),

        ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ALIGN", (8, 1), (11, -1), "LEFT"),

        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),

        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [
            colors.white,
            colors.HexColor("#F3F4F6"),
        ]),
    ]

    for row_idx in tomorrow_after_11_rows:
        table_style.extend([
            ("BACKGROUND", (0, row_idx), (-1, row_idx), colors.black),
            ("TEXTCOLOR", (0, row_idx), (-1, row_idx), colors.white),
            ("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold"),
        ])

    table.setStyle(TableStyle(table_style))

    elements.append(table)
    doc.build(elements)

    bio.seek(0)

    response = send_file(
        bio,
        as_attachment=False,
        download_name="prelista_operativa.pdf",
        mimetype="application/pdf",
    )

    response.headers["Content-Disposition"] = "inline; filename=prelista_operativa.pdf"

    return response