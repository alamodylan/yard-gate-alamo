# app/blueprints/tica/routes.py

from __future__ import annotations

import io
from datetime import datetime

from flask import (
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import noload

from app.blueprints.tica import tica_bp
from app.extensions import db
from app.models.tica import (
    TicaDestination,
    TicaDriver,
    TicaGeneratedFile,
    TicaImportBatch,
    TicaTransporter,
)
from app.models.user import User
from app.services.audit import audit_log

from app.blueprints.tica.services import (
    CR_TIMEZONE,
    GENERATED_STATUS_CREATED,
    IMPORT_DESTINATIONS,
    IMPORT_DRIVERS,
    IMPORT_STATUS_ERROR,
    IMPORT_TRANSPORTERS,
    TicaImportError,
    TicaServiceError,
    TicaValidationError,
    build_destinations_template,
    build_drivers_template,
    build_generated_file_record,
    build_generation_data,
    build_transporters_template,
    create_or_update_destination,
    create_or_update_driver,
    create_or_update_transporter,
    generate_trm_in_memory,
    normalize_boolean,
    rebuild_generated_file_bytes,
    run_catalog_import,
    search_destinations,
    search_drivers,
    search_transporters,
)


# =========================================================
# Constantes
# =========================================================

TICA_OUT_PATH_DISPLAY = r"C:\tica\tr\out"

CATALOG_PAGE_SIZE = 50
HISTORY_PAGE_SIZE = 50

ALLOWED_EXCEL_EXTENSIONS = {
    ".xlsx",
    ".xlsm",
}


# =========================================================
# Helpers internos
# =========================================================

def _optional_int(value):
    """
    Convierte un valor opcional a int.

    Devuelve None si viene vacío.
    """
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _request_payload() -> dict:
    """
    Permite reutilizar los endpoints tanto desde forms como desde fetch JSON.
    """
    if request.is_json:
        return request.get_json(silent=True) or {}

    return request.form.to_dict()


def _bool_from_payload(
    data: dict,
    field_name: str = "is_active",
    *,
    default: bool = True,
) -> bool:
    return normalize_boolean(
        data.get(field_name),
        default=default,
    )


def _service_error_json(
    exc: TicaServiceError,
    *,
    status_code: int = 400,
):
    return jsonify({
        "ok": False,
        "error": exc.code,
        "message": exc.message,
    }), status_code


def _get_selected_catalogs(
    *,
    transporter_id: int,
    driver_id: int,
    destination_id: int,
):
    """
    Obtiene los tres catálogos seleccionados para generación.

    Son solamente tres búsquedas por PK y únicamente ocurren al generar
    un TRM, no al abrir la pantalla.
    """
    transporter = db.session.get(
        TicaTransporter,
        transporter_id,
    )

    if not transporter or not transporter.is_active:
        raise TicaValidationError(
            "El transportista seleccionado no existe o está inactivo.",
            code="TRANSPORTER_NOT_FOUND",
        )

    driver = db.session.get(
        TicaDriver,
        driver_id,
    )

    if not driver or not driver.is_active:
        raise TicaValidationError(
            "El chofer seleccionado no existe o está inactivo.",
            code="DRIVER_NOT_FOUND",
        )

    if int(driver.transporter_id) != int(transporter.id):
        raise TicaValidationError(
            "El chofer seleccionado no pertenece al transportista indicado.",
            code="DRIVER_TRANSPORTER_MISMATCH",
        )

    destination = db.session.get(
        TicaDestination,
        destination_id,
    )

    if not destination or not destination.is_active:
        raise TicaValidationError(
            "La ubicación seleccionada no existe o está inactiva.",
            code="DESTINATION_NOT_FOUND",
        )

    return transporter, driver, destination


def _excel_extension(filename: str) -> str:
    filename = (filename or "").strip().lower()

    for extension in ALLOWED_EXCEL_EXTENSIONS:
        if filename.endswith(extension):
            return extension

    return ""


# =========================================================
# Generador principal
# =========================================================

@tica_bp.get("/")
@login_required
def generator():
    """
    Pantalla principal.

    No carga transportistas, choferes ni ubicaciones completos.
    Todos los catálogos se consultan por AJAX.
    """
    today_cr = datetime.now(CR_TIMEZONE).date()

    return render_template(
        "tica/generator.html",
        today_cr=today_cr,
        out_path_display=TICA_OUT_PATH_DISPLAY,
        default_dua_ordinal=1,
    )


@tica_bp.post("/generate")
@login_required
def generate():
    """
    Genera el TRM.

    Flujo:
    1. valida los IDs de catálogo;
    2. toma los valores editables del formulario;
    3. genera el TRM en memoria;
    4. guarda copia exacta en PostgreSQL;
    5. COMMIT;
    6. devuelve el archivo al navegador.

    No escribe archivos en Render.
    """
    transporter_id = _optional_int(
        request.form.get("transporter_id")
    )

    driver_id = _optional_int(
        request.form.get("driver_id")
    )

    destination_id = _optional_int(
        request.form.get("destination_id")
    )

    if not transporter_id:
        flash(
            "Debe seleccionar un transportista.",
            "danger",
        )
        return redirect(url_for("tica.generator"))

    if not driver_id:
        flash(
            "Debe seleccionar un chofer.",
            "danger",
        )
        return redirect(url_for("tica.generator"))

    if not destination_id:
        flash(
            "Debe seleccionar una ubicación.",
            "danger",
        )
        return redirect(url_for("tica.generator"))

    try:
        transporter, driver, destination = (
            _get_selected_catalogs(
                transporter_id=transporter_id,
                driver_id=driver_id,
                destination_id=destination_id,
            )
        )

        # =====================================================
        # IMPORTANTE
        #
        # Los campos cargados desde catálogo permanecen editables.
        # Lo que venga en el formulario es lo que finalmente se
        # escribe en el TRM.
        # =====================================================

        generation_data = build_generation_data(
            movement_type=request.form.get("movement_type"),
            trip_number=request.form.get("trip_number"),
            dua_ordinal=(
                request.form.get("dua_ordinal") or "1"
            ),

            transporter_identification=(
                request.form.get(
                    "transporter_identification"
                )
                or transporter.identification_number
            ),

            driver_identification=(
                request.form.get(
                    "driver_identification"
                )
                or driver.identification_number
            ),

            driver_name=(
                request.form.get("driver_name")
                or driver.name
            ),

            plate=(
                request.form.get("plate")
                or driver.plate
            ),

            weight=request.form.get("weight"),
            packages=request.form.get("packages"),

            destination_code=(
                request.form.get("destination_code")
                or destination.code
            ),

            container_number=request.form.get(
                "container_number"
            ),

            seal_number=request.form.get(
                "seal_number"
            ),

            # La fecha se obtiene automáticamente desde el
            # servicio usando America/Costa_Rica.
            movement_date=None,

            transporter_id=transporter.id,
            driver_id=driver.id,
            destination_id=destination.id,

            transporter_name_snapshot=(
                transporter.name
            ),

            destination_name_snapshot=(
                destination.name
            ),
        )

        result = generate_trm_in_memory(
            generation_data
        )

        record = build_generated_file_record(
            data=generation_data,
            result=result,
            created_by_user_id=current_user.id,
            status=GENERATED_STATUS_CREATED,
        )

        db.session.add(record)
        db.session.flush()

        audit_log(
            current_user.id,
            "TICA_TRM_GENERATED",
            "tica_generated_file",
            record.id,
            {
                "file_name": result.file_name,
                "movement_type": (
                    generation_data.movement_type
                ),
                "trip_number": (
                    generation_data.trip_number
                ),
                "dua_ordinal": (
                    generation_data.dua_ordinal
                ),
                "container_number": (
                    generation_data.container_number
                ),
                "transporter_id": transporter.id,
                "driver_id": driver.id,
                "destination_id": destination.id,
            },
        )

        db.session.commit()

        response = send_file(
            io.BytesIO(result.content_bytes),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=result.file_name,
            max_age=0,
        )

        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate"
        )

        response.headers["Pragma"] = "no-cache"

        return response

    except TicaValidationError as exc:
        db.session.rollback()

        flash(
            exc.message,
            "danger",
        )

        return redirect(url_for("tica.generator"))

    except IntegrityError:
        db.session.rollback()

        flash(
            "No se pudo guardar el registro del archivo generado.",
            "danger",
        )

        return redirect(url_for("tica.generator"))

    except Exception:
        db.session.rollback()

        flash(
            "Ocurrió un error generando el archivo TRM.",
            "danger",
        )

        return redirect(url_for("tica.generator"))


