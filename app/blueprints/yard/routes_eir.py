from datetime import datetime, timedelta

import pytz
from flask import render_template, redirect, url_for, flash, abort, request
from flask_login import login_required, current_user
from sqlalchemy import or_

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay
from app.models.eir import EIR
from app.models.chassis import Chassis, ChassisInventory
from app.models.movement import Movement
from app.services.audit import audit_log

from .routes import _ensure_active_site


UTC_TZ = pytz.utc


# =========================
# EIR - Listado / Detalle / PDF
# =========================
@yard_bp.get("/eir")
@login_required
def eir_list_view():
    from sqlalchemy.orm import joinedload

    site_id = _ensure_active_site()

    q = (request.args.get("q") or "").strip().upper()
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    status = (request.args.get("status") or "").strip().upper()

    page = request.args.get("page", 1, type=int)
    per_page = 50

    query = (
        EIR.query
        .options(
            joinedload(EIR.site),
            joinedload(EIR.container),
            joinedload(EIR.chassis),
            joinedload(EIR.created_by),
        )
        .filter(EIR.site_id == site_id)
        .outerjoin(Container, Container.id == EIR.container_id)
        .outerjoin(Chassis, Chassis.id == EIR.chassis_id)
    )

    if q:
        query = query.filter(
            or_(
                Container.code.ilike(f"%{q}%"),
                Chassis.chassis_number.ilike(f"%{q}%")
            )
        )

    if date_from:
        try:
            d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
            query = query.filter(EIR.trip_date >= d_from)
        except Exception:
            flash("Fecha desde inválida.", "danger")
            return redirect(url_for("yard.eir_list_view"))

    if date_to:
        try:
            d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
            query = query.filter(EIR.trip_date <= d_to)
        except Exception:
            flash("Fecha hasta inválida.", "danger")
            return redirect(url_for("yard.eir_list_view"))

    if status:
        query = query.filter(EIR.status == status)

    pagination = (
        query
        .order_by(EIR.id.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    rows = pagination.items

    return render_template(
        "yard/eir_list.html",
        rows=rows,
        pagination=pagination,
        q=q,
        date_from=date_from,
        date_to=date_to,
        status=status,
    )


@yard_bp.get("/eir/<int:eir_id>")
@login_required
def eir_detail_view(eir_id: int):
    site_id = _ensure_active_site()

    eir = EIR.query.get_or_404(eir_id)
    if eir.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    return render_template("yard/eir_detail.html", eir=eir)


@yard_bp.get("/eir/<int:eir_id>/pdf")
@login_required
def eir_pdf_view(eir_id: int):
    site_id = _ensure_active_site()

    eir = EIR.query.get_or_404(eir_id)
    if eir.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    return render_template("yard/eir_pdf.html", eir=eir)


@yard_bp.post("/eir/<int:eir_id>/revert")
@login_required
def eir_revert_view(eir_id: int):
    site_id = _ensure_active_site()

    eir = EIR.query.get_or_404(eir_id)
    if eir.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    if eir.status != "CONFIRMED":
        flash("Solo se puede revertir un EIR en estado CONFIRMADO.", "danger")
        return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    now_utc = datetime.now(UTC_TZ)

    editable_until = eir.editable_until
    if editable_until and editable_until.tzinfo is None:
        editable_until = UTC_TZ.localize(editable_until)

    if not eir.editable_until or now_utc > eir.editable_until:
        flash("La ventana de 24 horas para revertir este EIR ya venció.", "danger")
        return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    reverted_anything = False

    if eir.has_container and eir.container_id:
        c = Container.query.get(eir.container_id)
        if c:
            c.is_in_yard = True
            db.session.add(c)
            reverted_anything = True

            snap = eir.container_snapshot_json or {}
            pos = snap.get("position") or {}
            bay_code = (pos.get("bay_code") or "").strip().upper()
            depth_row = pos.get("depth_row")
            tier = pos.get("tier")

            if bay_code and depth_row and tier:
                bay = YardBay.query.filter_by(
                    code=bay_code,
                    site_id=eir.site_id,
                    is_active=True
                ).first()

                if bay:
                    existing_pos = ContainerPosition.query.filter_by(container_id=c.id).first()
                    if not existing_pos:
                        slot_taken = ContainerPosition.query.filter_by(
                            bay_id=bay.id,
                            depth_row=depth_row,
                            tier=tier
                        ).first()

                        if not slot_taken:
                            db.session.add(
                                ContainerPosition(
                                    container_id=c.id,
                                    bay_id=bay.id,
                                    depth_row=depth_row,
                                    tier=tier,
                                    placed_by_user_id=current_user.id,
                                )
                            )

    if eir.has_chassis and eir.chassis_id:
        ch = Chassis.query.get(eir.chassis_id)
        if ch:
            ch.site_id = eir.site_id
            ch.is_in_yard = True
            db.session.add(ch)
            reverted_anything = True

    eir.status = "REVERTED"
    eir.reverted_at = now_utc
    eir.reverted_by_user_id = current_user.id
    eir.inventory_restored_at = now_utc
    eir.updated_at = now_utc

    audit_log(
        current_user.id,
        "EIR_REVERTED",
        "eir",
        eir.id,
        {
            "site_id": eir.site_id,
            "eir_id": eir.id,
            "container_id": eir.container_id,
            "chassis_id": eir.chassis_id,
            "movement_id": eir.gate_out_movement_id,
            "reverted_anything": reverted_anything,
        },
    )

    db.session.commit()
    flash(f"EIR #{eir.id} revertido correctamente. El equipo volvió a inventario.", "success")
    return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))


