# app/blueprints/transport/routes.py

from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import (
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from flask_login import current_user, login_required
from sqlalchemy import func, or_, select
from sqlalchemy.orm import (
    aliased,
    joinedload,
    noload,
    selectinload,
)

from app.blueprints.transport import transport_bp
from app.blueprints.transport.services import (
    DOCUMENT_STATUSES,
    DRIVER_DOCUMENT_TYPES,
    DRIVER_STATUSES,
    INCIDENT_STATUSES,
    INCIDENT_TYPES,
    TRUCK_BONDED_STATUSES,
    TRUCK_STATUSES,
    TransportConflictError,
    TransportNotFoundError,
    TransportServiceError,
    TransportValidationError,
    add_incident_follow_up,
    assign_driver_to_truck,
    cancel_exit_permission,
    cancel_incident,
    create_driver,
    create_exit_permission,
    create_incident,
    create_truck,
    end_assignment,
    get_assignment_or_404,
    get_available_drivers,
    get_available_trucks,
    get_driver_or_404,
    get_exit_permission_or_404,
    get_incident_or_404,
    get_truck_or_404,
    register_exit_permission_return,
    resolve_incident,
    set_driver_status,
    set_truck_status,
    update_driver,
    update_truck,
    upsert_driver_apm_record,
    upsert_driver_document,
    upsert_truck_document,
    update_driver_complete_row,
    bulk_import_transport_excel,
    build_transport_bulk_template,
)
from app.extensions import db
from app.models.site import Site
from app.models.transport import (
    Driver,
    DriverApmRecord,
    DriverDocument,
    DriverExitPermission,
    DriverTruckAssignment,
    TransportDocumentChange,
    TransportIncident,
    TransportIncidentFollowUp,
    Truck,
    TruckDocument,
    TruckOwner,
)
from app.utils.permissions import require_permission


# =========================================================
# CONFIGURACIÓN
# =========================================================
DEFAULT_PER_PAGE = 25
MAX_PER_PAGE = 100


# =========================================================
# HELPERS GENERALES
# =========================================================
def _safe_page() -> int:
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    return max(page, 1)


def _safe_per_page() -> int:
    try:
        per_page = int(
            request.args.get(
                "per_page",
                DEFAULT_PER_PAGE,
            )
        )
    except (TypeError, ValueError):
        per_page = DEFAULT_PER_PAGE

    return max(
        1,
        min(per_page, MAX_PER_PAGE),
    )


def _clean_arg(
    name: str,
    *,
    upper: bool = False,
) -> str:
    value = (
        request.args.get(name)
        or ""
    ).strip()

    return value.upper() if upper else value


def _form_to_dict() -> dict:
    """
    Convierte ImmutableMultiDict a dict normal.

    Se conserva separado para que services.py sea reutilizable
    fuera de una petición Flask.
    """
    return request.form.to_dict()


def _rollback_and_flash(
    exc: Exception,
    *,
    default_message: str,
) -> None:
    db.session.rollback()

    message = str(exc).strip() or default_message

    if isinstance(
        exc,
        (
            TransportValidationError,
            TransportConflictError,
        ),
    ):
        flash(message, "warning")
    elif isinstance(
        exc,
        TransportNotFoundError,
    ):
        flash(message, "danger")
    else:
        flash(default_message, "danger")


def _get_sites() -> list[Site]:
    """
    Consulta pequeña para selectores.

    No carga relaciones de Site.
    """
    stmt = (
        select(Site)
        .order_by(Site.id.asc())
    )

    return list(
        db.session.scalars(stmt).all()
    )


def _get_active_assignment_for_driver_detail(
    driver_id: int,
) -> DriverTruckAssignment | None:
    stmt = (
        select(DriverTruckAssignment)
        .options(
            joinedload(
                DriverTruckAssignment.truck
            ).joinedload(Truck.owner),
            joinedload(
                DriverTruckAssignment.created_by
            ),
            joinedload(
                DriverTruckAssignment.ended_by
            ),
        )
        .where(
            DriverTruckAssignment.driver_id
            == driver_id,
            DriverTruckAssignment.status
            == "ACTIVE",
        )
        .limit(1)
    )

    return db.session.scalar(stmt)


def _get_active_assignment_for_truck_detail(
    truck_id: int,
) -> DriverTruckAssignment | None:
    stmt = (
        select(DriverTruckAssignment)
        .options(
            joinedload(
                DriverTruckAssignment.driver
            ).joinedload(
                Driver.habitual_site
            ),
            joinedload(
                DriverTruckAssignment.created_by
            ),
            joinedload(
                DriverTruckAssignment.ended_by
            ),
        )
        .where(
            DriverTruckAssignment.truck_id
            == truck_id,
            DriverTruckAssignment.status
            == "ACTIVE",
        )
        .limit(1)
    )

    return db.session.scalar(stmt)


# =========================================================
# INICIO DEL MÓDULO
# =========================================================
@transport_bp.get("/")
@login_required
@require_permission("drivers.view")
def index():
    """
    La entrada principal del módulo Transportes lleva directamente
    a la matriz operativa de choferes/cabezales.
    """
    return redirect(
        url_for("transport.drivers_list")
    )


# =========================================================
# CHOFERES
# =========================================================
@transport_bp.get("/drivers")
@login_required
@require_permission("drivers.view")
def drivers_list():
    """
    Matriz principal de choferes y cabezales.

    La lista es GLOBAL para todos los predios.

    habitual_site_id:
        Se utiliza únicamente como distintivo/filtro de patiero.

    Diseño de rendimiento:
    ----------------------
    1. Se pagina primero únicamente por ID de chofer.
    2. Luego se consulta la información completa solamente de
       los choferes visibles en esa página.
    3. Documentos se cargan en una única consulta.
    4. APM se carga en una única consulta.
    5. Se evita N+1.
    """

    # =====================================================
    # 1. PAGINACIÓN Y FILTROS
    # =====================================================
    page = _safe_page()
    per_page = _safe_per_page()

    search = _clean_arg("q")

    status = _clean_arg(
        "status",
        upper=True,
    )

    habitual_site_id = request.args.get(
        "habitual_site_id",
        type=int,
    )

    # =====================================================
    # 2. ALIASES
    # =====================================================
    active_assignment = aliased(
        DriverTruckAssignment
    )

    assigned_truck = aliased(
        Truck
    )

    assigned_owner = aliased(
        TruckOwner
    )

    # =====================================================
    # 3. CONSULTA BASE PARA PAGINACIÓN
    #
    # IMPORTANTE:
    # db.paginate() debe trabajar aquí únicamente con
    # Driver.id.
    #
    # No intentamos paginar Driver + columnas adicionales,
    # porque Flask-SQLAlchemy devuelve solamente el primer
    # objeto escalar.
    # =====================================================
    page_stmt = (
        select(
            Driver.id
        )
        .outerjoin(
            active_assignment,
            (
                active_assignment.driver_id
                == Driver.id
            )
            & (
                active_assignment.status
                == "ACTIVE"
            ),
        )
        .outerjoin(
            assigned_truck,
            assigned_truck.id
            == active_assignment.truck_id,
        )
        .outerjoin(
            assigned_owner,
            assigned_owner.id
            == assigned_truck.owner_id,
        )
    )

    # =====================================================
    # 4. BÚSQUEDA
    # =====================================================
    if search:
        like_value = f"%{search}%"

        page_stmt = page_stmt.where(
            or_(
                Driver.name.ilike(
                    like_value
                ),

                Driver.identification.ilike(
                    like_value
                ),

                Driver.residence.ilike(
                    like_value
                ),

                Driver.phone_1.ilike(
                    like_value
                ),

                Driver.phone_2.ilike(
                    like_value
                ),

                assigned_truck.plate.ilike(
                    like_value
                ),

                assigned_owner.name.ilike(
                    like_value
                ),
            )
        )

    # =====================================================
    # 5. FILTRO ESTADO
    # =====================================================
    if status in DRIVER_STATUSES:
        page_stmt = page_stmt.where(
            Driver.status == status
        )

    # =====================================================
    # 6. FILTRO PATIERO
    #
    # No limita la lista por predio activo.
    #
    # Solamente cuando el usuario selecciona expresamente
    # "Patiero X" se aplica este filtro.
    # =====================================================
    if habitual_site_id:
        page_stmt = page_stmt.where(
            Driver.habitual_site_id
            == habitual_site_id
        )

    # =====================================================
    # 7. ORDEN ALFABÉTICO
    # =====================================================
    page_stmt = (
        page_stmt
        .order_by(
            Driver.name.asc(),
            Driver.id.asc(),
        )
        .distinct()
    )

    # =====================================================
    # 8. PAGINAR IDS
    # =====================================================
    pagination = db.paginate(
        page_stmt,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    # pagination.items contiene IDs escalares.
    driver_ids = list(
        pagination.items
    )

    # =====================================================
    # 9. SIN REGISTROS
    # =====================================================
    if not driver_ids:
        return render_template(
            "transport/drivers_list.html",

            pagination=pagination,
            rows=[],

            sites=_get_sites(),

            filters={
                "q": search,
                "status": status,
                "habitual_site_id": (
                    habitual_site_id
                ),
                "per_page": per_page,
            },

            driver_statuses=DRIVER_STATUSES,
            document_statuses=DOCUMENT_STATUSES,
            driver_document_types=(
                DRIVER_DOCUMENT_TYPES
            ),

            today=date.today(),
        )

    # =====================================================
    # 10. CONSULTA COMPLETA DE LA PÁGINA
    #
    # Aquí sí obtenemos:
    #
    # Driver
    # + asignación
    # + cabezal
    # + propietario
    #
    # Solamente para los IDs visibles.
    # =====================================================
    rows_stmt = (
        select(
            Driver,

            # ---------------------------------------------
            # Asignación
            # ---------------------------------------------
            active_assignment.id.label(
                "active_assignment_id"
            ),

            active_assignment.started_at.label(
                "assignment_started_at"
            ),

            # ---------------------------------------------
            # Cabezal
            # ---------------------------------------------
            assigned_truck.id.label(
                "truck_id"
            ),

            assigned_truck.registration_date.label(
                "registration_date"
            ),

            assigned_truck.registered_site_id.label(
                "truck_site_id"
            ),

            assigned_truck.plate.label(
                "plate"
            ),

            assigned_truck.status.label(
                "truck_status"
            ),

            # ---------------------------------------------
            # Permiso muelle cabezal
            # ---------------------------------------------
            assigned_truck.dock_permit_number.label(
                "truck_dock_permit_number"
            ),

            assigned_truck.dock_permit_expiry_date.label(
                "truck_dock_permit_expiry_date"
            ),

            # ---------------------------------------------
            # TC
            # ---------------------------------------------
            assigned_truck.circulation_card.label(
                "circulation_card"
            ),

            # ---------------------------------------------
            # DEKRA
            # ---------------------------------------------
            assigned_truck.dekra_month.label(
                "dekra_month"
            ),

            assigned_truck.dekra_year.label(
                "dekra_year"
            ),

            # ---------------------------------------------
            # Seguro / AUT
            # ---------------------------------------------
            assigned_truck.insurance_name.label(
                "insurance_name"
            ),

            assigned_truck.insurance_expiry_date.label(
                "insurance_expiry_date"
            ),

            assigned_truck.is_payroll.label(
                "is_payroll"
            ),

            # ---------------------------------------------
            # RT
            # ---------------------------------------------
            assigned_truck.rt_name.label(
                "rt_name"
            ),

            assigned_truck.rt_expiry_date.label(
                "rt_expiry_date"
            ),

            # ---------------------------------------------
            # Otros datos cabezal
            # ---------------------------------------------
            assigned_truck.weights_dimensions.label(
                "weights_dimensions"
            ),

            assigned_truck.policy_number.label(
                "policy_number"
            ),

            assigned_truck.bonded_status.label(
                "bonded_status"
            ),

            # ---------------------------------------------
            # Propietario
            # ---------------------------------------------
            assigned_owner.id.label(
                "owner_id"
            ),

            assigned_owner.name.label(
                "owner_name"
            ),

            assigned_owner.phone.label(
                "owner_phone"
            ),
        )

        .outerjoin(
            active_assignment,
            (
                active_assignment.driver_id
                == Driver.id
            )
            & (
                active_assignment.status
                == "ACTIVE"
            ),
        )

        .outerjoin(
            assigned_truck,
            assigned_truck.id
            == active_assignment.truck_id,
        )

        .outerjoin(
            assigned_owner,
            assigned_owner.id
            == assigned_truck.owner_id,
        )

        .where(
            Driver.id.in_(
                driver_ids
            )
        )

        .options(
            joinedload(
                Driver.habitual_site
            ),

            noload(
                Driver.documents
            ),

            noload(
                Driver.apm_record
            ),

            noload(
                Driver.assignments
            ),

            noload(
                Driver.exit_permissions
            ),

            noload(
                Driver.incidents
            ),
        )

        .order_by(
            Driver.name.asc(),
            Driver.id.asc(),
        )
    )

    raw_rows = (
        db.session.execute(
            rows_stmt
        )
        .unique()
        .all()
    )

    # =====================================================
    # 11. DOCUMENTOS DE LOS CHOFERES DE ESTA PÁGINA
    #
    # Una sola consulta.
    # =====================================================
    documents_by_driver: dict[
        int,
        dict[str, dict]
    ] = {}

    documents_stmt = (
        select(
            DriverDocument.driver_id,
            DriverDocument.document_type,
            DriverDocument.document_number,
            DriverDocument.status,
            DriverDocument.issue_date,
            DriverDocument.expiry_date,
            DriverDocument.no_expiry,
            DriverDocument.notes,
        )
        .where(
            DriverDocument.driver_id.in_(
                driver_ids
            )
        )
    )

    document_rows = (
        db.session.execute(
            documents_stmt
        )
        .all()
    )

    for document_row in document_rows:
        driver_id = (
            document_row.driver_id
        )

        if (
            driver_id
            not in documents_by_driver
        ):
            documents_by_driver[
                driver_id
            ] = {}

        documents_by_driver[
            driver_id
        ][
            document_row.document_type
        ] = {
            "document_number": (
                document_row.document_number
            ),

            "status": (
                document_row.status
            ),

            "issue_date": (
                document_row.issue_date
            ),

            "expiry_date": (
                document_row.expiry_date
            ),

            "no_expiry": (
                bool(
                    document_row.no_expiry
                )
            ),

            "notes": (
                document_row.notes
            ),
        }

    # =====================================================
    # 12. APM
    #
    # Una sola consulta.
    # =====================================================
    apm_by_driver: dict[
        int,
        dict
    ] = {}

    apm_stmt = (
        select(
            DriverApmRecord.driver_id,
            DriverApmRecord.training_status,
            DriverApmRecord.card_status,
            DriverApmRecord.card_number,
            DriverApmRecord.expiry_mode,
            DriverApmRecord.expiry_date,
            DriverApmRecord.notes,
        )
        .where(
            DriverApmRecord.driver_id.in_(
                driver_ids
            )
        )
    )

    apm_rows = (
        db.session.execute(
            apm_stmt
        )
        .all()
    )

    for apm_row in apm_rows:
        apm_by_driver[
            apm_row.driver_id
        ] = {
            "training_status": (
                apm_row.training_status
            ),

            "card_status": (
                apm_row.card_status
            ),

            "card_number": (
                apm_row.card_number
            ),

            "expiry_mode": (
                apm_row.expiry_mode
            ),

            "expiry_date": (
                apm_row.expiry_date
            ),

            "notes": (
                apm_row.notes
            ),
        }

    # =====================================================
    # 13. ARMAR MATRIZ
    # =====================================================
    matrix_rows = []

    today = date.today()

    document_labels = {
        "DOCK_PERMIT": (
            "Permiso muelle chofer"
        ),

        "GENERAL_CARD": (
            "Carnet"
        ),

        "CHEMICAL_PERMIT": (
            "Permiso químico"
        ),

        "LICENSE": (
            "Licencia"
        ),

        "CRIMINAL_RECORD": (
            "Hoja delincuencia"
        ),
    }

    for row in raw_rows:
        # Aquí SÍ es una Row de SQLAlchemy.
        driver = row[0]

        documents = (
            documents_by_driver.get(
                driver.id,
                {},
            )
        )

        apm = (
            apm_by_driver.get(
                driver.id
            )
        )

        pending_items: list[str] = []
        expired_items: list[str] = []

        # =================================================
        # DOCUMENTOS DEL CHOFER
        # =================================================
        for (
            document_type,
            document_label,
        ) in document_labels.items():

            document = documents.get(
                document_type
            )

            if document is None:
                pending_items.append(
                    document_label
                )

                continue

            document_status = (
                document.get(
                    "status"
                )
                or "PENDING"
            )

            if (
                document_status
                == "PENDING"
            ):
                pending_items.append(
                    document_label
                )

            elif (
                document_status
                == "EXPIRED"
            ):
                expired_items.append(
                    document_label
                )

            elif (
                document_status
                != "NOT_APPLICABLE"
                and not document.get(
                    "no_expiry"
                )
                and document.get(
                    "expiry_date"
                )
                and document[
                    "expiry_date"
                ] < today
            ):
                expired_items.append(
                    document_label
                )

        # =================================================
        # APM
        # =================================================
        if apm is None:
            pending_items.append(
                "Capacitación APM"
            )

            pending_items.append(
                "Carnet APM"
            )

        else:
            if (
                apm.get(
                    "training_status"
                )
                != "YES"
            ):
                pending_items.append(
                    "Capacitación APM"
                )

            card_status = (
                apm.get(
                    "card_status"
                )
                or "PENDING"
            )

            if (
                card_status
                == "PENDING"
            ):
                pending_items.append(
                    "Carnet APM"
                )

            elif (
                card_status
                == "EXPIRED"
            ):
                expired_items.append(
                    "Carnet APM"
                )

            elif (
                apm.get(
                    "expiry_mode"
                )
                == "DATE"
                and apm.get(
                    "expiry_date"
                )
                and apm[
                    "expiry_date"
                ] < today
            ):
                expired_items.append(
                    "Carnet APM"
                )

        # =================================================
        # CABEZAL
        # =================================================
        if not row.truck_id:
            pending_items.append(
                "Cabezal sin asignar"
            )

        # =================================================
        # OBJETO PARA TEMPLATE
        # =================================================
        matrix_rows.append({
            # ---------------------------------------------
            # Chofer
            # ---------------------------------------------
            "driver": driver,

            # ---------------------------------------------
            # Fecha ingreso
            # ---------------------------------------------
            "registration_date": (
                row.registration_date
            ),

            # ---------------------------------------------
            # Asignación
            # ---------------------------------------------
            "active_assignment_id": (
                row.active_assignment_id
            ),

            "assignment_started_at": (
                row.assignment_started_at
            ),

            # ---------------------------------------------
            # Cabezal
            # ---------------------------------------------
            "truck": (
                {
                    "id": (
                        row.truck_id
                    ),

                    "plate": (
                        row.plate
                    ),

                    "site_id": (
                        row.truck_site_id
                    ),

                    "status": (
                        row.truck_status
                    ),

                    "dock_permit_number": (
                        row.truck_dock_permit_number
                    ),

                    "dock_permit_expiry_date": (
                        row.truck_dock_permit_expiry_date
                    ),

                    "circulation_card": (
                        row.circulation_card
                    ),

                    "dekra_month": (
                        row.dekra_month
                    ),

                    "dekra_year": (
                        row.dekra_year
                    ),

                    "insurance_name": (
                        row.insurance_name
                    ),

                    "insurance_expiry_date": (
                        row.insurance_expiry_date
                    ),

                    "is_payroll": (
                        bool(
                            row.is_payroll
                        )
                    ),

                    "rt_name": (
                        row.rt_name
                    ),

                    "rt_expiry_date": (
                        row.rt_expiry_date
                    ),

                    "weights_dimensions": (
                        row.weights_dimensions
                    ),

                    "policy_number": (
                        row.policy_number
                    ),

                    "bonded_status": (
                        row.bonded_status
                    ),
                }
                if row.truck_id
                else None
            ),

            # ---------------------------------------------
            # Propietario
            # ---------------------------------------------
            "owner": (
                {
                    "id": (
                        row.owner_id
                    ),

                    "name": (
                        row.owner_name
                    ),

                    "phone": (
                        row.owner_phone
                    ),
                }
                if row.owner_id
                else None
            ),

            # ---------------------------------------------
            # Documentos
            # ---------------------------------------------
            "documents": documents,

            # ---------------------------------------------
            # APM
            # ---------------------------------------------
            "apm": apm,

            # ---------------------------------------------
            # Pendientes
            # ---------------------------------------------
            "pending_items": (
                pending_items
            ),

            "expired_items": (
                expired_items
            ),

            "pending_count": (
                len(
                    pending_items
                )
                + len(
                    expired_items
                )
            ),
        })

    # =====================================================
    # 14. TEMPLATE
    # =====================================================
    return render_template(
        "transport/drivers_list.html",

        pagination=pagination,

        rows=matrix_rows,

        sites=_get_sites(),

        filters={
            "q": search,

            "status": status,

            "habitual_site_id": (
                habitual_site_id
            ),

            "per_page": (
                per_page
            ),
        },

        driver_statuses=(
            DRIVER_STATUSES
        ),

        document_statuses=(
            DOCUMENT_STATUSES
        ),

        driver_document_types=(
            DRIVER_DOCUMENT_TYPES
        ),

        today=today,
    )


@transport_bp.post(
    "/drivers/<int:driver_id>/inline-update"
)
@login_required
@require_permission("drivers.actions")
def driver_inline_update(driver_id: int):
    """
    Guarda únicamente una fila de la matriz de choferes.

    La actualización es transaccional:

    - chofer;
    - documentos;
    - APM;
    - asignación activa.

    Si alguna validación falla, no se guarda ninguna parte de la fila.
    """
    try:
        driver = get_driver_or_404(
            driver_id
        )

        update_driver_complete_row(
            driver,
            _form_to_dict(),
            user_id=current_user.id,
        )

        flash(
            f"Se guardaron los cambios de {driver.name}.",
            "success",
        )

    except TransportNotFoundError:
        db.session.rollback()
        abort(404)

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible guardar la fila del chofer."
            ),
        )

    # Conserva la página y los filtros actuales de la matriz.
    next_url = (
        request.form.get("next")
        or request.args.get("next")
    )

    if (
        next_url
        and next_url.startswith("/")
        and not next_url.startswith("//")
    ):
        return redirect(next_url)

    return redirect(
        url_for(
            "transport.drivers_list"
        )
    )