# =========================================================
# API AJAX - Transportistas
# =========================================================

@tica_bp.get("/api/transporters")
@login_required
def api_search_transporters():
    q = (
        request.args.get("q")
        or ""
    ).strip()

    items = search_transporters(
        q,
        limit=20,
        active_only=True,
    )

    return jsonify({
        "ok": True,
        "items": items,
    })


@tica_bp.post("/api/transporters")
@login_required
def api_save_transporter():
    """
    Crea un transportista o actualiza uno existente
    cuando coincide la cédula.
    """
    data = _request_payload()

    try:
        transporter, created = (
            create_or_update_transporter(
                name=data.get("name"),
                identification_number=data.get(
                    "identification_number"
                ),
                user_id=current_user.id,
                is_active=_bool_from_payload(
                    data,
                    default=True,
                ),
            )
        )

        db.session.flush()

        audit_log(
            current_user.id,
            (
                "TICA_TRANSPORTER_CREATED"
                if created
                else "TICA_TRANSPORTER_UPDATED"
            ),
            "tica_transporter",
            transporter.id,
            {
                "name": transporter.name,
                "identification_number": (
                    transporter.identification_number
                ),
            },
        )

        db.session.commit()

        return jsonify({
            "ok": True,
            "created": created,
            "item": {
                "id": transporter.id,
                "name": transporter.name,
                "identification_number": (
                    transporter.identification_number
                ),
                "is_active": (
                    bool(transporter.is_active)
                ),
            },
        })

    except TicaValidationError as exc:
        db.session.rollback()

        return _service_error_json(
            exc,
            status_code=400,
        )

    except IntegrityError:
        db.session.rollback()

        return jsonify({
            "ok": False,
            "error": "TRANSPORTER_DUPLICATE",
            "message": (
                "Ya existe un transportista con "
                "esa identificación."
            ),
        }), 409


