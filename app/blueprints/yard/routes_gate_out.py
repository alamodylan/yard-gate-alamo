import json
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text, or_

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay
from app.models.movement import Movement, MovementPhoto
from app.models.site import Site
from app.models.eir import EIR, EIRContainerDamage
from app.models.chassis import Chassis, ChassisInventory
from app.models.chassis_tire import ChassisTire
from app.services.audit import audit_log
from app.services.storage import get_storage, build_photo_key

from .routes import (
    _ensure_active_site,
    _parse_axle_seals_payload,
    _get_axle_seals_from_chassis_tires,
    _save_axle_seals_for_event,
    _compare_axle_seals,
    _format_axle_seal_difference_lines,
)

EIR_VALIDATE_CHASSIS_SEALS = False

@yard_bp.get("/gate-out")
@login_required
def gate_out_view():
    site_id = _ensure_active_site()

    active_site = Site.query.get(site_id)
    site_code = (active_site.code or "").upper() if active_site else ""

    if site_code in {"COYOL", "CALDERA", "LIMON"}:
        sql_last_class = text("""
            SELECT DISTINCT ON (cc.container_id)
                cc.container_id,
                cc.shipping_line
            FROM yard_gate_alamo.container_classifications cc
            WHERE cc.site_id = :site_id
            ORDER BY cc.container_id, cc.classified_at DESC NULLS LAST, cc.id DESC
        """)

        class_rows = db.session.execute(
            sql_last_class,
            {"site_id": site_id},
        ).mappings().all()

        shipping_line_map = {
            int(r["container_id"]): (r["shipping_line"] or "").strip().upper()
            for r in class_rows
        }

        allowed_gate_out_statuses = {
            "PARA_DESPACHO",
            "EVACUAR_SOLICITADO",
            "DESPACHO_MONTADO",
            "EVACUACION_MONTADA",
        }

        containers_raw = (
            db.session.query(Container, ContainerPosition, YardBay)
            .outerjoin(
                ContainerPosition,
                ContainerPosition.container_id == Container.id,
            )
            .outerjoin(
                YardBay,
                YardBay.id == ContainerPosition.bay_id,
            )
            .filter(
                Container.is_in_yard == True,  # noqa: E712
                Container.site_id == site_id,
                Container.dispatch_status.in_(list(allowed_gate_out_statuses)),
            )
            .order_by(
                Container.dispatch_status.asc(),
                YardBay.code.asc().nulls_last(),
                ContainerPosition.depth_row.asc().nulls_last(),
                ContainerPosition.tier.asc().nulls_last(),
                Container.code.asc(),
            )
            .all()
        )

        containers = []

        for c, p, b in containers_raw:
            dispatch_status = (c.dispatch_status or "NORMAL").strip().upper()

            containers.append({
                "container": c,
                "position": p,
                "bay": b,
                "shipping_line": shipping_line_map.get(c.id, ""),
                "is_mounted": dispatch_status in allowed_gate_out_statuses,
                "dispatch_status": dispatch_status,
            })

        chassis_rows = (
            Chassis.query
            .filter(
                Chassis.site_id == site_id,
                Chassis.is_in_yard == True,  # noqa: E712
            )
            .order_by(Chassis.chassis_number.asc())
            .all()
        )

        eirs_draft = (
            EIR.query
            .filter_by(site_id=site_id, status="DRAFT")
            .order_by(EIR.id.desc())
            .limit(200)
            .all()
        )

        return render_template(
            "yard/gate_out_predios.html",
            containers=containers,
            chassis_rows=chassis_rows,
            eirs_draft=eirs_draft,
        )

    containers = (
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

    return render_template(
        "yard/gate_out.html",
        rows=containers,
    )

@yard_bp.get("/api/yard/gate-out/search-chassis")
@login_required
def api_gate_out_search_chassis():
    site_id = _ensure_active_site()

    q = (request.args.get("q") or "").strip()
    q_like = f"%{q}%"

    query = (
        Chassis.query
        .filter(
            Chassis.site_id == site_id,
            Chassis.is_in_yard == True,  # noqa: E712
        )
    )

    if q:
        query = query.filter(
            or_(
                Chassis.chassis_number.ilike(q_like),
                Chassis.plate.ilike(q_like),
                Chassis.type_code.ilike(q_like),
            )
        )

    rows = (
        query
        .order_by(Chassis.chassis_number.asc())
        .limit(20)
        .all()
    )

    results = []

    for ch in rows:
        parts = [ch.chassis_number or "SIN NÚMERO"]

        if ch.plate:
            parts.append(f"Placa {ch.plate}")

        if ch.type_code:
            parts.append(ch.type_code)

        if ch.axles:
            parts.append(f"{ch.axles} ejes")

        results.append({
            "id": ch.id,
            "label": " · ".join(parts),
            "chassis_number": ch.chassis_number or "",
            "plate": ch.plate or "",
            "axles": ch.axles or "",
            "type_code": ch.type_code or "",
            "status": ch.status or "",
        })

    return jsonify({
        "ok": True,
        "results": results,
    })


@yard_bp.get("/api/yard/gate-out/search-containers")
@login_required
def api_gate_out_search_containers():
    site_id = _ensure_active_site()

    q = (request.args.get("q") or "").strip()
    q_like = f"%{q}%"

    allowed_gate_out_statuses = {
        "PARA_DESPACHO",
        "EVACUAR_SOLICITADO",
        "DESPACHO_MONTADO",
        "EVACUACION_MONTADA",
    }

    sql_last_class = text("""
        SELECT DISTINCT ON (cc.container_id)
            cc.container_id,
            cc.shipping_line
        FROM yard_gate_alamo.container_classifications cc
        WHERE cc.site_id = :site_id
        ORDER BY cc.container_id, cc.classified_at DESC NULLS LAST, cc.id DESC
    """)

    class_rows = db.session.execute(
        sql_last_class,
        {"site_id": site_id},
    ).mappings().all()

    shipping_line_map = {
        int(r["container_id"]): (r["shipping_line"] or "").strip().upper()
        for r in class_rows
    }

    query = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(
            ContainerPosition,
            ContainerPosition.container_id == Container.id,
        )
        .outerjoin(
            YardBay,
            YardBay.id == ContainerPosition.bay_id,
        )
        .filter(
            Container.is_in_yard == True,  # noqa: E712
            Container.site_id == site_id,
            Container.dispatch_status.in_(list(allowed_gate_out_statuses)),
        )
    )

    if q:
        query = query.filter(
            or_(
                Container.code.ilike(q_like),
                Container.size.ilike(q_like),
                Container.dispatch_status.ilike(q_like),
            )
        )

    rows = (
        query
        .order_by(
            Container.dispatch_status.asc(),
            YardBay.code.asc().nulls_last(),
            ContainerPosition.depth_row.asc().nulls_last(),
            ContainerPosition.tier.asc().nulls_last(),
            Container.code.asc(),
        )
        .limit(20)
        .all()
    )

    results = []

    for c, p, b in rows:
        dispatch_status = (c.dispatch_status or "NORMAL").strip().upper()

        if b and p:
            position_label = f"{b.code} F{str(p.depth_row).zfill(2)} N{p.tier}"
        else:
            position_label = "Montado / sin posición física"

        status_label = {
            "PARA_DESPACHO": "Asignado para despacho",
            "EVACUAR_SOLICITADO": "Solicitado para evacuar",
            "DESPACHO_MONTADO": "Despacho montado",
            "EVACUACION_MONTADA": "Evacuación montada",
        }.get(dispatch_status, dispatch_status)

        label = f"{c.code} · {c.size} · {position_label} · {status_label}"

        results.append({
            "id": c.id,
            "label": label,
            "code": c.code or "",
            "size": c.size or "",
            "bay": b.code if b else "",
            "row": p.depth_row if p else "",
            "tier": p.tier if p else "",
            "dispatch_status": dispatch_status,
            "shipping_line": shipping_line_map.get(c.id, ""),
            "position_label": position_label,
        })

    return jsonify({
        "ok": True,
        "results": results,
    })