@transport_bp.route(
    "/drivers/new",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.actions")
def driver_create():
    if request.method == "POST":
        try:
            driver = create_driver(
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El chofer fue creado correctamente.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.driver_detail",
                    driver_id=driver.id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible crear el chofer."
                ),
            )

    return render_template(
        "transport/driver_form.html",
        driver=None,
        sites=_get_sites(),
        driver_statuses=DRIVER_STATUSES,
        mode="create",
    )


@transport_bp.get(
    "/drivers/<int:driver_id>"
)
@login_required
@require_permission("drivers.view")
def driver_detail(driver_id: int):
    stmt = (
        select(Driver)
        .options(
            joinedload(
                Driver.habitual_site
            ),
            joinedload(
                Driver.created_by
            ),
            joinedload(
                Driver.updated_by
            ),
            selectinload(
                Driver.documents
            ),
            selectinload(
                Driver.apm_record
            ),
            noload(
                Driver.assignments
            ),
            noload(
                Driver.exit_permissions
            ),
            noload(
                Driver.incidents
            ),
        )
        .where(
            Driver.id == driver_id
        )
    )

    driver = db.session.scalar(stmt)

    if driver is None:
        abort(404)

    active_assignment = (
        _get_active_assignment_for_driver_detail(
            driver.id
        )
    )

    assignment_stmt = (
        select(DriverTruckAssignment)
        .options(
            joinedload(
                DriverTruckAssignment.truck
            ).joinedload(Truck.owner),
            joinedload(
                DriverTruckAssignment.created_by
            ),
            joinedload(
                DriverTruckAssignment.ended_by
            ),
        )
        .where(
            DriverTruckAssignment.driver_id
            == driver.id
        )
        .order_by(
            DriverTruckAssignment.started_at.desc(),
            DriverTruckAssignment.id.desc(),
        )
        .limit(20)
    )

    assignments = list(
        db.session.scalars(
            assignment_stmt
        ).unique().all()
    )

    incident_stmt = (
        select(TransportIncident)
        .options(
            joinedload(
                TransportIncident.truck
            ),
            joinedload(
                TransportIncident.reported_by
            ),
            noload(
                TransportIncident.follow_ups
            ),
        )
        .where(
            TransportIncident.driver_id
            == driver.id
        )
        .order_by(
            TransportIncident.occurred_at.desc(),
            TransportIncident.id.desc(),
        )
        .limit(10)
    )

    incidents = list(
        db.session.scalars(
            incident_stmt
        ).unique().all()
    )

    permission_stmt = (
        select(DriverExitPermission)
        .options(
            joinedload(
                DriverExitPermission.truck
            ),
            joinedload(
                DriverExitPermission.authorized_by
            ),
            joinedload(
                DriverExitPermission.returned_by
            ),
        )
        .where(
            DriverExitPermission.driver_id
            == driver.id
        )
        .order_by(
            DriverExitPermission.departure_at.desc(),
            DriverExitPermission.id.desc(),
        )
        .limit(10)
    )

    exit_permissions = list(
        db.session.scalars(
            permission_stmt
        ).unique().all()
    )

    return render_template(
        "transport/driver_detail.html",
        driver=driver,
        active_assignment=active_assignment,
        assignments=assignments,
        incidents=incidents,
        exit_permissions=exit_permissions,
        driver_document_types=(
            DRIVER_DOCUMENT_TYPES
        ),
        document_statuses=DOCUMENT_STATUSES,
    )


