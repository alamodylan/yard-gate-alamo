# app/models/eir.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class EIR(db.Model):
    __tablename__ = "eirs"
    __table_args__ = (
        db.Index("idx_eirs_site_date", "site_id", db.text("trip_date DESC")),
        db.Index("idx_eirs_gate_out_movement", "gate_out_movement_id"),
        db.Index("ix_eirs_site_id", "site_id"),
        db.Index("ix_eirs_status", "status"),
        db.Index("ix_eirs_container_id", "container_id"),
        db.Index("ix_eirs_chassis_id", "chassis_id"),
        db.Index("ix_eirs_finalized_at", "finalized_at"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    # =========================
    # Relaciones base
    # =========================
    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False
    )

    last_edited_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True
    )

    reverted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True
    )

    gate_out_movement_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.movements.id"),
        nullable=True
    )

    chassis_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.chassis.id"),
        nullable=True
    )

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id"),
        nullable=True
    )

    # =========================
    # Control de tiempos / flujo
    # =========================
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    finalized_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    editable_until = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    last_edited_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    reverted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    pdf_generated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    inventory_out_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    inventory_restored_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True
    )

    # =========================
    # Encabezado del EIR
    # =========================
    terminal_name = db.Column(
        db.String(40),
        nullable=False
    )

    trip_date = db.Column(
        db.Date,
        nullable=False
    )

    trip_time = db.Column(
        db.Time,
        nullable=True
    )

    carrier = db.Column(
        db.String(40),
        nullable=False,
        default="ATM"
    )

    origin = db.Column(
        db.String(40),
        nullable=False
    )

    destination = db.Column(
        db.String(120),
        nullable=True
    )

    operation_type = db.Column(
        db.String(20),
        nullable=True
    )  # EXPORTACION | IMPORTACION

    driver_name = db.Column(
        db.String(120),
        nullable=True
    )

    driver_id_doc = db.Column(
        db.String(60),
        nullable=True
    )

    truck_plate = db.Column(
        db.String(30),
        nullable=True
    )

    # =========================
    # Flags de equipo
    # =========================
    has_chassis = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    has_container = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    is_reefer = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    has_genset = db.Column(
        db.Boolean,
        nullable=False,
        default=False
    )

    # =========================
    # Datos visibles del equipo
    # =========================
    shipping_line = db.Column(
        db.String(40),
        nullable=True
    )

    container_size = db.Column(
        db.String(20),
        nullable=True
    )

    container_seal = db.Column(
        db.String(60),
        nullable=True
    )

    chassis_plate = db.Column(
        db.String(30),
        nullable=True
    )

    # =========================
    # Estado del flujo
    # =========================
    status = db.Column(
        db.String(20),
        nullable=False,
        default="PENDING"
    )  # PENDING | FINAL | EDITING | REVERTED

    # =========================
    # Observaciones / auditoría
    # =========================
    general_notes = db.Column(
        db.Text,
        nullable=True
    )

    edit_reason = db.Column(
        db.Text,
        nullable=True
    )

    revert_reason = db.Column(
        db.Text,
        nullable=True
    )

    # =========================
    # Snapshots del momento de salida
    # Guardan “foto lógica” del EIR al guardar.
    # =========================
    chassis_snapshot_json = db.Column(
        db.JSON,
        nullable=True
    )

    container_snapshot_json = db.Column(
        db.JSON,
        nullable=True
    )

    reefer_snapshot_json = db.Column(
        db.JSON,
        nullable=True
    )

    # =========================
    # Relaciones
    # =========================
    site = db.relationship("Site", lazy=True)

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy=True
    )

    last_edited_by = db.relationship(
        "User",
        foreign_keys=[last_edited_by_user_id],
        lazy=True
    )

    reverted_by = db.relationship(
        "User",
        foreign_keys=[reverted_by_user_id],
        lazy=True
    )

    chassis = db.relationship("Chassis", lazy=True)
    container = db.relationship("Container", lazy=True)
    gate_out_movement = db.relationship("Movement", lazy=True)

    damages = db.relationship(
        "EIRContainerDamage",
        back_populates="eir",
        cascade="all, delete-orphan",
        lazy=True,
    )

    # =========================
    # Helpers útiles
    # =========================
    @property
    def is_editable_window_open(self) -> bool:
        if not self.editable_until:
            return False
        return datetime.utcnow() <= self.editable_until

    @property
    def can_be_reverted(self) -> bool:
        return self.status in {"CONFIRMED", "EDITING"} and self.is_editable_window_open

    @property
    def can_be_edited(self) -> bool:
        return self.status == "CONFIRMED" and self.is_editable_window_open


class EIRContainerDamage(db.Model):
    __tablename__ = "eir_container_damages"
    __table_args__ = (
        db.Index("idx_eir_damages_eir", "eir_id"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    eir_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.eirs.id", ondelete="CASCADE"),
        nullable=False
    )

    side = db.Column(
        db.String(20),
        nullable=False
    )  # LEFT | RIGHT | FRONT | REAR | ROOF | INTERIOR

    damage_type = db.Column(
        db.String(10),
        nullable=False
    )  # A | R | G | M | C | F | H | Q

    x = db.Column(
        db.Numeric(6, 3),
        nullable=False
    )  # 0..1

    y = db.Column(
        db.Numeric(6, 3),
        nullable=False
    )  # 0..1

    notes = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False
    )

    eir = db.relationship(
        "EIR",
        back_populates="damages",
        lazy=True
    )

    created_by = db.relationship("User", lazy=True)