# =========================================================
# API AJAX - Choferes
# =========================================================

@tica_bp.get("/api/drivers")
@login_required
def api_search_drivers():
    transporter_id = _optional_int(
        request.args.get("transporter_id")
    )

    if not transporter_id:
        return jsonify({
            "ok": True,
            "items": [],
        })

    q = (
        request.args.get("q")
        or ""
    ).strip()

    items = search_drivers(
        transporter_id=transporter_id,
        query_text=q,
        limit=20,
        active_only=True,
    )

    return jsonify({
        "ok": True,
        "items": items,
    })


@tica_bp.post("/api/drivers")
@login_required
def api_save_driver():
    """
    Crea/actualiza chofer y lo liga a un transportista.
    """
    data = _request_payload()

    try:
        driver, created = create_or_update_driver(
            transporter_id=data.get(
                "transporter_id"
            ),
            name=data.get("name"),
            identification_number=data.get(
                "identification_number"
            ),
            plate=data.get("plate"),
            user_id=current_user.id,
            is_active=_bool_from_payload(
                data,
                default=True,
            ),
        )

        db.session.flush()

        audit_log(
            current_user.id,
            (
                "TICA_DRIVER_CREATED"
                if created
                else "TICA_DRIVER_UPDATED"
            ),
            "tica_driver",
            driver.id,
            {
                "transporter_id": (
                    driver.transporter_id
                ),
                "name": driver.name,
                "identification_number": (
                    driver.identification_number
                ),
                "plate": driver.plate,
            },
        )

        db.session.commit()

        return jsonify({
            "ok": True,
            "created": created,
            "item": {
                "id": driver.id,
                "transporter_id": (
                    driver.transporter_id
                ),
                "name": driver.name,
                "identification_number": (
                    driver.identification_number
                ),
                "plate": driver.plate,
                "is_active": bool(
                    driver.is_active
                ),
            },
        })

    except TicaValidationError as exc:
        db.session.rollback()

        return _service_error_json(
            exc,
            status_code=400,
        )

    except IntegrityError:
        db.session.rollback()

        return jsonify({
            "ok": False,
            "error": "DRIVER_DUPLICATE",
            "message": (
                "Ya existe un chofer con esa identificación."
            ),
        }), 409