@transport_bp.route(
    "/drivers/<int:driver_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.actions")
def driver_edit(driver_id: int):
    try:
        driver = get_driver_or_404(
            driver_id
        )
    except TransportNotFoundError:
        abort(404)

    if request.method == "POST":
        try:
            update_driver(
                driver,
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El chofer fue actualizado correctamente.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.driver_detail",
                    driver_id=driver.id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible actualizar el chofer."
                ),
            )

    return render_template(
        "transport/driver_form.html",
        driver=driver,
        sites=_get_sites(),
        driver_statuses=DRIVER_STATUSES,
        mode="edit",
    )


@transport_bp.post(
    "/drivers/<int:driver_id>/status"
)
@login_required
@require_permission("drivers.actions")
def driver_change_status(driver_id: int):
    try:
        driver = get_driver_or_404(
            driver_id
        )

        set_driver_status(
            driver,
            request.form.get("status"),
            user_id=current_user.id,
        )

        flash(
            "El estado del chofer fue actualizado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible actualizar el estado."
            ),
        )

    return redirect(
        url_for(
            "transport.driver_detail",
            driver_id=driver_id,
        )
    )


@transport_bp.post(
    "/drivers/<int:driver_id>/documents/<string:document_type>"
)
@login_required
@require_permission("drivers.actions")
def driver_document_save(
    driver_id: int,
    document_type: str,
):
    try:
        driver = get_driver_or_404(
            driver_id
        )

        upsert_driver_document(
            driver,
            document_type,
            _form_to_dict(),
            user_id=current_user.id,
        )

        flash(
            "El documento del chofer fue guardado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible guardar el documento."
            ),
        )

    return redirect(
        url_for(
            "transport.driver_detail",
            driver_id=driver_id,
        )
    )


