import json
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from sqlalchemy import text

from app.blueprints.yard import yard_bp
from app.extensions import db
from app.models.yard import YardBlock, YardBay
from app.models.container import Container, ContainerPosition
from app.models.movement import Movement, MovementPhoto
from app.models.site import Site
from app.models.chassis import Chassis, ChassisInventory
from app.models.chassis_tire import ChassisTire
from app.services.audit import audit_log
from app.services.storage import get_storage, build_photo_key
from app.services.yard_logic import find_first_free_slot

from .routes import (
    _ensure_active_site,
    CONTAINER_RE,
    SIZES,
    TIRE_STATES,
    _norm_enum,
    allowed_positions_for,
    _normalize_structure_status_for_db,
    _normalize_twistlocks_status_for_db,
    _normalize_landing_gear_status_for_db,
    _normalize_lights_status_for_db,
    _normalize_mudflap_status_for_db,
    _calc_tire_state_from_data,
    _normalize_position_for_tire_master,
    _save_grouped_tire_readings,
    _insert_dynamic,
    _build_chassis_gate_in_ticket_text,
    _fetch_last_final_eir_for_chassis,
    _build_workshop_ticket_text,
    _parse_axle_seals_payload,
    _get_axle_seals_for_event,
    _save_axle_seals_for_event,
    _compare_axle_seals,
    _format_axle_seal_difference_lines,
    _build_merchant_gate_in_ticket_text,
    _send_ticket_to_print_agent,
    _enqueue_print_job,
)


def _get_container_prefill_data(code: str) -> dict:
    """
    Busca datos base del contenedor y su última clasificación conocida.
    Retorna estructura lista para autocompletar Gate In.
    """
    normalized_code = (code or "").strip().upper()
    if not normalized_code:
        return {"found": False}

    container = (
        Container.query
        .filter(Container.code == normalized_code)
        .order_by(Container.id.desc())
        .first()
    )

    last_class = db.session.execute(text("""
        SELECT
            cc.container_id,
            cc.shipping_line,
            cc.max_gross_kg,
            cc.tare_kg,
            cc.manufacture_year,
            cc.summary_text,
            cc.notes,
            cc.classified_at
        FROM yard_gate_alamo.container_classifications cc
        JOIN (
            SELECT container_id, MAX(classified_at) AS max_classified_at
            FROM yard_gate_alamo.container_classifications
            WHERE container_id IN (
                SELECT id FROM yard_gate_alamo.containers WHERE code = :code
            )
            GROUP BY container_id
        ) x
          ON x.container_id = cc.container_id
         AND x.max_classified_at = cc.classified_at
        ORDER BY cc.classified_at DESC
        LIMIT 1
    """), {"code": normalized_code}).mappings().first()

    if not container and not last_class:
        return {"found": False}

    data = {
        "found": True,
        "container_id": container.id if container else None,
        "code": normalized_code,
        "size": container.size if container else None,
        "year": container.year if container else None,
        "status_notes": container.status_notes if container else None,
        "site_id": container.site_id if container else None,
        "is_in_yard": bool(container.is_in_yard) if container else False,

        "shipping_line": last_class["shipping_line"] if last_class else None,
        "max_gross_kg": last_class["max_gross_kg"] if last_class else None,
        "tare_kg": last_class["tare_kg"] if last_class else None,
        "manufacture_year": last_class["manufacture_year"] if last_class else None,
        "summary_text": last_class["summary_text"] if last_class else None,
        "classification_notes": last_class["notes"] if last_class else None,
    }

    if not data["year"] and data["manufacture_year"]:
        data["year"] = data["manufacture_year"]

    return data


# =========================
# Gate In
# =========================
@yard_bp.get("/gate-in")
@login_required
def gate_in_view():
    site_id = _ensure_active_site()

    blocks = (
        YardBlock.query
        .filter_by(site_id=site_id)
        .order_by(YardBlock.code.asc())
        .all()
    )

    sql_rows = db.session.execute(text("""
        SELECT
            c.id,
            c.chassis_number,
            c.plate,
            c.axles,
            c.status,
            c.site_id,
            c.type_code
        FROM yard_gate_alamo.chassis c
        ORDER BY c.chassis_number ASC
    """)).mappings().all()

    chassis_rows = [dict(r) for r in sql_rows]

    return render_template(
        "yard/gate_in.html",
        blocks=blocks,
        sizes=SIZES,
        chassis_rows=chassis_rows,
    )