# =========================================================
# API AJAX - Ubicaciones
# =========================================================

@tica_bp.get("/api/destinations")
@login_required
def api_search_destinations():
    q = (
        request.args.get("q")
        or ""
    ).strip()

    items = search_destinations(
        q,
        limit=20,
        active_only=True,
    )

    return jsonify({
        "ok": True,
        "items": items,
    })


@tica_bp.post("/api/destinations")
@login_required
def api_save_destination():
    data = _request_payload()

    try:
        destination, created = (
            create_or_update_destination(
                name=data.get("name"),
                code=data.get("code"),
                user_id=current_user.id,
                is_active=_bool_from_payload(
                    data,
                    default=True,
                ),
            )
        )

        db.session.flush()

        audit_log(
            current_user.id,
            (
                "TICA_DESTINATION_CREATED"
                if created
                else "TICA_DESTINATION_UPDATED"
            ),
            "tica_destination",
            destination.id,
            {
                "name": destination.name,
                "code": destination.code,
            },
        )

        db.session.commit()

        return jsonify({
            "ok": True,
            "created": created,
            "item": {
                "id": destination.id,
                "name": destination.name,
                "code": destination.code,
                "is_active": bool(
                    destination.is_active
                ),
            },
        })

    except TicaValidationError as exc:
        db.session.rollback()

        return _service_error_json(
            exc,
            status_code=400,
        )

    except IntegrityError:
        db.session.rollback()

        return jsonify({
            "ok": False,
            "error": "DESTINATION_DUPLICATE",
            "message": (
                "Ya existe una ubicación con ese código."
            ),
        }), 409


# =========================================================
# Historial TRM
# =========================================================

@tica_bp.get("/history")
@login_required
def history():
    """
    Historial paginado.

    No trae todo el histórico.
    No necesita cargar las relaciones completas de transportista,
    chofer y destino porque la tabla conserva snapshots.
    """
    page = request.args.get(
        "page",
        1,
        type=int,
    )

    q = (
        request.args.get("q")
        or ""
    ).strip().upper()

    movement_type = (
        request.args.get("movement_type")
        or ""
    ).strip().upper()

    query = (
        db.session.query(
            TicaGeneratedFile,
            User.username.label("username"),
        )
        .outerjoin(
            User,
            User.id
            == TicaGeneratedFile.created_by_user_id,
        )
        .options(
            noload(TicaGeneratedFile.transporter),
            noload(TicaGeneratedFile.driver),
            noload(TicaGeneratedFile.destination),
        )
    )

    if q:
        like = f"%{q}%"

        query = query.filter(
            db.or_(
                db.func.upper(
                    TicaGeneratedFile.trip_number
                ).like(like),
                db.func.upper(
                    TicaGeneratedFile.container_number
                ).like(like),
                db.func.upper(
                    TicaGeneratedFile.file_name
                ).like(like),
                db.func.upper(
                    TicaGeneratedFile.driver_name
                ).like(like),
                db.func.upper(
                    TicaGeneratedFile.transporter_name_snapshot
                ).like(like),
            )
        )

    if movement_type in {"E", "S"}:
        query = query.filter(
            TicaGeneratedFile.movement_type
            == movement_type
        )

    pagination = (
        query
        .order_by(
            TicaGeneratedFile.created_at.desc(),
            TicaGeneratedFile.id.desc(),
        )
        .paginate(
            page=page,
            per_page=HISTORY_PAGE_SIZE,
            error_out=False,
        )
    )

    items = [
        {
            "record": row[0],
            "username": row[1] or "—",
        }
        for row in pagination.items
    ]

    return render_template(
        "tica/history.html",
        items=items,
        pagination=pagination,
        q=q,
        movement_type=movement_type,
        out_path_display=TICA_OUT_PATH_DISPLAY,
    )