@yard_bp.post("/eir/<int:eir_id>/confirm")
@login_required
def eir_confirm_view(eir_id: int):
    site_id = _ensure_active_site()

    eir = EIR.query.get_or_404(eir_id)
    if eir.site_id != site_id and getattr(current_user, "role", None) != "admin":
        abort(403)

    if eir.status != "PENDING":
        flash("Solo se puede confirmar un EIR en estado PENDIENTE.", "danger")
        return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    c = None
    bay_code = depth_row = tier = None

    if eir.has_container and eir.container_id:
        c = Container.query.get(eir.container_id)
        if not c or c.site_id != site_id or not c.is_in_yard:
            flash("El contenedor ya no está disponible en inventario para confirmar este EIR.", "danger")
            return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

        pos = ContainerPosition.query.filter_by(container_id=c.id).first()
        if pos:
            bay = YardBay.query.get(pos.bay_id)
            bay_code = bay.code if bay else None
            depth_row = pos.depth_row
            tier = pos.tier

    ch = None
    if eir.has_chassis and eir.chassis_id:
        ch = Chassis.query.get(eir.chassis_id)
        if not ch or ch.site_id != site_id or not ch.is_in_yard:
            flash("El chasis ya no está disponible en inventario para confirmar este EIR.", "danger")
            return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    if not c and not ch:
        flash("Este EIR no tiene equipo válido para confirmar.", "danger")
        return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    mv = Movement(
        site_id=site_id,
        container_id=c.id if c else None,
        movement_type="GATE_OUT",
        occurred_at=datetime.utcnow(),
        bay_code=bay_code,
        depth_row=depth_row,
        tier=tier,
        driver_name=eir.driver_name or None,
        driver_id_doc=eir.driver_id_doc or None,
        truck_plate=eir.truck_plate or None,
        notes=eir.general_notes or None,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(mv)
    db.session.flush()

    if c:
        ContainerPosition.query.filter_by(container_id=c.id).delete()
        c.is_in_yard = False
        db.session.add(c)

    if ch:
        ch.is_in_yard = False
        db.session.add(ch)

        inv_rows = ChassisInventory.query.filter_by(chassis_id=ch.id).all()
        for inv in inv_rows:
            inv.is_in_yard = False
            inv.updated_at = datetime.utcnow()
            db.session.add(inv)

    now_utc = datetime.utcnow()
    eir.gate_out_movement_id = mv.id
    eir.status = "CONFIRMED"
    eir.finalized_at = now_utc
    eir.inventory_out_at = now_utc
    eir.editable_until = now_utc + timedelta(hours=24)
    eir.updated_at = now_utc
    eir.last_edited_at = now_utc
    eir.last_edited_by_user_id = current_user.id

    audit_log(
        current_user.id,
        "EIR_CONFIRMED",
        "eir",
        eir.id,
        {
            "site_id": site_id,
            "eir_id": eir.id,
            "movement_id": mv.id,
            "container_id": eir.container_id,
            "chassis_id": eir.chassis_id,
            "chassis_only": bool(ch and not c),
        },
    )

    db.session.commit()
    flash(f"EIR #{eir.id} confirmado correctamente. Se aplicó el Gate Out.", "success")
    return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))