@yard_bp.get("/api/container-prefill/<code>")
@login_required
def api_container_prefill(code: str):
    _ensure_active_site()

    normalized_code = code.strip().upper()

    if not CONTAINER_RE.match(normalized_code):
        return jsonify({
            "ok": False,
            "found": False,
            "error": "INVALID_CONTAINER_CODE"
        }), 400

    payload = _get_container_prefill_data(normalized_code)

    return jsonify({
        "ok": True,
        **payload
    })


@yard_bp.post("/gate-in")
@login_required
def gate_in_post():
    site_id = _ensure_active_site()
    active_site = Site.query.get(site_id)

    gate_in_mode = (request.form.get("gate_in_mode") or "CHASSIS_CONTAINER").strip().upper()
    has_chassis = (request.form.get("has_chassis") or "0").strip() == "1"
    has_container = (request.form.get("has_container") or "0").strip() == "1"
    is_merchant = has_container and not has_chassis

    if gate_in_mode not in {"CHASSIS_CONTAINER", "CHASSIS_ONLY", "CONTAINER_ONLY"}:
        flash("Tipo de ingreso inválido.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    if not has_chassis and not has_container:
        flash("Debes seleccionar al menos chasis o contenedor.", "danger")
        return redirect(url_for("yard.gate_in_view"))

    code = (request.form.get("container_code") or "").strip().upper()
    size = (request.form.get("size") or "").strip()
    year_raw = (request.form.get("year") or "").strip()

    status_notes_extra = (request.form.get("status_notes") or "").strip()

    driver_name = (request.form.get("driver_name") or "").strip()
    driver_id_doc = (request.form.get("driver_id_doc") or "").strip()
    truck_plate = (request.form.get("truck_plate") or "").strip()

    block_code = (request.form.get("block") or "").strip().upper()
    bay_number_raw = (request.form.get("bay_number") or "").strip()

    placement_mode = (request.form.get("placement_mode") or "auto").strip().lower()
    depth_row_raw = (request.form.get("depth_row") or "").strip()
    tier_raw = (request.form.get("tier") or "").strip()

    summary_text = (request.form.get("summary_text") or "").strip()
    classification_notes = (request.form.get("classification_notes") or "").strip()

    shipping_line = (request.form.get("shipping_line") or "").strip().upper()
    shipping_line_other = (request.form.get("shipping_line_other") or "").strip().upper()
    if shipping_line == "VASI":
        shipping_line = shipping_line_other or ""

    max_gross_hidden = (request.form.get("max_gross_kg_hidden") or "").strip()
    max_gross_other = (request.form.get("max_gross_kg") or "").strip()
    max_gross_kg = None

    if max_gross_hidden:
        try:
            max_gross_kg = int(max_gross_hidden)
        except ValueError:
            max_gross_kg = None
    elif max_gross_other:
        try:
            max_gross_kg = int(max_gross_other)
        except ValueError:
            max_gross_kg = None

    tare_raw = (request.form.get("tare_kg") or "").strip()
    tare_kg = None
    if tare_raw:
        try:
            tare_kg = int(tare_raw)
        except ValueError:
            tare_kg = None

    needs_workshop = (request.form.get("needs_workshop") or "0").strip() == "1"

    final_status_notes = summary_text or ""
    if status_notes_extra:
        final_status_notes = (
            final_status_notes + (", " if final_status_notes else "") + status_notes_extra
        ).strip()

    chassis_id_raw = (request.form.get("chassis_id") or "").strip()
    chassis_tire_checks_json_raw = (request.form.get("chassis_tire_checks_json") or "{}").strip()
    chassis_inspection_json_raw = (request.form.get("chassis_inspection_json") or "{}").strip()

    chassis_axle_seals_json_raw = (request.form.get("chassis_axle_seals_json") or "{}").strip()
    chassis_axle_seals = _parse_axle_seals_payload(chassis_axle_seals_json_raw)

    selected_chassis = None
    chassis_tire_checks = {}
    chassis_inspection = {}

    c = None
    bay = None
    bay_code = None
    depth_row = None
    tier = None
    year = None
    mv = None
    workshop_ticket_id = None
    chassis_classification_ticket_payload = None
    merchant_ticket_payload = None
    print_job_ids = []

    username = (
        getattr(current_user, "name", None)
        or getattr(current_user, "username", None)
        or getattr(current_user, "email", None)
        or f"USER {current_user.id}"
    )

    # =========================
    # Validación Merchant
    # =========================
    if is_merchant:
        if not driver_name:
            flash("Para ingreso Merchant debes ingresar el nombre del chofer.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if not driver_id_doc:
            flash("Para ingreso Merchant debes ingresar la cédula del chofer.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if not truck_plate:
            flash("Para ingreso Merchant debes ingresar la placa del cabezal.", "danger")
            return redirect(url_for("yard.gate_in_view"))

    # =========================
    # Validar / cargar chasis
    # =========================
    if has_chassis:
        if not chassis_id_raw:
            flash("Debes cargar un chasis para este tipo de ingreso.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if not chassis_id_raw.isdigit():
            flash("Chasis inválido.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        selected_chassis = Chassis.query.get(int(chassis_id_raw))
        if not selected_chassis:
            flash("El chasis seleccionado no existe.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        active_inv = (
            ChassisInventory.query
            .filter_by(chassis_id=selected_chassis.id, is_in_yard=True)
            .first()
        )

        if active_inv:
            inv_site = Site.query.get(active_inv.site_id)
            inv_site_name = inv_site.name if inv_site else f"ID {active_inv.site_id}"

            if active_inv.site_id == site_id:
                flash(
                    f"El chasis {selected_chassis.chassis_number} ya se encuentra en inventario de este predio.",
                    "danger",
                )
                return redirect(url_for("yard.gate_in_view"))

            flash(
                f"El chasis {selected_chassis.chassis_number} está activo en inventario del predio {inv_site_name}. "
                f"Primero debe realizarse el Gate Out / EIR de salida en ese predio.",
                "danger",
            )
            return redirect(url_for("yard.gate_in_view"))

        try:
            parsed_tires = json.loads(chassis_tire_checks_json_raw or "{}")
            chassis_tire_checks = parsed_tires if isinstance(parsed_tires, dict) else {}
        except Exception:
            flash("La clasificación de llantas del chasis viene dañada.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        try:
            parsed_inspection = json.loads(chassis_inspection_json_raw or "{}")
            chassis_inspection = parsed_inspection if isinstance(parsed_inspection, dict) else {}
        except Exception:
            flash("La clasificación estructural del chasis viene dañada.", "danger")
            return redirect(url_for("yard.gate_in_view"))

    # =========================
    # Validar / procesar contenedor
    # =========================
    if has_container:
        if not CONTAINER_RE.match(code):
            flash("Formato de contenedor inválido. Debe ser AAAA-000000-0.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if size not in SIZES:
            flash("Tamaño inválido.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if year_raw:
            try:
                year = int(year_raw)
                if year < 1950 or year > (datetime.utcnow().year + 1):
                    raise ValueError()
            except ValueError:
                flash("Año inválido.", "danger")
                return redirect(url_for("yard.gate_in_view"))

        if block_code not in {"A", "B", "C", "D"}:
            flash("Bloque inválido.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        try:
            bay_number = int(bay_number_raw)
            if not (1 <= bay_number <= 15):
                raise ValueError()
        except ValueError:
            flash("Estiba inválida (1..15).", "danger")
            return redirect(url_for("yard.gate_in_view"))

        block = YardBlock.query.filter_by(code=block_code, site_id=site_id).first()
        bay = (
            YardBay.query
            .filter_by(block_id=block.id, bay_number=bay_number, is_active=True, site_id=site_id)
            .first()
            if block else None
        )

        if not bay:
            flash("Estiba no encontrada.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        db.session.query(YardBay).filter(YardBay.id == bay.id).with_for_update().one()

        existing_here = Container.query.filter_by(site_id=site_id, code=code).first()

        other_in_yard = (
            Container.query
            .filter(
                Container.code == code,
                Container.is_in_yard == True,  # noqa: E712
                Container.site_id != site_id,
            )
            .first()
        )

        if other_in_yard:
            flash("Este contenedor está en patio, pero en otro predio.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if existing_here and existing_here.is_in_yard:
            flash("Este contenedor ya está en patio.", "danger")
            return redirect(url_for("yard.gate_in_view"))

        if existing_here:
            c = existing_here
        else:
            c = (
                Container.query
                .filter(Container.code == code)
                .order_by(Container.id.desc())
                .first()
            )

        if not c:
            c = Container(
                code=code,
                size=size,
                year=year,
                status_notes=final_status_notes or None,
                is_in_yard=True,
                site_id=site_id,
            )
            db.session.add(c)
            db.session.flush()
        else:
            c.site_id = site_id
            c.is_in_yard = True

            if size:
                c.size = size
            if year is not None:
                c.year = year
            if final_status_notes:
                c.status_notes = final_status_notes

            db.session.add(c)
            db.session.flush()

        should_insert_class = any([
            bool(shipping_line),
            bool(summary_text),
            max_gross_kg is not None,
            tare_kg is not None,
            year is not None,
            bool(classification_notes),
            bool(needs_workshop),
        ])

        if should_insert_class:
            shipping_line_db = (shipping_line or "").strip().upper()
            if not shipping_line_db:
                shipping_line_db = "ATM"

            db.session.execute(text("""
                INSERT INTO yard_gate_alamo.container_classifications
                (site_id, container_id, classified_at, classified_by_user_id,
                 shipping_line, max_gross_kg, tare_kg, manufacture_year,
                 needs_workshop, summary_text, notes)
                VALUES
                (:site_id, :container_id, NOW(), :uid,
                 :shipping_line, :max_gross_kg, :tare_kg, :manufacture_year,
                 :needs_workshop, :summary_text, :notes)
            """), {
                "site_id": site_id,
                "container_id": c.id,
                "uid": current_user.id,
                "shipping_line": shipping_line_db,
                "max_gross_kg": max_gross_kg,
                "tare_kg": tare_kg,
                "manufacture_year": year,
                "needs_workshop": bool(needs_workshop),
                "summary_text": (summary_text or None),
                "notes": (classification_notes or None),
            })

        if placement_mode == "manual":
            try:
                depth_row = int(depth_row_raw)
                tier = int(tier_raw)
            except ValueError:
                db.session.rollback()
                flash("Fila/Nivel inválidos.", "danger")
                return redirect(url_for("yard.gate_in_view"))

            if not (1 <= depth_row <= bay.max_depth_rows) or not (1 <= tier <= bay.max_tiers):
                db.session.rollback()
                flash("Fila/Nivel fuera de rango.", "danger")
                return redirect(url_for("yard.gate_in_view"))

            occupied = ContainerPosition.query.filter_by(
                bay_id=bay.id,
                depth_row=depth_row,
                tier=tier,
            ).first()

            if occupied:
                db.session.rollback()
                flash("Ese slot ya está ocupado.", "danger")
                return redirect(url_for("yard.gate_in_view"))
        else:
            slot = find_first_free_slot(bay.id)
            if not slot:
                db.session.rollback()
                flash(f"La estiba {bay.code} está llena.", "danger")
                return redirect(url_for("yard.gate_in_view"))
            depth_row, tier = slot

        ContainerPosition.query.filter_by(container_id=c.id).delete()
        db.session.add(
            ContainerPosition(
                container_id=c.id,
                bay_id=bay.id,
                depth_row=depth_row,
                tier=tier,
                placed_by_user_id=current_user.id,
            )
        )

        bay_code = bay.code

    # =========================
    # Crear movimiento
    # =========================
    if has_container:
        movement_notes = final_status_notes or None
    elif has_chassis:
        movement_notes = f"INGRESO SOLO CHASIS: {selected_chassis.chassis_number}"
    else:
        movement_notes = None

    mv = Movement(
        site_id=site_id,
        container_id=c.id if c else None,
        movement_type="GATE_IN",
        occurred_at=datetime.utcnow(),
        bay_code=bay_code,
        depth_row=depth_row,
        tier=tier,
        driver_name=driver_name or None,
        driver_id_doc=driver_id_doc or None,
        truck_plate=truck_plate or None,
        notes=movement_notes,
        created_by_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.session.add(mv)
    db.session.flush()

    # =========================
    # Procesar clasificación de chasis
    # =========================
    if selected_chassis:
        axles = int(getattr(selected_chassis, "axles", 2) or 2)
        allowed = set(allowed_positions_for(axles))

        structure_status = _normalize_structure_status_for_db(
            _norm_enum(chassis_inspection.get("structure_status"))
        )
        twistlocks_status = _normalize_twistlocks_status_for_db(
            _norm_enum(chassis_inspection.get("twistlocks_status"))
        )
        landing_gear_status = _normalize_landing_gear_status_for_db(
            _norm_enum(chassis_inspection.get("landing_gear_status"))
        )
        lights_status = _normalize_lights_status_for_db(
            _norm_enum(chassis_inspection.get("lights_status"))
        )
        mudflap_status = _normalize_mudflap_status_for_db(
            _norm_enum(chassis_inspection.get("mudflap_status"))
        )

        plate_text = (chassis_inspection.get("plate_text") or "").strip()
        plate_validation_status = _norm_enum(chassis_inspection.get("plate_validation_status"))

        damage_summary = (chassis_inspection.get("damage_summary") or "").strip()
        comments = (chassis_inspection.get("comments") or "").strip()
        driver_comments = (chassis_inspection.get("driver_comments") or "").strip()

        tire_lines = []
        ticket_alert_lines = []
        ticket_tire_rows = []
        any_tire_issue = False
        grouped_tire_readings = {}

        seal_differences = []

        last_eir_for_seals = _fetch_last_final_eir_for_chassis(selected_chassis.id)
        last_eir_id_for_seals = (
            int(last_eir_for_seals["id"])
            if last_eir_for_seals and last_eir_for_seals.get("id")
            else None
        )

        expected_seals = {}
        if last_eir_id_for_seals:
            expected_seals = _get_axle_seals_for_event(
                chassis_id=selected_chassis.id,
                event_type="EIR_OUT",
                event_id=last_eir_id_for_seals,
            )

        seal_differences = _compare_axle_seals(
            expected_seals,
            chassis_axle_seals,
        )

        _save_axle_seals_for_event(
            site_id=site_id,
            chassis_id=selected_chassis.id,
            axles=axles,
            seals_payload=chassis_axle_seals,
            event_type="GATE_IN",
            event_id=mv.id,
            user_id=current_user.id,
        )

        if seal_differences:
            seal_lines = _format_axle_seal_difference_lines(seal_differences)
            tire_lines.extend(seal_lines)
            ticket_alert_lines.extend(seal_lines)
            any_tire_issue = True

        tire_state_labels = {
            "GASTADA": "REGULAR",
            "PINCHADA": "DESINFLADA",
            "CAMBIAR": "MAL ESTADO",
            "NO_APTA": "ROJA",
            "OK": "VERDE",
        }

        for pos, item in (chassis_tire_checks or {}).items():
            pos = (pos or "").strip().upper()
            if pos not in allowed:
                continue

            item = item or {}
            seal_status = _norm_enum(item.get("seal_status")) or "OK"
            tire_number_status = _norm_enum(item.get("tire_number_status")) or "OK"
            pressure_psi = item.get("pressure_psi")

            estrias_mm_raw = item.get("estrias_mm")
            is_flat = bool(item.get("is_flat"))

            estrias_mm = None
            if estrias_mm_raw not in (None, ""):
                try:
                    estrias_mm = int(estrias_mm_raw)
                except Exception:
                    estrias_mm = None

            tire_state = _calc_tire_state_from_data(estrias_mm, is_flat)

            if seal_status not in {"OK", "DISTINTO"}:
                seal_status = "OK"

            if tire_number_status not in {"OK", "DISTINTO"}:
                tire_number_status = "OK"

            if tire_state not in TIRE_STATES:
                tire_state = "OK"

            row = ChassisTire.query.filter_by(
                chassis_id=selected_chassis.id,
                position_code=pos,
            ).first()

            if not row:
                row = ChassisTire(
                    chassis_id=selected_chassis.id,
                    position_code=pos,
                )
                db.session.add(row)
                db.session.flush()

            marchamo_config = row.marchamo
            tire_number_config = row.tire.tire_number if row.tire else None

            master_pos = _normalize_position_for_tire_master(pos)
            if master_pos:
                grp = grouped_tire_readings.setdefault(master_pos, {
                    "states": [],
                    "pressures": [],
                    "comments": [],
                    "seal_issue": False,
                    "seal_2": None,
                })

                grp["states"].append(tire_state)

                if pressure_psi not in (None, ""):
                    grp["pressures"].append(pressure_psi)

                if seal_status != "OK":
                    grp["seal_issue"] = True

                detail_parts = [
                    f"{pos}",
                    f"SEAL={seal_status}",
                    f"STATE={tire_state}",
                ]
                if estrias_mm not in (None, ""):
                    detail_parts.append(f"MM={estrias_mm}")
                if is_flat:
                    detail_parts.append("FLAT=SI")
                if pressure_psi not in (None, ""):
                    detail_parts.append(f"PSI={pressure_psi}")

                grp["comments"].append(" | ".join(detail_parts))

            row.estrias_mm = estrias_mm
            row.is_flat = is_flat
            row.tire_state = tire_state
            row.updated_at = datetime.utcnow()
            db.session.add(row)

            if seal_status == "DISTINTO":
                any_tire_issue = True
                tire_lines.append(f"{pos}: MARCHAMO DISTINTO")
                ticket_alert_lines.append(f"MARCHAMO DISTINTO EN {pos}")

            if tire_number_status == "DISTINTO":
                any_tire_issue = True
                tire_lines.append(f"{pos}: NUMERO DE LLANTA DISTINTO")
                ticket_alert_lines.append(f"NUMERO DE LLANTA DISTINTO EN {pos}")

            if is_flat:
                any_tire_issue = True
                tire_lines.append(f"{pos}: PINCHADA (DESINFLADA)")
            elif tire_state != "OK":
                any_tire_issue = True
                tire_lines.append(
                    f"{pos}: ESTADO {tire_state} ({tire_state_labels.get(tire_state, tire_state)})"
                )

            ticket_tire_rows.append({
                "pos": pos,
                "marchamo_config": marchamo_config,
                "seal_status": seal_status,
                "tire_number_config": tire_number_config,
                "tire_number_status": tire_number_status,
                "estrias_mm": estrias_mm,
                "is_flat": is_flat,
                "tire_state": tire_state,
                "pressure_psi": pressure_psi,
            })

        _save_grouped_tire_readings(
            site_id=site_id,
            chassis_id=selected_chassis.id,
            axles=axles,
            grouped_readings=grouped_tire_readings,
            user_id=current_user.id,
            event_type="GATE_IN",
            event_id=mv.id,
        )

        structure_lines = []

        if structure_status in {"GOLPE", "DOBLADO", "SOLDADURA"}:
            structure_lines.append(f"Estructura: {structure_status}")

        if twistlocks_status in {"DANADOS"}:
            structure_lines.append(f"Twistlocks: {twistlocks_status}")

        if landing_gear_status in {"DANADAS"}:
            structure_lines.append(f"Patas: {landing_gear_status}")

        if lights_status in {"IZQ_DANADA", "DER_DANADA"}:
            structure_lines.append(f"Luces: {lights_status}")

        if mudflap_status in {"NO_TRAE"}:
            structure_lines.append(f"Faldones: {mudflap_status}")

        if plate_validation_status in {"DISTINTA", "NO_TRAE"}:
            line = f"Placa: {plate_validation_status}"
            if plate_text:
                line += f" (CONFIGURADA: {plate_text})"
            structure_lines.append(line)

        if damage_summary:
            structure_lines.append(f"Resumen: {damage_summary}")

        if comments:
            structure_lines.append(f"Chequeador: {comments}")

        if driver_comments:
            structure_lines.append(f"Chofer: {driver_comments}")

        chassis_needs_workshop_manual = bool(chassis_inspection.get("needs_workshop"))
        needs_workshop_chassis = (
            bool(structure_lines)
            or bool(any_tire_issue)
            or chassis_needs_workshop_manual
        )

        inspection_id = _insert_dynamic("yard_gate_alamo", "chassis_inspections", {
            "site_id": site_id,
            "chassis_id": selected_chassis.id,
            "inspected_at": datetime.utcnow(),
            "inspected_by_user_id": current_user.id,
            "structure_status": structure_status or None,
            "twistlocks_status": twistlocks_status or None,
            "landing_gear_status": landing_gear_status or None,
            "lights_status": lights_status or None,
            "mudflap_status": mudflap_status or None,
            "plate_text": plate_text or None,
            "plate_validation_status": plate_validation_status or None,
            "comments": comments or None,
            "driver_comments": driver_comments or None,
            "needs_workshop": needs_workshop_chassis,
            "damage_summary": damage_summary or None,
            "movement_id": mv.id,
        })

        selected_chassis.site_id = site_id
        selected_chassis.is_in_yard = True
        selected_chassis.updated_at = datetime.utcnow()
        db.session.add(selected_chassis)

        inv = ChassisInventory.query.filter_by(chassis_id=selected_chassis.id).first()
        if not inv:
            inv = ChassisInventory(
                site_id=site_id,
                chassis_id=selected_chassis.id,
                chassis_code=selected_chassis.chassis_number,
                is_in_yard=True,
            )
        else:
            inv.site_id = site_id
            inv.chassis_code = selected_chassis.chassis_number
            inv.is_in_yard = True
            inv.updated_at = datetime.utcnow()
        db.session.add(inv)

        chassis_classification_ticket_payload = _build_chassis_gate_in_ticket_text(
            site_name=(active_site.name if active_site else ""),
            username=username,
            occurred_at=mv.occurred_at or datetime.utcnow(),
            chassis_number=selected_chassis.chassis_number,
            plate=selected_chassis.plate,
            structure_status=structure_status,
            twistlocks_status=twistlocks_status,
            landing_gear_status=landing_gear_status,
            lights_status=lights_status,
            mudflap_status=mudflap_status,
            plate_validation_status=plate_validation_status,
            damage_summary=damage_summary or None,
            comments=comments or None,
            driver_comments=driver_comments or None,
            tire_rows=ticket_tire_rows,
            alert_lines=ticket_alert_lines,
        )

        if needs_workshop_chassis:
            last_eir = _fetch_last_final_eir_for_chassis(selected_chassis.id)
            eir_prev_id = int(last_eir["id"]) if last_eir and last_eir.get("id") else None

            body = _build_workshop_ticket_text(
                chassis_number=selected_chassis.chassis_number,
                axles=axles,
                structure_lines=structure_lines,
                tire_lines=tire_lines,
                eir_prev_id=eir_prev_id,
            )

            workshop_ticket_id = _insert_dynamic("yard_gate_alamo", "workshop_tickets", {
                "site_id": site_id,
                "chassis_id": selected_chassis.id,
                "inspection_id": inspection_id,
                "created_at": datetime.utcnow(),
                "created_by_user_id": current_user.id,
                "status": "OPEN",
                "ticket_type": "CHASSIS_DAMAGE",
                "movement_id": mv.id,
                "payload_text": body,
                "notes": body,
            })

            audit_log(
                current_user.id,
                "WORKSHOP_TICKET_CREATED_FROM_GATE_IN",
                "workshop_ticket",
                workshop_ticket_id,
                {
                    "site_id": site_id,
                    "movement_id": mv.id,
                    "chassis_id": selected_chassis.id,
                    "container_id": c.id if c else None,
                    "seal_mismatch": bool(seal_differences),
                },
            )

        if chassis_classification_ticket_payload:
            print_job_id = _enqueue_print_job(
                payload_text=chassis_classification_ticket_payload,
                requested_by=username,
                request_origin="GATE_IN_CHASSIS",
                ticket_id=workshop_ticket_id,
            )
            print_job_ids.append(print_job_id)

            _send_ticket_to_print_agent(chassis_classification_ticket_payload)

        audit_log(
            current_user.id,
            "CHASSIS_CLASSIFIED_FROM_GATE_IN",
            "chassis",
            selected_chassis.id,
            {
                "site_id": site_id,
                "movement_id": mv.id,
                "container_id": c.id if c else None,
                "needs_workshop": needs_workshop_chassis,
                "classification_ticket": bool(chassis_classification_ticket_payload),
                "seal_mismatch": bool(seal_differences),
                "print_job_ids": print_job_ids,
            },
        )

    # =========================
    # Ticket Merchant
    # =========================
    if is_merchant and c:
        merchant_ticket_payload = _build_merchant_gate_in_ticket_text(
            site_name=(active_site.name if active_site else ""),
            username=username,
            occurred_at=mv.occurred_at or datetime.utcnow(),
            container_code=c.code,
            container_size=c.size,
            bay_code=bay_code,
            depth_row=depth_row,
            tier=tier,
            driver_name=driver_name,
            driver_id_doc=driver_id_doc,
            truck_plate=truck_plate,
            shipping_line=shipping_line or None,
            max_gross_kg=max_gross_kg,
            tare_kg=tare_kg,
            manufacture_year=year,
            summary_text=summary_text or None,
            classification_notes=classification_notes or None,
        )
        

        print_job_id = _enqueue_print_job(
            payload_text=merchant_ticket_payload,
            requested_by=username,
            request_origin="GATE_IN_MERCHANT",
            ticket_id=None,
        )
        print_job_ids.append(print_job_id)

        _send_ticket_to_print_agent(merchant_ticket_payload)

    # =========================
    # Fotos contenedor
    # =========================
    if has_container and c:
        storage = get_storage()
        photos = request.files.getlist("photos") or []

        for f in photos:
            if not f or not f.filename:
                continue
            try:
                key = build_photo_key(c.code, mv.id, f.filename)
                url = storage.upload_fileobj(
                    f,
                    key,
                    f.mimetype or "application/octet-stream",
                )
                db.session.add(
                    MovementPhoto(
                        movement_id=mv.id,
                        photo_type="CONTAINER",
                        url=url,
                    )
                )
            except Exception as e:
                db.session.add(
                    MovementPhoto(
                        movement_id=mv.id,
                        photo_type="UPLOAD_ERROR",
                        url=str(e),
                    )
                )

    audit_log(
        current_user.id,
        "GATE_IN_CREATED",
        "movement",
        mv.id,
        {
            "gate_in_mode": gate_in_mode,
            "has_chassis": has_chassis,
            "has_container": has_container,
            "is_merchant": is_merchant,
            "container_id": c.id if c else None,
            "container_code": c.code if c else None,
            "bay": bay.code if bay else None,
            "depth_row": depth_row,
            "tier": tier,
            "site_id": site_id,
            "chassis_id": selected_chassis.id if selected_chassis else None,
            "workshop_ticket_id": workshop_ticket_id,
            "print_job_ids": print_job_ids,
        },
    )

    db.session.commit()

    if has_chassis and has_container:
        msg = f"Gate In registrado: {c.code} con chasis {selected_chassis.chassis_number}."
    elif has_chassis:
        msg = f"Ingreso de chasis registrado: {selected_chassis.chassis_number}."
    else:
        msg = f"Ingreso Merchant registrado: {c.code}."

    if print_job_ids:
        msg += " Ticket enviado a cola de impresión."

    if workshop_ticket_id:
        msg += " Se generó ticket de taller por hallazgos."

    flash(msg, "success")
    return redirect(url_for("yard.gate_in_view"))