@tica_bp.get("/history/<int:file_id>/download")
@login_required
def history_download(file_id: int):
    generated_file = db.session.get(
        TicaGeneratedFile,
        file_id,
    )

    if not generated_file:
        return jsonify({
            "ok": False,
            "error": "FILE_NOT_FOUND",
        }), 404

    try:
        content_bytes = rebuild_generated_file_bytes(
            generated_file
        )

        response = send_file(
            io.BytesIO(content_bytes),
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=generated_file.file_name,
            max_age=0,
        )

        response.headers["Cache-Control"] = (
            "no-store, no-cache, must-revalidate"
        )

        return response

    except TicaServiceError as exc:
        flash(
            exc.message,
            "danger",
        )

        return redirect(url_for("tica.history"))


@tica_bp.get("/history/<int:file_id>/content")
@login_required
def history_content(file_id: int):
    """
    Permite visualizar el contenido guardado sin regenerarlo.
    """
    row = (
        db.session.query(
            TicaGeneratedFile.id,
            TicaGeneratedFile.file_name,
            TicaGeneratedFile.content_text,
        )
        .filter(
            TicaGeneratedFile.id == file_id
        )
        .first()
    )

    if not row:
        return jsonify({
            "ok": False,
            "error": "FILE_NOT_FOUND",
        }), 404

    if not row.content_text:
        return jsonify({
            "ok": False,
            "error": "CONTENT_NOT_FOUND",
        }), 404

    content_bytes = row.content_text.encode(
        "ISO-8859-1",
        errors="strict",
    )

    return Response(
        content_bytes,
        status=200,
        content_type=(
            "text/plain; charset=ISO-8859-1"
        ),
    )


# =========================================================
# Catálogo - Transportistas
# =========================================================

@tica_bp.get("/transporters")
@login_required
def transporters():
    page = request.args.get(
        "page",
        1,
        type=int,
    )

    q = (
        request.args.get("q")
        or ""
    ).strip().upper()

    query = TicaTransporter.query

    if q:
        like = f"%{q}%"

        query = query.filter(
            db.or_(
                db.func.upper(
                    TicaTransporter.name
                ).like(like),
                db.func.upper(
                    TicaTransporter.identification_number
                ).like(like),
            )
        )

    pagination = (
        query
        .order_by(
            TicaTransporter.name.asc(),
            TicaTransporter.id.asc(),
        )
        .paginate(
            page=page,
            per_page=CATALOG_PAGE_SIZE,
            error_out=False,
        )
    )

    transporter_ids = [
        item.id
        for item in pagination.items
    ]

    driver_counts = {}

    if transporter_ids:
        count_rows = (
            db.session.query(
                TicaDriver.transporter_id,
                db.func.count(
                    TicaDriver.id
                ).label("qty"),
            )
            .filter(
                TicaDriver.transporter_id.in_(
                    transporter_ids
                )
            )
            .group_by(
                TicaDriver.transporter_id
            )
            .all()
        )

        driver_counts = {
            int(row.transporter_id): int(row.qty)
            for row in count_rows
        }

    return render_template(
        "tica/transporters.html",
        items=pagination.items,
        pagination=pagination,
        driver_counts=driver_counts,
        q=q,
    )


# =========================================================
# Catálogo - Choferes
# =========================================================

@tica_bp.get("/drivers")
@login_required
def drivers():
    page = request.args.get(
        "page",
        1,
        type=int,
    )

    q = (
        request.args.get("q")
        or ""
    ).strip().upper()

    transporter_id = request.args.get(
        "transporter_id",
        type=int,
    )

    query = TicaDriver.query

    if transporter_id:
        query = query.filter(
            TicaDriver.transporter_id
            == transporter_id
        )

    if q:
        like = f"%{q}%"

        query = query.filter(
            db.or_(
                db.func.upper(
                    TicaDriver.name
                ).like(like),
                db.func.upper(
                    TicaDriver.identification_number
                ).like(like),
                db.func.upper(
                    TicaDriver.plate
                ).like(like),
            )
        )

    pagination = (
        query
        .order_by(
            TicaDriver.name.asc(),
            TicaDriver.id.asc(),
        )
        .paginate(
            page=page,
            per_page=CATALOG_PAGE_SIZE,
            error_out=False,
        )
    )

    return render_template(
        "tica/drivers.html",
        items=pagination.items,
        pagination=pagination,
        q=q,
        transporter_id=transporter_id,
    )