@transport_bp.post(
    "/drivers/<int:driver_id>/apm"
)
@login_required
@require_permission("drivers.actions")
def driver_apm_save(driver_id: int):
    try:
        driver = get_driver_or_404(
            driver_id
        )

        upsert_driver_apm_record(
            driver,
            _form_to_dict(),
            user_id=current_user.id,
        )

        flash(
            "La información APM fue guardada.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible guardar la información APM."
            ),
        )

    return redirect(
        url_for(
            "transport.driver_detail",
            driver_id=driver_id,
        )
    )

@transport_bp.get(
    "/drivers/bulk-upload/template"
)
@login_required
@require_permission("drivers.import")
def drivers_bulk_template():
    """
    Descarga la plantilla oficial para la carga masiva
    de choferes y cabezales.

    No consulta PostgreSQL.
    El Excel se genera directamente en memoria.
    """

    template_file = (
        build_transport_bulk_template()
    )

    return send_file(
        template_file,
        as_attachment=True,
        download_name=(
            "plantilla_carga_masiva_choferes.xlsx"
        ),
        mimetype=(
            "application/"
            "vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

@transport_bp.get("/drivers/bulk-upload")
@login_required
@require_permission("drivers.import")
def drivers_bulk_upload():
    return render_template(
        "transport/drivers_bulk_upload.html",
        result=None,
        sites=_get_sites(),
    )


@transport_bp.post("/drivers/bulk-upload")
@login_required
@require_permission("drivers.import")
def drivers_bulk_upload_post():
    """
    Procesa la carga masiva de choferes y cabezales.

    El predio activo se utiliza únicamente como dato técnico
    para registered_site_id de cabezales nuevos.

    habitual_site_id se utiliza exclusivamente como distintivo
    de patiero cuando el usuario lo selecciona expresamente.
    """

    # =====================================================
    # 1. ARCHIVO
    # =====================================================
    file = request.files.get(
        "file"
    )

    if (
        file is None
        or not file.filename
    ):
        flash(
            "Debe seleccionar un archivo Excel.",
            "warning",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    # =====================================================
    # 2. VALIDAR EXTENSIÓN
    # =====================================================
    filename = (
        file.filename
        or ""
    ).strip().lower()

    if not filename.endswith(
        (
            ".xlsx",
            ".xlsm",
        )
    ):
        flash(
            "El archivo debe tener formato .xlsx o .xlsm.",
            "warning",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    # =====================================================
    # 3. PATIERO
    #
    # Es OPCIONAL.
    #
    # None:
    #   chofer normal
    #
    # ID:
    #   patiero del predio seleccionado
    # =====================================================
    habitual_site_id = request.form.get(
        "habitual_site_id",
        type=int,
    )

    # =====================================================
    # 4. PREDIO TÉCNICO PARA CABEZALES NUEVOS
    #
    # No viene del formulario.
    #
    # Se usa el predio activo únicamente porque la BD exige
    # registered_site_id para Truck.
    #
    # NO condiciona la lista ni la operación del chofer.
    # =====================================================
    registered_site_id = getattr(
        g,
        "active_site_id",
        None,
    )

    if not registered_site_id:
        flash(
            (
                "No fue posible determinar el predio activo "
                "para registrar técnicamente los cabezales."
            ),
            "danger",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    # =====================================================
    # 5. PROCESAR
    # =====================================================
    try:
        result = (
            bulk_import_transport_excel(
                file.stream,
                user_id=current_user.id,

                # Predio técnico del Truck.
                registered_site_id=(
                    registered_site_id
                ),

                # Solo distintivo patiero.
                habitual_site_id=(
                    habitual_site_id
                ),
            )
        )

    except TransportValidationError as exc:
        db.session.rollback()

        flash(
            str(exc),
            "warning",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    except TransportServiceError as exc:
        db.session.rollback()

        flash(
            str(exc),
            "danger",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error inesperado en carga masiva "
            "de choferes/cabezales."
        )

        flash(
            (
                "Ocurrió un error inesperado durante "
                "la carga masiva."
            ),
            "danger",
        )

        return render_template(
            "transport/drivers_bulk_upload.html",
            result=None,
            sites=_get_sites(),
        )

    # =====================================================
    # 6. MENSAJE FINAL
    # =====================================================
    if result.get(
        "error_count",
        0,
    ):
        flash(
            (
                "Carga completada con observaciones. "
                f"Procesadas: {result.get('processed', 0)}. "
                f"Omitidas: {result.get('skipped', 0)}. "
                f"Observaciones: {result.get('error_count', 0)}."
            ),
            "warning",
        )

    else:
        flash(
            (
                "Carga masiva completada correctamente. "
                f"Se procesaron "
                f"{result.get('processed', 0)} fila(s)."
            ),
            "success",
        )

    # =====================================================
    # 7. MOSTRAR RESULTADO
    # =====================================================
    return render_template(
        "transport/drivers_bulk_upload.html",
        result=result,
        sites=_get_sites(),
    )

# =========================================================
# CABEZALES
# =========================================================
@transport_bp.get("/trucks")
@login_required
@require_permission("drivers.view")
def trucks_list():
    page = _safe_page()
    per_page = _safe_per_page()

    search = _clean_arg("q")
    status = _clean_arg(
        "status",
        upper=True,
    )
    bonded_status = _clean_arg(
        "bonded_status",
        upper=True,
    )

    registered_site_id = request.args.get(
        "registered_site_id",
        type=int,
    )

    active_assignment = aliased(
        DriverTruckAssignment
    )

    assigned_driver = aliased(Driver)

    stmt = (
        select(
            Truck,
            active_assignment.id.label(
                "active_assignment_id"
            ),
            assigned_driver.id.label(
                "assigned_driver_id"
            ),
            assigned_driver.name.label(
                "assigned_driver_name"
            ),
        )
        .outerjoin(
            active_assignment,
            (
                active_assignment.truck_id
                == Truck.id
            )
            & (
                active_assignment.status
                == "ACTIVE"
            ),
        )
        .outerjoin(
            assigned_driver,
            assigned_driver.id
            == active_assignment.driver_id,
        )
        .options(
            joinedload(
                Truck.registered_site
            ),
            joinedload(
                Truck.owner
            ),
            noload(
                Truck.documents
            ),
            noload(
                Truck.assignments
            ),
            noload(
                Truck.exit_permissions
            ),
            noload(
                Truck.incidents
            ),
        )
        .order_by(
            Truck.plate.asc(),
            Truck.id.asc(),
        )
    )

    if search:
        like_value = f"%{search}%"

        stmt = stmt.where(
            or_(
                Truck.plate.ilike(
                    like_value
                ),
                Truck.policy_number.ilike(
                    like_value
                ),
                Truck.dock_permit_number.ilike(
                    like_value
                ),
                TruckOwner.name.ilike(
                    like_value
                ),
                assigned_driver.name.ilike(
                    like_value
                ),
            )
        ).outerjoin(
            TruckOwner,
            TruckOwner.id == Truck.owner_id,
        )

    if status in TRUCK_STATUSES:
        stmt = stmt.where(
            Truck.status == status
        )

    if (
        bonded_status
        in TRUCK_BONDED_STATUSES
    ):
        stmt = stmt.where(
            Truck.bonded_status
            == bonded_status
        )

    if registered_site_id:
        stmt = stmt.where(
            Truck.registered_site_id
            == registered_site_id
        )

    pagination = db.paginate(
        stmt,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "transport/trucks_list.html",
        pagination=pagination,
        rows=pagination.items,
        sites=_get_sites(),
        truck_statuses=TRUCK_STATUSES,
        bonded_statuses=(
            TRUCK_BONDED_STATUSES
        ),
        filters={
            "q": search,
            "status": status,
            "bonded_status": bonded_status,
            "registered_site_id": (
                registered_site_id
            ),
            "per_page": per_page,
        },
    )


@transport_bp.route(
    "/trucks/new",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.actions")
def truck_create():
    if request.method == "POST":
        try:
            truck = create_truck(
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El cabezal fue creado correctamente.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.truck_detail",
                    truck_id=truck.id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible crear el cabezal."
                ),
            )

    return render_template(
        "transport/truck_form.html",
        truck=None,
        sites=_get_sites(),
        truck_statuses=TRUCK_STATUSES,
        bonded_statuses=(
            TRUCK_BONDED_STATUSES
        ),
        mode="create",
    )


@transport_bp.get(
    "/trucks/<int:truck_id>"
)
@login_required
@require_permission("drivers.view")
def truck_detail(truck_id: int):
    stmt = (
        select(Truck)
        .options(
            joinedload(
                Truck.registered_site
            ),
            joinedload(
                Truck.owner
            ),
            joinedload(
                Truck.created_by
            ),
            joinedload(
                Truck.updated_by
            ),
            selectinload(
                Truck.documents
            ),
            noload(
                Truck.assignments
            ),
            noload(
                Truck.exit_permissions
            ),
            noload(
                Truck.incidents
            ),
        )
        .where(
            Truck.id == truck_id
        )
    )

    truck = db.session.scalar(stmt)

    if truck is None:
        abort(404)

    active_assignment = (
        _get_active_assignment_for_truck_detail(
            truck.id
        )
    )

    assignment_stmt = (
        select(DriverTruckAssignment)
        .options(
            joinedload(
                DriverTruckAssignment.driver
            ).joinedload(
                Driver.habitual_site
            ),
            joinedload(
                DriverTruckAssignment.created_by
            ),
            joinedload(
                DriverTruckAssignment.ended_by
            ),
        )
        .where(
            DriverTruckAssignment.truck_id
            == truck.id
        )
        .order_by(
            DriverTruckAssignment.started_at.desc(),
            DriverTruckAssignment.id.desc(),
        )
        .limit(20)
    )

    assignments = list(
        db.session.scalars(
            assignment_stmt
        ).unique().all()
    )

    incident_stmt = (
        select(TransportIncident)
        .options(
            joinedload(
                TransportIncident.driver
            ),
            joinedload(
                TransportIncident.reported_by
            ),
            noload(
                TransportIncident.follow_ups
            ),
        )
        .where(
            TransportIncident.truck_id
            == truck.id
        )
        .order_by(
            TransportIncident.occurred_at.desc(),
            TransportIncident.id.desc(),
        )
        .limit(10)
    )

    incidents = list(
        db.session.scalars(
            incident_stmt
        ).unique().all()
    )

    return render_template(
        "transport/truck_detail.html",
        truck=truck,
        active_assignment=active_assignment,
        assignments=assignments,
        incidents=incidents,
        document_statuses=DOCUMENT_STATUSES,
    )


@transport_bp.route(
    "/trucks/<int:truck_id>/edit",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.actions")
def truck_edit(truck_id: int):
    try:
        truck = get_truck_or_404(
            truck_id
        )
    except TransportNotFoundError:
        abort(404)

    if request.method == "POST":
        try:
            update_truck(
                truck,
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El cabezal fue actualizado correctamente.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.truck_detail",
                    truck_id=truck.id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible actualizar el cabezal."
                ),
            )

    return render_template(
        "transport/truck_form.html",
        truck=truck,
        sites=_get_sites(),
        truck_statuses=TRUCK_STATUSES,
        bonded_statuses=(
            TRUCK_BONDED_STATUSES
        ),
        mode="edit",
    )


@transport_bp.post(
    "/trucks/<int:truck_id>/status"
)
@login_required
@require_permission("drivers.actions")
def truck_change_status(truck_id: int):
    try:
        truck = get_truck_or_404(
            truck_id
        )

        set_truck_status(
            truck,
            request.form.get("status"),
            user_id=current_user.id,
        )

        flash(
            "El estado del cabezal fue actualizado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible actualizar el estado."
            ),
        )

    return redirect(
        url_for(
            "transport.truck_detail",
            truck_id=truck_id,
        )
    )


@transport_bp.post(
    "/trucks/<int:truck_id>/documents/<string:document_type>"
)
@login_required
@require_permission("drivers.actions")
def truck_document_save(
    truck_id: int,
    document_type: str,
):
    try:
        truck = get_truck_or_404(
            truck_id
        )

        upsert_truck_document(
            truck,
            document_type,
            _form_to_dict(),
            user_id=current_user.id,
        )

        flash(
            "El documento del cabezal fue guardado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible guardar el documento."
            ),
        )

    return redirect(
        url_for(
            "transport.truck_detail",
            truck_id=truck_id,
        )
    )


# =========================================================
# ASIGNACIONES
# =========================================================
@transport_bp.route(
    "/assignments/new",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.actions")
def assignment_create():
    selected_driver_id = request.args.get(
        "driver_id",
        type=int,
    )

    selected_truck_id = request.args.get(
        "truck_id",
        type=int,
    )

    if request.method == "POST":
        try:
            assignment = assign_driver_to_truck(
                driver_id=request.form.get(
                    "driver_id",
                    type=int,
                ),
                truck_id=request.form.get(
                    "truck_id",
                    type=int,
                ),
                user_id=current_user.id,
                started_at=request.form.get(
                    "started_at"
                ),
                notes=request.form.get(
                    "notes"
                ),
                replace_existing=(
                    request.form.get(
                        "replace_existing"
                    )
                    == "1"
                ),
                replacement_reason=(
                    request.form.get(
                        "replacement_reason"
                    )
                ),
            )

            flash(
                "El chofer fue asignado al cabezal.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.driver_detail",
                    driver_id=assignment.driver_id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible realizar la asignación."
                ),
            )

    available_drivers = get_available_drivers(
        limit=100
    )

    available_trucks = get_available_trucks(
        limit=100
    )

    selected_driver = None
    selected_truck = None

    if selected_driver_id:
        selected_driver = db.session.get(
            Driver,
            selected_driver_id,
        )

        if (
            selected_driver
            and all(
                driver.id
                != selected_driver.id
                for driver in available_drivers
            )
        ):
            available_drivers.insert(
                0,
                selected_driver,
            )

    if selected_truck_id:
        selected_truck = db.session.get(
            Truck,
            selected_truck_id,
        )

        if (
            selected_truck
            and all(
                truck.id
                != selected_truck.id
                for truck in available_trucks
            )
        ):
            available_trucks.insert(
                0,
                selected_truck,
            )

    return render_template(
        "transport/assignment_form.html",
        available_drivers=available_drivers,
        available_trucks=available_trucks,
        selected_driver_id=selected_driver_id,
        selected_truck_id=selected_truck_id,
    )


@transport_bp.post(
    "/assignments/<int:assignment_id>/end"
)
@login_required
@require_permission("drivers.actions")
def assignment_end(assignment_id: int):
    driver_id = None
    truck_id = None

    try:
        assignment = get_assignment_or_404(
            assignment_id
        )

        driver_id = assignment.driver_id
        truck_id = assignment.truck_id

        end_assignment(
            assignment,
            user_id=current_user.id,
            ended_at=request.form.get(
                "ended_at"
            ),
            end_reason=request.form.get(
                "end_reason"
            ),
            notes=request.form.get(
                "notes"
            ),
        )

        flash(
            "La asignación fue finalizada.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible finalizar la asignación."
            ),
        )

    next_url = request.form.get("next")

    if (
        next_url
        and next_url.startswith("/")
        and not next_url.startswith("//")
    ):
        return redirect(next_url)

    if driver_id:
        return redirect(
            url_for(
                "transport.driver_detail",
                driver_id=driver_id,
            )
        )

    if truck_id:
        return redirect(
            url_for(
                "transport.truck_detail",
                truck_id=truck_id,
            )
        )

    return redirect(
        url_for(
            "transport.drivers_list"
        )
    )


# =========================================================
# PERMISOS DE SALIDA
# =========================================================
@transport_bp.get(
    "/exit-permissions"
)
@login_required
@require_permission("drivers.history.view")
def exit_permissions_list():
    page = _safe_page()
    per_page = _safe_per_page()

    search = _clean_arg("q")
    status = _clean_arg(
        "status",
        upper=True,
    )

    stmt = (
        select(DriverExitPermission)
        .options(
            joinedload(
                DriverExitPermission.driver
            ),
            joinedload(
                DriverExitPermission.truck
            ),
            joinedload(
                DriverExitPermission.authorized_by
            ),
            joinedload(
                DriverExitPermission.returned_by
            ),
        )
        .order_by(
            DriverExitPermission.departure_at.desc(),
            DriverExitPermission.id.desc(),
        )
    )

    if search:
        like_value = f"%{search}%"

        stmt = (
            stmt
            .join(
                Driver,
                Driver.id
                == DriverExitPermission.driver_id,
            )
            .outerjoin(
                Truck,
                Truck.id
                == DriverExitPermission.truck_id,
            )
            .where(
                or_(
                    Driver.name.ilike(
                        like_value
                    ),
                    Driver.identification.ilike(
                        like_value
                    ),
                    Truck.plate.ilike(
                        like_value
                    ),
                    DriverExitPermission.destination.ilike(
                        like_value
                    ),
                    DriverExitPermission.reason.ilike(
                        like_value
                    ),
                )
            )
        )

    if status in {
        "AUTHORIZED",
        "RETURNED",
        "CANCELLED",
    }:
        stmt = stmt.where(
            DriverExitPermission.status
            == status
        )

    pagination = db.paginate(
        stmt,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "transport/exit_permissions_list.html",
        pagination=pagination,
        permissions=pagination.items,
        filters={
            "q": search,
            "status": status,
            "per_page": per_page,
        },
    )


@transport_bp.route(
    "/exit-permissions/new",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.operations")
def exit_permission_create():
    selected_driver_id = request.args.get(
        "driver_id",
        type=int,
    )

    selected_truck_id = request.args.get(
        "truck_id",
        type=int,
    )

    if request.method == "POST":
        try:
            permission = create_exit_permission(
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El permiso de salida fue registrado.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.driver_detail",
                    driver_id=permission.driver_id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible registrar el permiso."
                ),
            )

    drivers_stmt = (
        select(Driver)
        .options(
            noload(
                Driver.documents
            ),
            noload(
                Driver.apm_record
            ),
            noload(
                Driver.assignments
            ),
            noload(
                Driver.exit_permissions
            ),
            noload(
                Driver.incidents
            ),
        )
        .where(
            Driver.status == "ACTIVE"
        )
        .order_by(
            Driver.name.asc()
        )
        .limit(100)
    )

    trucks_stmt = (
        select(Truck)
        .options(
            joinedload(
                Truck.owner
            ),
            noload(
                Truck.documents
            ),
            noload(
                Truck.assignments
            ),
            noload(
                Truck.exit_permissions
            ),
            noload(
                Truck.incidents
            ),
        )
        .where(
            Truck.status == "ACTIVE"
        )
        .order_by(
            Truck.plate.asc()
        )
        .limit(100)
    )

    drivers = list(
        db.session.scalars(
            drivers_stmt
        ).all()
    )

    trucks = list(
        db.session.scalars(
            trucks_stmt
        ).unique().all()
    )

    return render_template(
        "transport/exit_permission_form.html",
        drivers=drivers,
        trucks=trucks,
        selected_driver_id=selected_driver_id,
        selected_truck_id=selected_truck_id,
        now=datetime.now(),
    )


@transport_bp.post(
    "/exit-permissions/<int:permission_id>/return"
)
@login_required
@require_permission("drivers.operations")
def exit_permission_return(permission_id: int):
    driver_id = None

    try:
        permission = get_exit_permission_or_404(
            permission_id
        )

        driver_id = permission.driver_id

        register_exit_permission_return(
            permission,
            user_id=current_user.id,
            actual_return_at=request.form.get(
                "actual_return_at"
            ),
            notes=request.form.get(
                "notes"
            ),
        )

        flash(
            "El regreso fue registrado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible registrar el regreso."
            ),
        )

    if driver_id:
        return redirect(
            url_for(
                "transport.driver_detail",
                driver_id=driver_id,
            )
        )

    return redirect(
        url_for(
            "transport.exit_permissions_list"
        )
    )


@transport_bp.post(
    "/exit-permissions/<int:permission_id>/cancel"
)
@login_required
@require_permission("drivers.operations")
def exit_permission_cancel(permission_id: int):
    try:
        permission = get_exit_permission_or_404(
            permission_id
        )

        cancel_exit_permission(
            permission,
            user_id=current_user.id,
            notes=request.form.get(
                "notes"
            ),
        )

        flash(
            "El permiso fue cancelado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible cancelar el permiso."
            ),
        )

    return redirect(
        url_for(
            "transport.exit_permissions_list"
        )
    )


# =========================================================
# INCIDENTES
# =========================================================
@transport_bp.get("/incidents")
@login_required
@require_permission("drivers.history.view")
def incidents_list():
    page = _safe_page()
    per_page = _safe_per_page()

    search = _clean_arg("q")
    status = _clean_arg(
        "status",
        upper=True,
    )
    incident_type = _clean_arg(
        "incident_type",
        upper=True,
    )

    stmt = (
        select(TransportIncident)
        .options(
            joinedload(
                TransportIncident.driver
            ),
            joinedload(
                TransportIncident.truck
            ),
            joinedload(
                TransportIncident.reported_by
            ),
            noload(
                TransportIncident.follow_ups
            ),
        )
        .order_by(
            TransportIncident.occurred_at.desc(),
            TransportIncident.id.desc(),
        )
    )

    if search:
        like_value = f"%{search}%"

        stmt = (
            stmt
            .join(
                Truck,
                Truck.id
                == TransportIncident.truck_id,
            )
            .outerjoin(
                Driver,
                Driver.id
                == TransportIncident.driver_id,
            )
            .where(
                or_(
                    Truck.plate.ilike(
                        like_value
                    ),
                    Driver.name.ilike(
                        like_value
                    ),
                    TransportIncident.location.ilike(
                        like_value
                    ),
                    TransportIncident.description.ilike(
                        like_value
                    ),
                )
            )
        )

    if status in INCIDENT_STATUSES:
        stmt = stmt.where(
            TransportIncident.status
            == status
        )

    if incident_type in INCIDENT_TYPES:
        stmt = stmt.where(
            TransportIncident.incident_type
            == incident_type
        )

    pagination = db.paginate(
        stmt,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "transport/incidents_list.html",
        pagination=pagination,
        incidents=pagination.items,
        incident_statuses=INCIDENT_STATUSES,
        incident_types=INCIDENT_TYPES,
        filters={
            "q": search,
            "status": status,
            "incident_type": incident_type,
            "per_page": per_page,
        },
    )


@transport_bp.route(
    "/incidents/new",
    methods=["GET", "POST"],
)
@login_required
@require_permission("drivers.operations")
def incident_create():
    selected_driver_id = request.args.get(
        "driver_id",
        type=int,
    )

    selected_truck_id = request.args.get(
        "truck_id",
        type=int,
    )

    if request.method == "POST":
        try:
            incident = create_incident(
                _form_to_dict(),
                user_id=current_user.id,
            )

            flash(
                "El incidente fue registrado.",
                "success",
            )

            return redirect(
                url_for(
                    "transport.incident_detail",
                    incident_id=incident.id,
                )
            )

        except TransportServiceError as exc:
            _rollback_and_flash(
                exc,
                default_message=(
                    "No fue posible registrar el incidente."
                ),
            )

    drivers_stmt = (
        select(Driver)
        .options(
            noload(
                Driver.documents
            ),
            noload(
                Driver.apm_record
            ),
            noload(
                Driver.assignments
            ),
            noload(
                Driver.exit_permissions
            ),
            noload(
                Driver.incidents
            ),
        )
        .where(
            Driver.status == "ACTIVE"
        )
        .order_by(
            Driver.name.asc()
        )
        .limit(100)
    )

    trucks_stmt = (
        select(Truck)
        .options(
            joinedload(
                Truck.owner
            ),
            noload(
                Truck.documents
            ),
            noload(
                Truck.assignments
            ),
            noload(
                Truck.exit_permissions
            ),
            noload(
                Truck.incidents
            ),
        )
        .where(
            Truck.status.in_(
                (
                    "ACTIVE",
                    "DAMAGED",
                    "STRANDED",
                )
            )
        )
        .order_by(
            Truck.plate.asc()
        )
        .limit(100)
    )

    return render_template(
        "transport/incident_form.html",
        drivers=list(
            db.session.scalars(
                drivers_stmt
            ).all()
        ),
        trucks=list(
            db.session.scalars(
                trucks_stmt
            ).unique().all()
        ),
        incident_types=INCIDENT_TYPES,
        selected_driver_id=selected_driver_id,
        selected_truck_id=selected_truck_id,
        now=datetime.now(),
    )


@transport_bp.get(
    "/incidents/<int:incident_id>"
)
@login_required
@require_permission("drivers.history.view")
def incident_detail(incident_id: int):
    stmt = (
        select(TransportIncident)
        .options(
            joinedload(
                TransportIncident.driver
            ),
            joinedload(
                TransportIncident.truck
            ).joinedload(Truck.owner),
            joinedload(
                TransportIncident.reported_by
            ),
            joinedload(
                TransportIncident.resolved_by
            ),
            selectinload(
                TransportIncident.follow_ups
            ).joinedload(
                TransportIncidentFollowUp.created_by
            ),
        )
        .where(
            TransportIncident.id
            == incident_id
        )
    )

    incident = db.session.scalar(stmt)

    if incident is None:
        abort(404)

    return render_template(
        "transport/incident_detail.html",
        incident=incident,
        incident_statuses=INCIDENT_STATUSES,
        truck_statuses=TRUCK_STATUSES,
    )


@transport_bp.post(
    "/incidents/<int:incident_id>/follow-up"
)
@login_required
@require_permission("drivers.operations")
def incident_follow_up(incident_id: int):
    try:
        incident = get_incident_or_404(
            incident_id
        )

        add_incident_follow_up(
            incident,
            _form_to_dict(),
            user_id=current_user.id,
        )

        flash(
            "El seguimiento fue registrado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible registrar el seguimiento."
            ),
        )

    return redirect(
        url_for(
            "transport.incident_detail",
            incident_id=incident_id,
        )
    )


