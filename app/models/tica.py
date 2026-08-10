# app/models/tica.py

from datetime import datetime

from app.extensions import db


SCHEMA = "yard_gate_alamo"


class TicaTransporter(db.Model):
    """
    Catálogo de transportistas utilizado exclusivamente por el módulo TICA.

    El nombre sirve para búsqueda y selección dentro del sistema.
    En el archivo TRM solamente se escribe identification_number.
    """

    __tablename__ = "tica_transporters"
    __table_args__ = (
        db.UniqueConstraint(
            "identification_number",
            name="uq_tica_transporters_identification",
        ),
        db.Index(
            "ix_tica_transporters_name",
            "name",
        ),
        db.Index(
            "ix_tica_transporters_active_name",
            "is_active",
            "name",
        ),
        {
            "schema": SCHEMA,
        },
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(180),
        nullable=False,
    )

    identification_number = db.Column(
        db.String(50),
        nullable=False,
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    drivers = db.relationship(
        "TicaDriver",
        back_populates="transporter",
        lazy="selectin",
    )

    generated_files = db.relationship(
        "TicaGeneratedFile",
        back_populates="transporter",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<TicaTransporter "
            f"id={self.id} "
            f"name={self.name!r} "
            f"identification={self.identification_number!r}>"
        )


class TicaDriver(db.Model):
    """
    Chofer asociado a un transportista.

    Al seleccionarlo en el generador se cargan:
    - nombre
    - identificación
    - placa

    Los valores permanecen editables en el formulario sin modificar
    automáticamente el catálogo maestro.
    """

    __tablename__ = "tica_drivers"
    __table_args__ = (
        db.UniqueConstraint(
            "identification_number",
            name="uq_tica_drivers_identification",
        ),
        db.Index(
            "ix_tica_drivers_transporter",
            "transporter_id",
        ),
        db.Index(
            "ix_tica_drivers_transporter_active",
            "transporter_id",
            "is_active",
        ),
        db.Index(
            "ix_tica_drivers_name",
            "name",
        ),
        db.Index(
            "ix_tica_drivers_plate",
            "plate",
        ),
        {
            "schema": SCHEMA,
        },
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    transporter_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.tica_transporters.id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    name = db.Column(
        db.String(180),
        nullable=False,
    )

    identification_number = db.Column(
        db.String(50),
        nullable=False,
    )

    plate = db.Column(
        db.String(30),
        nullable=False,
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    transporter = db.relationship(
        "TicaTransporter",
        back_populates="drivers",
        lazy="joined",
    )

    generated_files = db.relationship(
        "TicaGeneratedFile",
        back_populates="driver",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<TicaDriver "
            f"id={self.id} "
            f"name={self.name!r} "
            f"identification={self.identification_number!r}>"
        )


class TicaDestination(db.Model):
    """
    Catálogo de ubicaciones o destinos.

    El usuario busca por name o code.
    En el archivo TRM solamente se escribe code dentro de TRTRTADST.
    """

    __tablename__ = "tica_destinations"
    __table_args__ = (
        db.UniqueConstraint(
            "code",
            name="uq_tica_destinations_code",
        ),
        db.Index(
            "ix_tica_destinations_name",
            "name",
        ),
        db.Index(
            "ix_tica_destinations_active_name",
            "is_active",
            "name",
        ),
        {
            "schema": SCHEMA,
        },
    )

    id = db.Column(
        db.Integer,
        primary_key=True,
    )

    name = db.Column(
        db.String(180),
        nullable=False,
    )

    code = db.Column(
        db.String(50),
        nullable=False,
    )

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    generated_files = db.relationship(
        "TicaGeneratedFile",
        back_populates="destination",
        lazy="select",
    )

    def __repr__(self) -> str:
        return (
            f"<TicaDestination "
            f"id={self.id} "
            f"name={self.name!r} "
            f"code={self.code!r}>"
        )


class TicaGeneratedFile(db.Model):
    """
    Historial de archivos TRM generados.

    Guarda referencias a los catálogos y también una fotografía de los
    valores utilizados. Esto conserva el contenido histórico aunque los
    catálogos cambien posteriormente.
    """

    __tablename__ = "tica_generated_files"
    __table_args__ = (
        db.CheckConstraint(
            "movement_type IN ('E', 'S')",
            name="ck_tica_generated_files_movement_type",
        ),
        db.CheckConstraint(
            "dua_ordinal >= 1",
            name="ck_tica_generated_files_dua_ordinal",
        ),
        db.CheckConstraint(
            "packages >= 0",
            name="ck_tica_generated_files_packages",
        ),
        db.CheckConstraint(
            "weight >= 0",
            name="ck_tica_generated_files_weight",
        ),
        db.CheckConstraint(
            "status IN ('CREATED', 'WRITTEN', 'ERROR')",
            name="ck_tica_generated_files_status",
        ),
        db.Index(
            "ix_tica_generated_files_created_at",
            "created_at",
        ),
        db.Index(
            "ix_tica_generated_files_trip",
            "trip_number",
        ),
        db.Index(
            "ix_tica_generated_files_container",
            "container_number",
        ),
        db.Index(
            "ix_tica_generated_files_transporter",
            "transporter_id",
        ),
        db.Index(
            "ix_tica_generated_files_driver",
            "driver_id",
        ),
        db.Index(
            "ix_tica_generated_files_destination",
            "destination_id",
        ),
        db.Index(
            "ix_tica_generated_files_status_created",
            "status",
            "created_at",
        ),
        {
            "schema": SCHEMA,
        },
    )

    id = db.Column(
        db.BigInteger,
        primary_key=True,
    )

    transporter_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.tica_transporters.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.tica_drivers.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    destination_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.tica_destinations.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    movement_type = db.Column(
        db.String(1),
        nullable=False,
    )

    trip_number = db.Column(
        db.String(60),
        nullable=False,
    )

    dua_ordinal = db.Column(
        db.Integer,
        nullable=False,
        default=1,
    )

    # =====================================================
    # Copia exacta de los datos utilizados
    # =====================================================

    transporter_name_snapshot = db.Column(
        db.String(180),
        nullable=True,
    )

    transporter_identification = db.Column(
        db.String(50),
        nullable=False,
    )

    driver_name = db.Column(
        db.String(180),
        nullable=False,
    )

    driver_identification = db.Column(
        db.String(50),
        nullable=False,
    )

    plate = db.Column(
        db.String(30),
        nullable=False,
    )

    destination_name_snapshot = db.Column(
        db.String(180),
        nullable=True,
    )

    destination_code = db.Column(
        db.String(50),
        nullable=False,
    )

    weight = db.Column(
        db.Numeric(14, 3),
        nullable=False,
    )

    packages = db.Column(
        db.Integer,
        nullable=False,
    )

    movement_date = db.Column(
        db.Date,
        nullable=False,
    )

    container_number = db.Column(
        db.String(30),
        nullable=False,
    )

    seal_number = db.Column(
        db.String(120),
        nullable=False,
    )

    # =====================================================
    # Resultado generado
    # =====================================================

    file_name = db.Column(
        db.String(255),
        nullable=False,
    )

    output_path = db.Column(
        db.Text,
        nullable=True,
    )

    content_text = db.Column(
        db.Text,
        nullable=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="CREATED",
    )

    error_message = db.Column(
        db.Text,
        nullable=True,
    )

    written_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    transporter = db.relationship(
        "TicaTransporter",
        back_populates="generated_files",
        lazy="joined",
    )

    driver = db.relationship(
        "TicaDriver",
        back_populates="generated_files",
        lazy="joined",
    )

    destination = db.relationship(
        "TicaDestination",
        back_populates="generated_files",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return (
            f"<TicaGeneratedFile "
            f"id={self.id} "
            f"file_name={self.file_name!r} "
            f"status={self.status!r}>"
        )


class TicaImportBatch(db.Model):
    """
    Historial de cargas masivas.

    Permite registrar importaciones de:
    - transportistas
    - choferes
    - ubicaciones
    """

    __tablename__ = "tica_import_batches"
    __table_args__ = (
        db.CheckConstraint(
            """
            import_type IN (
                'TRANSPORTERS',
                'DRIVERS',
                'DESTINATIONS'
            )
            """,
            name="ck_tica_import_batches_type",
        ),
        db.CheckConstraint(
            """
            status IN (
                'PROCESSING',
                'COMPLETED',
                'PARTIAL',
                'ERROR'
            )
            """,
            name="ck_tica_import_batches_status",
        ),
        db.Index(
            "ix_tica_import_batches_created_at",
            "created_at",
        ),
        db.Index(
            "ix_tica_import_batches_type_created",
            "import_type",
            "created_at",
        ),
        {
            "schema": SCHEMA,
        },
    )

    id = db.Column(
        db.BigInteger,
        primary_key=True,
    )

    import_type = db.Column(
        db.String(30),
        nullable=False,
    )

    original_file_name = db.Column(
        db.String(255),
        nullable=False,
    )

    total_rows = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    created_rows = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    updated_rows = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    skipped_rows = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    error_rows = db.Column(
        db.Integer,
        nullable=False,
        default=0,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="PROCESSING",
    )

    result_json = db.Column(
        db.JSON,
        nullable=True,
    )

    error_message = db.Column(
        db.Text,
        nullable=True,
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.users.id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<TicaImportBatch "
            f"id={self.id} "
            f"type={self.import_type!r} "
            f"status={self.status!r}>"
        )