# =========================================================
# Catálogo - Ubicaciones
# =========================================================

@tica_bp.get("/destinations")
@login_required
def destinations():
    page = request.args.get(
        "page",
        1,
        type=int,
    )

    q = (
        request.args.get("q")
        or ""
    ).strip().upper()

    query = TicaDestination.query

    if q:
        like = f"%{q}%"

        query = query.filter(
            db.or_(
                db.func.upper(
                    TicaDestination.name
                ).like(like),
                db.func.upper(
                    TicaDestination.code
                ).like(like),
            )
        )

    pagination = (
        query
        .order_by(
            TicaDestination.name.asc(),
            TicaDestination.id.asc(),
        )
        .paginate(
            page=page,
            per_page=CATALOG_PAGE_SIZE,
            error_out=False,
        )
    )

    return render_template(
        "tica/destinations.html",
        items=pagination.items,
        pagination=pagination,
        q=q,
    )


# =========================================================
# Activar / desactivar catálogos
# =========================================================

@tica_bp.post("/transporters/<int:item_id>/toggle")
@login_required
def transporter_toggle(item_id: int):
    item = db.session.get(
        TicaTransporter,
        item_id,
    )

    if not item:
        flash(
            "Transportista no encontrado.",
            "danger",
        )
        return redirect(url_for("tica.transporters"))

    item.is_active = not bool(item.is_active)
    item.updated_by_user_id = current_user.id
    item.updated_at = datetime.utcnow()

    audit_log(
        current_user.id,
        "TICA_TRANSPORTER_TOGGLED",
        "tica_transporter",
        item.id,
        {
            "is_active": item.is_active,
        },
    )

    db.session.commit()

    flash(
        "Estado del transportista actualizado.",
        "success",
    )

    return redirect(url_for("tica.transporters"))


@tica_bp.post("/drivers/<int:item_id>/toggle")
@login_required
def driver_toggle(item_id: int):
    item = db.session.get(
        TicaDriver,
        item_id,
    )

    if not item:
        flash(
            "Chofer no encontrado.",
            "danger",
        )
        return redirect(url_for("tica.drivers"))

    item.is_active = not bool(item.is_active)
    item.updated_by_user_id = current_user.id
    item.updated_at = datetime.utcnow()

    audit_log(
        current_user.id,
        "TICA_DRIVER_TOGGLED",
        "tica_driver",
        item.id,
        {
            "is_active": item.is_active,
        },
    )

    db.session.commit()

    flash(
        "Estado del chofer actualizado.",
        "success",
    )

    return redirect(url_for("tica.drivers"))


@tica_bp.post("/destinations/<int:item_id>/toggle")
@login_required
def destination_toggle(item_id: int):
    item = db.session.get(
        TicaDestination,
        item_id,
    )

    if not item:
        flash(
            "Ubicación no encontrada.",
            "danger",
        )
        return redirect(url_for("tica.destinations"))

    item.is_active = not bool(item.is_active)
    item.updated_by_user_id = current_user.id
    item.updated_at = datetime.utcnow()

    audit_log(
        current_user.id,
        "TICA_DESTINATION_TOGGLED",
        "tica_destination",
        item.id,
        {
            "is_active": item.is_active,
        },
    )

    db.session.commit()

    flash(
        "Estado de la ubicación actualizado.",
        "success",
    )

    return redirect(url_for("tica.destinations"))


# =========================================================
# Carga masiva
# =========================================================