@transport_bp.post(
    "/incidents/<int:incident_id>/resolve"
)
@login_required
@require_permission("drivers.operations")
def incident_resolve(incident_id: int):
    try:
        incident = get_incident_or_404(
            incident_id
        )

        resolve_incident(
            incident,
            user_id=current_user.id,
            resolution=request.form.get(
                "resolution"
            ),
            resolved_at=request.form.get(
                "resolved_at"
            ),
            truck_status=(
                request.form.get(
                    "truck_status"
                )
                or "ACTIVE"
            ),
        )

        flash(
            "El incidente fue resuelto.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible resolver el incidente."
            ),
        )

    return redirect(
        url_for(
            "transport.incident_detail",
            incident_id=incident_id,
        )
    )


@transport_bp.post(
    "/incidents/<int:incident_id>/cancel"
)
@login_required
@require_permission("drivers.operations")
def incident_cancel(incident_id: int):
    try:
        incident = get_incident_or_404(
            incident_id
        )

        cancel_incident(
            incident,
            user_id=current_user.id,
            reason=request.form.get(
                "reason"
            ),
            restore_truck_status=(
                request.form.get(
                    "restore_truck_status",
                    "1",
                )
                == "1"
            ),
        )

        flash(
            "El incidente fue cancelado.",
            "success",
        )

    except TransportServiceError as exc:
        _rollback_and_flash(
            exc,
            default_message=(
                "No fue posible cancelar el incidente."
            ),
        )

    return redirect(
        url_for(
            "transport.incident_detail",
            incident_id=incident_id,
        )
    )