@yard_bp.post("/gate-out")
@login_required
def gate_out_post():
    site_id = _ensure_active_site()
    active_site = Site.query.get(site_id)
    site_code = (active_site.code or "").upper() if active_site else ""
    is_predio = site_code in {"COYOL", "CALDERA", "LIMON"}

    if is_predio:
        mode = (request.form.get("mode") or "create").strip().lower()
        save_mode = (request.form.get("save_mode") or "pending").strip().lower()
        is_draft = save_mode == "draft"

        eir_id_raw = (request.form.get("eir_id") or "").strip()

        has_chassis = (request.form.get("has_chassis") or "0").strip() == "1"
        has_container = (request.form.get("has_container") or "0").strip() == "1"
        is_reefer = (request.form.get("is_reefer") or "0").strip() == "1"
        has_genset = (request.form.get("has_genset") or "0").strip() == "1"

        chassis_id_raw = (request.form.get("chassis_id") or "").strip()
        container_id_raw = (request.form.get("container_id") or "").strip()

        terminal_name = (request.form.get("terminal_name") or (active_site.name if active_site else "")).strip()
        trip_date_raw = (request.form.get("trip_date") or "").strip()
        trip_time_raw = (request.form.get("trip_time") or "").strip()
        carrier = (request.form.get("carrier") or "ATM").strip() or "ATM"
        origin = (request.form.get("origin") or site_code).strip()
        destination = (request.form.get("destination") or "").strip()
        operation_type = (request.form.get("operation_type") or "").strip().upper()

        driver_name = (request.form.get("driver_name") or "").strip()
        driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
        truck_plate = (request.form.get("truck_plate") or "").strip()

        shipping_line = (request.form.get("shipping_line") or "").strip().upper()
        container_seal = (request.form.get("container_seal") or "").strip()
        general_notes = (request.form.get("notes") or "").strip()

        chassis_axle_seals_json_raw = (request.form.get("chassis_axle_seals_json") or "{}").strip()
        chassis_axle_seals = _parse_axle_seals_payload(chassis_axle_seals_json_raw)

        chassis_lights_status = (request.form.get("chassis_lights_status") or "").strip().upper()
        chassis_lights_detail = (request.form.get("chassis_lights_detail") or "").strip()
        chassis_twistlocks_status = (request.form.get("chassis_twistlocks_status") or "").strip().upper()
        chassis_twistlocks_detail = (request.form.get("chassis_twistlocks_detail") or "").strip()
        chassis_mudflaps_status = (request.form.get("chassis_mudflaps_status") or "").strip().upper()
        chassis_mudflaps_detail = (request.form.get("chassis_mudflaps_detail") or "").strip()
        chassis_landing_gear_status = (request.form.get("chassis_landing_gear_status") or "").strip().upper()
        chassis_landing_gear_detail = (request.form.get("chassis_landing_gear_detail") or "").strip()
        chassis_structure_status = (request.form.get("chassis_structure_status") or "").strip().upper()
        chassis_structure_detail = (request.form.get("chassis_structure_detail") or "").strip()

        rf_running_status = (request.form.get("rf_running_status") or "").strip().upper()
        rf_temperature = (request.form.get("rf_temperature") or "").strip()
        rf_genset = (request.form.get("rf_genset") or "").strip().upper()
        rf_plug = (request.form.get("rf_plug") or "").strip()
        rf_cord = (request.form.get("rf_cord") or "").strip()
        rf_computer = (request.form.get("rf_computer") or "").strip()
        rf_fuel = (request.form.get("rf_fuel") or "").strip()
        rf_hourmeter = (request.form.get("rf_hourmeter") or "").strip()
        rf_alternator = (request.form.get("rf_alternator") or "").strip()
        rf_battery = (request.form.get("rf_battery") or "").strip()
        rf_notes = (request.form.get("rf_notes") or "").strip()

        damage_points_raw = (request.form.get("container_damage_points_json") or "[]").strip()

        terminal_name = terminal_name or (active_site.name if active_site else site_code or "ATM")
        origin = origin or (active_site.name if active_site else site_code or "ATM")
        carrier = carrier or "ATM"

        if trip_date_raw:
            try:
                trip_date = datetime.strptime(trip_date_raw, "%Y-%m-%d").date()
            except Exception:
                flash("Fecha inválida. Usa el selector de fecha.", "danger")
                return redirect(url_for("yard.gate_out_view"))
        else:
            trip_date = datetime.utcnow().date()

        trip_time = None
        if trip_time_raw:
            try:
                trip_time = datetime.strptime(trip_time_raw, "%H:%M").time()
            except Exception:
                flash("Hora inválida. Usa el selector de hora.", "danger")
                return redirect(url_for("yard.gate_out_view"))

        if operation_type and operation_type not in {"EXPORTACION", "IMPORTACION"}:
            flash("Tipo de operación inválido.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        try:
            damage_points = json.loads(damage_points_raw or "[]")
            if not isinstance(damage_points, list):
                damage_points = []
        except Exception:
            flash("Los daños del contenedor vienen dañados en el formulario.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        c = None
        bay_code = None
        depth_row = None
        tier = None
        container_size = None
        container_snapshot = None

        if has_container:
            if not container_id_raw or not str(container_id_raw).isdigit():
                if not is_draft:
                    flash("Debes seleccionar un contenedor.", "danger")
                    return redirect(url_for("yard.gate_out_view"))
            else:
                c = Container.query.get(int(container_id_raw))

                if not c or not c.is_in_yard or c.site_id != site_id:
                    if not is_draft:
                        flash("Contenedor no válido o no está en patio en este predio.", "danger")
                        return redirect(url_for("yard.gate_out_view"))
                    c = None

                if c:
                    pos = ContainerPosition.query.filter_by(container_id=c.id).first()
                    if pos:
                        bay = YardBay.query.get(pos.bay_id)
                        bay_code = bay.code if bay else None
                        depth_row = pos.depth_row
                        tier = pos.tier

                    container_size = getattr(c, "size", None)

                    if not shipping_line:
                        sql_last_class = text("""
                            SELECT shipping_line
                            FROM yard_gate_alamo.container_classifications
                            WHERE site_id = :site_id
                              AND container_id = :container_id
                            ORDER BY classified_at DESC NULLS LAST, id DESC
                            LIMIT 1
                        """)
                        row_class = db.session.execute(sql_last_class, {
                            "site_id": site_id,
                            "container_id": c.id,
                        }).mappings().first()

                        if row_class and row_class.get("shipping_line"):
                            shipping_line = (row_class.get("shipping_line") or "").strip().upper()

                    container_snapshot = {
                        "container_id": c.id,
                        "container_code": c.code,
                        "size": container_size,
                        "shipping_line": shipping_line or None,
                        "seal": container_seal or None,
                        "position": {
                            "bay_code": bay_code,
                            "depth_row": depth_row,
                            "tier": tier,
                        },
                        "damage_count": len(damage_points),
                    }

        ch = None
        chassis_snapshot = None

        if has_chassis:
            if not chassis_id_raw or not str(chassis_id_raw).isdigit():
                if not is_draft:
                    flash("Debes seleccionar un chasis.", "danger")
                    return redirect(url_for("yard.gate_out_view"))
            else:
                ch = Chassis.query.get(int(chassis_id_raw))

                if not ch:
                    if not is_draft:
                        flash("Chasis inválido.", "danger")
                        return redirect(url_for("yard.gate_out_view"))
                    ch = None

                if ch and (ch.site_id != site_id or not ch.is_in_yard):
                    if not is_draft:
                        flash("Ese chasis no está disponible en este predio.", "danger")
                        return redirect(url_for("yard.gate_out_view"))
                    ch = None

                if ch:
                    if EIR_VALIDATE_CHASSIS_SEALS:
                        expected_seals = _get_axle_seals_from_chassis_tires(ch.id)

                        seal_differences = _compare_axle_seals(
                            expected_seals,
                            chassis_axle_seals,
                        )

                        if seal_differences:
                            detail_lines = _format_axle_seal_difference_lines(seal_differences)

                            flash(
                                "No se puede guardar el EIR. "
                                "Los marchamos escaneados no coinciden con la configuración del chasis. "
                                + " | ".join(detail_lines),
                                "danger",
                            )

                            return redirect(url_for("yard.gate_out_view"))

                    tire_rows = (
                        ChassisTire.query
                        .filter_by(chassis_id=ch.id)
                        .order_by(ChassisTire.position_code.asc())
                        .all()
                    )

                    tires_snapshot = []

                    for tr in tire_rows:
                        tires_snapshot.append({
                            "position_code": tr.position_code,
                            "marchamo": tr.marchamo,
                            "tire_state": tr.tire_state,
                            "tire_number": tr.tire.tire_number if tr.tire else None,
                            "brand": tr.tire.brand if tr.tire else None,
                            "estrias_mm": getattr(tr, "estrias_mm", None),
                            "is_flat": bool(getattr(tr, "is_flat", False)),
                        })

                    chassis_snapshot = {
                        "chassis_id": ch.id,
                        "chassis_number": ch.chassis_number,
                        "plate": ch.plate,
                        "axles": ch.axles,
                        "type_code": getattr(ch, "type_code", None),
                        "inspection": {
                            "lights": {
                                "status": chassis_lights_status or "OK",
                                "detail": chassis_lights_detail or None,
                            },
                            "twist_locks": {
                                "status": chassis_twistlocks_status or "OK",
                                "detail": chassis_twistlocks_detail or None,
                            },
                            "mudflaps": {
                                "status": chassis_mudflaps_status or "OK",
                                "detail": chassis_mudflaps_detail or None,
                            },
                            "landing_gear": {
                                "status": chassis_landing_gear_status or "OK",
                                "detail": chassis_landing_gear_detail or None,
                            },
                            "structure": {
                                "status": chassis_structure_status or "OK",
                                "detail": chassis_structure_detail or None,
                            },
                        },
                        "tires": tires_snapshot,
                        "axle_seals_entered": chassis_axle_seals,
                        "seal_validation_enabled": bool(EIR_VALIDATE_CHASSIS_SEALS),
                    }

        reefer_snapshot = None

        if is_reefer:
            reefer_snapshot = {
                "running_status": rf_running_status or None,
                "temperature": rf_temperature or None,
                "genset": rf_genset or None,
                "plug": rf_plug or None,
                "cord": rf_cord or None,
                "computer": rf_computer or None,
                "fuel": rf_fuel or None,
                "hourmeter": rf_hourmeter or None,
                "alternator": rf_alternator or None,
                "battery": rf_battery or None,
                "notes": rf_notes or None,
            }

        if not is_draft and not has_container and not has_chassis:
            flash("Debes indicar al menos un equipo: chasis o contenedor.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        if not is_draft and has_container and not c:
            flash("Debes seleccionar un contenedor válido.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        if not is_draft and has_chassis and not ch:
            flash("Debes seleccionar un chasis válido.", "danger")
            return redirect(url_for("yard.gate_out_view"))

        mv = None

        if mode == "link":
            if not eir_id_raw or not str(eir_id_raw).isdigit():
                db.session.rollback()
                flash("Selecciona un EIR válido para continuar.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            eir = EIR.query.get(int(eir_id_raw))

            if not eir or eir.site_id != site_id:
                db.session.rollback()
                flash("Ese EIR no corresponde a este predio.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            if eir.status not in {"DRAFT", "PENDING", "EDITING"}:
                db.session.rollback()
                flash("Solo puedes continuar EIRs en estado DRAFT, PENDING o EDITING.", "danger")
                return redirect(url_for("yard.gate_out_view"))

            EIRContainerDamage.query.filter_by(eir_id=eir.id).delete()

        else:
            eir = EIR(
                site_id=site_id,
                created_by_user_id=current_user.id,
                terminal_name=terminal_name or "",
                trip_date=trip_date,
                trip_time=trip_time,
                carrier=carrier or "ATM",
                origin=origin or "",
                destination=destination or "",
                operation_type=operation_type or None,
                has_chassis=bool(has_chassis and ch),
                chassis_id=ch.id if ch else None,
                has_container=bool(has_container and c),
                container_id=c.id if c else None,
                is_reefer=bool(is_reefer),
                has_genset=bool(has_genset),
                status="DRAFT" if is_draft else "PENDING",
            )

            db.session.add(eir)
            db.session.flush()

        eir.terminal_name = terminal_name or ""
        eir.trip_date = trip_date
        eir.trip_time = trip_time
        eir.carrier = carrier or "ATM"
        eir.origin = origin or ""
        eir.destination = destination or ""
        eir.operation_type = operation_type or None

        eir.driver_name = driver_name or None
        eir.driver_id_doc = driver_id_doc or None
        eir.truck_plate = truck_plate or None

        eir.has_chassis = bool(has_chassis and ch)
        eir.chassis_id = ch.id if ch else None
        eir.chassis_plate = ch.plate if ch and ch.plate else None

        eir.has_container = bool(has_container and c)
        eir.container_id = c.id if c else None
        eir.container_size = container_size if c else None
        eir.shipping_line = shipping_line or None
        eir.container_seal = container_seal or None

        eir.is_reefer = bool(is_reefer)
        eir.has_genset = bool(has_genset)

        eir.general_notes = general_notes or None
        eir.chassis_snapshot_json = chassis_snapshot
        eir.container_snapshot_json = container_snapshot
        eir.reefer_snapshot_json = reefer_snapshot
        eir.gate_out_movement_id = None

        now_utc = datetime.utcnow()

        eir.status = "DRAFT" if is_draft else "PENDING"
        eir.updated_at = now_utc
        eir.last_edited_at = now_utc
        eir.last_edited_by_user_id = current_user.id

        if is_draft:
            eir.pdf_generated_at = None
            eir.finalized_at = None
            eir.inventory_out_at = None
            eir.editable_until = None
        else:
            eir.pdf_generated_at = now_utc
            eir.finalized_at = None
            eir.inventory_out_at = None
            eir.editable_until = None

        for item in damage_points:
            side = (item.get("side") or "").strip().upper()
            damage_type = (item.get("damage_type") or "").strip().upper()

            try:
                x = float(item.get("x"))
                y = float(item.get("y"))
            except Exception:
                continue

            if side not in {"LEFT", "RIGHT", "FRONT", "REAR", "ROOF", "INTERIOR"}:
                continue

            if damage_type not in {"A", "R", "G", "M", "C", "F", "H", "Q"}:
                continue

            dmg = EIRContainerDamage(
                eir_id=eir.id,
                side=side,
                damage_type=damage_type,
                x=x,
                y=y,
                notes=(item.get("notes") or "").strip() or None,
                created_by_user_id=current_user.id,
            )
            db.session.add(dmg)

        if ch:
            axles = int(getattr(ch, "axles", 2) or 2)

            _save_axle_seals_for_event(
                site_id=site_id,
                chassis_id=ch.id,
                axles=axles,
                seals_payload=chassis_axle_seals,
                event_type="EIR_OUT",
                event_id=eir.id,
                user_id=current_user.id,
            )

        audit_log(
            current_user.id,
            "EIR_DRAFT_SAVED" if is_draft else "EIR_PENDING_SAVED",
            "eir",
            eir.id,
            {
                "site_id": site_id,
                "eir_id": eir.id,
                "movement_id": mv.id if mv else None,
                "container_code": c.code if c else None,
                "chassis_id": ch.id if ch else None,
                "damage_count": len(damage_points),
                "is_reefer": bool(is_reefer),
                "seal_validation_enabled": bool(EIR_VALIDATE_CHASSIS_SEALS),
            },
        )

        db.session.commit()

        if is_draft:
            flash(
                f"EIR #{eir.id} guardado como BORRADOR. Puedes continuarlo después.",
                "success",
            )
        else:
            flash(
                f"EIR #{eir.id} guardado correctamente en estado PENDIENTE. "
                f"Debes confirmarlo para aplicar la salida de inventario.",
                "success",
            )

        return redirect(url_for("yard.eir_detail_view", eir_id=eir.id))

    container_id = request.form.get("container_id")
    driver_name = (request.form.get("driver_name") or "").strip()
    driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
    truck_plate = (request.form.get("truck_plate") or "").strip()
    notes = (request.form.get("notes") or "").strip()

    if not container_id or not str(container_id).isdigit():
        flash("Selecciona un contenedor.", "danger")
        return redirect(url_for("yard.gate_out_view"))

    c = Container.query.get(int(container_id))
    if not c or not c.is_in_yard or c.site_id != site_id:
        flash("Contenedor no válido o ya salió (predio actual).", "danger")
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
        site_id=site_id,
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

        db.session.add(
            MovementPhoto(
                movement_id=mv.id,
                photo_type="DRIVER_ID",
                url=url,
            )
        )

    ContainerPosition.query.filter_by(container_id=c.id).delete()
    c.is_in_yard = False

    audit_log(
        current_user.id,
        "GATE_OUT_CREATED",
        "container",
        c.id,
        {
            "container_code": c.code,
            "from_bay": bay_code,
            "depth_row": depth_row,
            "tier": tier,
            "site_id": site_id,
        },
    )

    db.session.commit()

    flash(f"Gate Out registrado: {c.code}", "success")
    return redirect(url_for("yard.ticket_view", movement_id=mv.id))


@yard_bp.get("/eir/<int:eir_id>/continue")
@login_required
def eir_continue_view(eir_id):
    site_id = _ensure_active_site()

    eir = EIR.query.get_or_404(eir_id)

    if eir.site_id != site_id:
        flash("Ese EIR no corresponde al predio activo.", "danger")
        return redirect(url_for("yard.eir_list_view"))

    if eir.status != "DRAFT":
        flash("Solo los borradores pueden continuar editándose.", "warning")
        return redirect(url_for("yard.eir_list_view"))

    active_site = Site.query.get(site_id)
    site_code = (active_site.code or "").upper() if active_site else ""

    sql_last_class = text("""
        SELECT DISTINCT ON (cc.container_id)
            cc.container_id,
            cc.shipping_line
        FROM yard_gate_alamo.container_classifications cc
        WHERE cc.site_id = :site_id
        ORDER BY cc.container_id, cc.classified_at DESC NULLS LAST, cc.id DESC
    """)

    class_rows = db.session.execute(
        sql_last_class,
        {"site_id": site_id},
    ).mappings().all()

    shipping_line_map = {
        int(r["container_id"]): (r["shipping_line"] or "").strip().upper()
        for r in class_rows
    }

    allowed_gate_out_statuses = {
        "PARA_DESPACHO",
        "EVACUAR_SOLICITADO",
        "DESPACHO_MONTADO",
        "EVACUACION_MONTADA",
    }

    containers_raw = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(
            ContainerPosition,
            ContainerPosition.container_id == Container.id,
        )
        .outerjoin(
            YardBay,
            YardBay.id == ContainerPosition.bay_id,
        )
        .filter(
            Container.is_in_yard == True,  # noqa: E712
            Container.site_id == site_id,
            Container.dispatch_status.in_(list(allowed_gate_out_statuses)),
        )
        .order_by(
            Container.dispatch_status.asc(),
            YardBay.code.asc().nulls_last(),
            ContainerPosition.depth_row.asc().nulls_last(),
            ContainerPosition.tier.asc().nulls_last(),
            Container.code.asc(),
        )
        .all()
    )

    containers = []

    for c, p, b in containers_raw:
        dispatch_status = (c.dispatch_status or "NORMAL").strip().upper()

        containers.append({
            "container": c,
            "position": p,
            "bay": b,
            "shipping_line": shipping_line_map.get(c.id, ""),
            "is_mounted": dispatch_status in allowed_gate_out_statuses,
            "dispatch_status": dispatch_status,
        })

    chassis_rows = (
        Chassis.query
        .filter(
            Chassis.site_id == site_id,
            Chassis.is_in_yard == True,  # noqa: E712
        )
        .order_by(Chassis.chassis_number.asc())
        .all()
    )

    eirs_draft = (
        EIR.query
        .filter_by(site_id=site_id, status="DRAFT")
        .order_by(EIR.id.desc())
        .limit(200)
        .all()
    )

    return render_template(
        "yard/gate_out_predios.html",
        containers=containers,
        chassis_rows=chassis_rows,
        eirs_draft=eirs_draft,
        edit_eir=eir,
    )