@tica_bp.get("/bulk")
@login_required
def bulk_upload():
    recent_imports = (
        TicaImportBatch.query
        .order_by(
            TicaImportBatch.created_at.desc(),
            TicaImportBatch.id.desc(),
        )
        .limit(20)
        .all()
    )

    return render_template(
        "tica/bulk_upload.html",
        recent_imports=recent_imports,
    )


@tica_bp.post("/bulk")
@login_required
def bulk_upload_post():
    import_type = (
        request.form.get("import_type")
        or ""
    ).strip().upper()

    uploaded_file = request.files.get("file")

    if import_type not in {
        IMPORT_TRANSPORTERS,
        IMPORT_DRIVERS,
        IMPORT_DESTINATIONS,
    }:
        flash(
            "Tipo de carga masiva inválido.",
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))

    if not uploaded_file or not uploaded_file.filename:
        flash(
            "Debe seleccionar un archivo Excel.",
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))

    if not _excel_extension(
        uploaded_file.filename
    ):
        flash(
            "El archivo debe ser .xlsx o .xlsm.",
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))

    try:
        batch, summary = run_catalog_import(
            import_type=import_type,
            file_source=uploaded_file.stream,
            original_file_name=uploaded_file.filename,
            user_id=current_user.id,
        )

        audit_log(
            current_user.id,
            "TICA_CATALOG_IMPORT",
            "tica_import_batch",
            batch.id,
            {
                "import_type": import_type,
                "file_name": uploaded_file.filename,
                "total_rows": summary.total_rows,
                "created_rows": summary.created_rows,
                "updated_rows": summary.updated_rows,
                "skipped_rows": summary.skipped_rows,
                "error_rows": summary.error_rows,
                "status": summary.status,
            },
        )

        db.session.commit()

        category = (
            "warning"
            if summary.error_rows
            else "success"
        )

        flash(
            (
                f"Carga procesada. "
                f"Creados: {summary.created_rows}. "
                f"Actualizados: {summary.updated_rows}. "
                f"Omitidos: {summary.skipped_rows}. "
                f"Errores: {summary.error_rows}."
            ),
            category,
        )

        return redirect(url_for("tica.bulk_upload"))

    except TicaImportError as exc:
        db.session.rollback()

        # Guarda registro del intento fallido.
        try:
            failed_batch = TicaImportBatch(
                import_type=import_type,
                original_file_name=(
                    uploaded_file.filename[:255]
                ),
                total_rows=0,
                created_rows=0,
                updated_rows=0,
                skipped_rows=0,
                error_rows=1,
                status=IMPORT_STATUS_ERROR,
                error_message=exc.message[:4000],
                created_by_user_id=current_user.id,
                created_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )

            db.session.add(failed_batch)
            db.session.commit()

        except Exception:
            db.session.rollback()

        flash(
            exc.message,
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))

    except IntegrityError:
        db.session.rollback()

        flash(
            (
                "La carga no pudo completarse por un "
                "dato duplicado o relación inválida."
            ),
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))

    except Exception:
        db.session.rollback()

        flash(
            "Ocurrió un error procesando la carga masiva.",
            "danger",
        )

        return redirect(url_for("tica.bulk_upload"))


# =========================================================
# Descargar plantillas Excel
# =========================================================

@tica_bp.get("/bulk/template/<string:template_type>")
@login_required
def bulk_template(template_type: str):
    template_type = (
        template_type
        or ""
    ).strip().upper()

    if template_type == IMPORT_TRANSPORTERS:
        content = build_transporters_template()
        file_name = "plantilla_transportistas_tica.xlsx"

    elif template_type == IMPORT_DRIVERS:
        content = build_drivers_template()
        file_name = "plantilla_choferes_tica.xlsx"

    elif template_type == IMPORT_DESTINATIONS:
        content = build_destinations_template()
        file_name = "plantilla_ubicaciones_tica.xlsx"

    else:
        return jsonify({
            "ok": False,
            "error": "INVALID_TEMPLATE_TYPE",
        }), 404

    return send_file(
        io.BytesIO(content),
        mimetype=(
            "application/vnd.openxmlformats-"
            "officedocument.spreadsheetml.sheet"
        ),
        as_attachment=True,
        download_name=file_name,
        max_age=0,
    )