# =========================================================
# HISTORIAL DOCUMENTAL
# =========================================================
@transport_bp.get("/history")
@login_required
@require_permission("drivers.history.view")
def history():
    page = _safe_page()
    per_page = _safe_per_page()

    entity_type = _clean_arg(
        "entity_type",
        upper=True,
    )

    entity_id = request.args.get(
        "entity_id",
        type=int,
    )

    document_type = _clean_arg(
        "document_type",
        upper=True,
    )

    stmt = (
        select(TransportDocumentChange)
        .options(
            joinedload(
                TransportDocumentChange.changed_by
            )
        )
        .order_by(
            TransportDocumentChange.changed_at.desc(),
            TransportDocumentChange.id.desc(),
        )
    )

    if entity_type in {
        "DRIVER",
        "TRUCK",
        "APM",
    }:
        stmt = stmt.where(
            TransportDocumentChange.entity_type
            == entity_type
        )

    if entity_id:
        stmt = stmt.where(
            TransportDocumentChange.entity_id
            == entity_id
        )

    if document_type:
        stmt = stmt.where(
            TransportDocumentChange.document_type
            == document_type
        )

    pagination = db.paginate(
        stmt,
        page=page,
        per_page=per_page,
        error_out=False,
    )

    return render_template(
        "transport/history.html",
        pagination=pagination,
        changes=pagination.items,
        filters={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "document_type": document_type,
            "per_page": per_page,
        },
    )