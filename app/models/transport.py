# app/models/transport.py
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.extensions import db

SCHEMA = "yard_gate_alamo"


# =========================================================
# PROPIETARIOS DE CABEZALES
# =========================================================
class TruckOwner(db.Model):
    __tablename__ = "truck_owners"
    __table_args__ = (
        UniqueConstraint(
            "name",
            "phone",
            name="uq_truck_owners_name_phone",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(160), nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    email = db.Column(db.String(160), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    is_active = db.Column(
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("true"),
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    trucks = db.relationship(
        "Truck",
        back_populates="owner",
        lazy="selectin",
    )


# =========================================================
# CHOFERES
# =========================================================
class Driver(db.Model):
    __tablename__ = "drivers"
    __table_args__ = (
        UniqueConstraint(
            "identification",
            name="uq_drivers_identification",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')",
            name="ck_drivers_status",
        ),
        Index(
            "ix_drivers_name",
            "name",
        ),
        Index(
            "ix_drivers_habitual_site_id",
            "habitual_site_id",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    name = db.Column(db.String(180), nullable=False)
    residence = db.Column(db.String(240), nullable=True)

    identification = db.Column(db.String(40), nullable=False)

    phone_1 = db.Column(db.String(40), nullable=True)
    phone_2 = db.Column(db.String(40), nullable=True)

    habitual_site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )

    notes = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    habitual_site = db.relationship(
        "Site",
        foreign_keys=[habitual_site_id],
        lazy="joined",
    )

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="joined",
    )

    updated_by = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
        lazy="joined",
    )

    documents = db.relationship(
        "DriverDocument",
        back_populates="driver",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    apm_record = db.relationship(
        "DriverApmRecord",
        back_populates="driver",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    assignments = db.relationship(
        "DriverTruckAssignment",
        back_populates="driver",
        lazy="selectin",
    )

    exit_permissions = db.relationship(
        "DriverExitPermission",
        back_populates="driver",
        lazy="selectin",
    )

    incidents = db.relationship(
        "TransportIncident",
        back_populates="driver",
        lazy="selectin",
    )


# =========================================================
# DOCUMENTOS DE CHOFER
# =========================================================
class DriverDocument(db.Model):
    __tablename__ = "driver_documents"
    __table_args__ = (
        UniqueConstraint(
            "driver_id",
            "document_type",
            name="uq_driver_documents_driver_type",
        ),
        CheckConstraint(
            """
            document_type IN (
                'DOCK_PERMIT',
                'GENERAL_CARD',
                'CHEMICAL_PERMIT',
                'LICENSE',
                'CRIMINAL_RECORD'
            )
            """,
            name="ck_driver_documents_type",
        ),
        CheckConstraint(
            """
            status IN (
                'PENDING',
                'VALID',
                'EXPIRED',
                'NOT_APPLICABLE'
            )
            """,
            name="ck_driver_documents_status",
        ),
        Index(
            "ix_driver_documents_expiry_date",
            "expiry_date",
        ),
        Index(
            "ix_driver_documents_driver_id",
            "driver_id",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.drivers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    document_type = db.Column(db.String(40), nullable=False)

    document_number = db.Column(db.String(100), nullable=True)

    status = db.Column(
        db.String(30),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    issue_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)

    no_expiry = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.text("false"),
    )

    notes = db.Column(db.Text, nullable=True)

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    driver = db.relationship(
        "Driver",
        back_populates="documents",
    )

    updated_by = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
        lazy="joined",
    )


# =========================================================
# INFORMACIÓN APM DEL CHOFER
# =========================================================
class DriverApmRecord(db.Model):
    __tablename__ = "driver_apm_records"
    __table_args__ = (
        UniqueConstraint(
            "driver_id",
            name="uq_driver_apm_records_driver_id",
        ),
        CheckConstraint(
            "training_status IN ('PENDING', 'YES')",
            name="ck_driver_apm_training_status",
        ),
        CheckConstraint(
            "card_status IN ('PENDING', 'EXPIRED', 'YES')",
            name="ck_driver_apm_card_status",
        ),
        CheckConstraint(
            """
            expiry_mode IN (
                'PENDING',
                'EXPIRED',
                'NO_EXPIRY',
                'DATE'
            )
            """,
            name="ck_driver_apm_expiry_mode",
        ),
        Index(
            "ix_driver_apm_expiry_date",
            "expiry_date",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.drivers.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    training_status = db.Column(
        db.String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    card_status = db.Column(
        db.String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    card_number = db.Column(db.String(100), nullable=True)

    expiry_mode = db.Column(
        db.String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    expiry_date = db.Column(db.Date, nullable=True)

    notes = db.Column(db.Text, nullable=True)

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    driver = db.relationship(
        "Driver",
        back_populates="apm_record",
    )

    updated_by = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
        lazy="joined",
    )


# =========================================================
# CABEZALES
# =========================================================
class Truck(db.Model):
    __tablename__ = "trucks"
    __table_args__ = (
        UniqueConstraint(
            "plate",
            name="uq_trucks_plate",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE', 'DAMAGED', 'STRANDED')",
            name="ck_trucks_status",
        ),
        CheckConstraint(
            "bonded_status IN ('BONDED', 'PENDING')",
            name="ck_trucks_bonded_status",
        ),
        Index(
            "ix_trucks_registration_date",
            "registration_date",
        ),
        Index(
            "ix_trucks_registered_site_id",
            "registered_site_id",
        ),
        Index(
            "ix_trucks_owner_id",
            "owner_id",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    registration_date = db.Column(db.Date, nullable=False)

    registered_site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    plate = db.Column(db.String(40), nullable=False)

    owner_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.truck_owners.id"),
        nullable=True,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )

    dock_permit_number = db.Column(db.String(100), nullable=True)
    dock_permit_expiry_date = db.Column(db.Date, nullable=True)

    circulation_card = db.Column(db.String(180), nullable=True)

    dekra_month = db.Column(db.SmallInteger, nullable=True)
    dekra_year = db.Column(db.SmallInteger, nullable=True)

    insurance_name = db.Column(db.String(160), nullable=True)
    insurance_expiry_date = db.Column(db.Date, nullable=True)

    is_payroll = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.text("false"),
    )

    rt_name = db.Column(db.String(180), nullable=True)
    rt_expiry_date = db.Column(db.Date, nullable=True)

    weights_dimensions = db.Column(db.Numeric(12, 2), nullable=True)

    policy_number = db.Column(db.String(120), nullable=True)

    bonded_status = db.Column(
        db.String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    notes = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    registered_site = db.relationship(
        "Site",
        foreign_keys=[registered_site_id],
        lazy="joined",
    )

    owner = db.relationship(
        "TruckOwner",
        back_populates="trucks",
        lazy="joined",
    )

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="joined",
    )

    updated_by = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
        lazy="joined",
    )

    documents = db.relationship(
        "TruckDocument",
        back_populates="truck",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    assignments = db.relationship(
        "DriverTruckAssignment",
        back_populates="truck",
        lazy="selectin",
    )

    exit_permissions = db.relationship(
        "DriverExitPermission",
        back_populates="truck",
        lazy="selectin",
    )

    incidents = db.relationship(
        "TransportIncident",
        back_populates="truck",
        lazy="selectin",
    )


# =========================================================
# DOCUMENTOS ADICIONALES DEL CABEZAL
# =========================================================
class TruckDocument(db.Model):
    __tablename__ = "truck_documents"
    __table_args__ = (
        UniqueConstraint(
            "truck_id",
            "document_type",
            name="uq_truck_documents_truck_type",
        ),
        CheckConstraint(
            """
            status IN (
                'PENDING',
                'VALID',
                'EXPIRED',
                'NOT_APPLICABLE'
            )
            """,
            name="ck_truck_documents_status",
        ),
        Index(
            "ix_truck_documents_expiry_date",
            "expiry_date",
        ),
        Index(
            "ix_truck_documents_truck_id",
            "truck_id",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    truck_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.trucks.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    document_type = db.Column(db.String(50), nullable=False)

    document_number = db.Column(db.String(120), nullable=True)

    status = db.Column(
        db.String(30),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )

    issue_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)

    no_expiry = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.text("false"),
    )

    notes = db.Column(db.Text, nullable=True)

    extra_data = db.Column(
        JSONB,
        nullable=True,
    )

    updated_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    truck = db.relationship(
        "Truck",
        back_populates="documents",
    )

    updated_by = db.relationship(
        "User",
        foreign_keys=[updated_by_user_id],
        lazy="joined",
    )


# =========================================================
# ASIGNACIÓN CHOFER ↔ CABEZAL
# =========================================================
class DriverTruckAssignment(db.Model):
    __tablename__ = "driver_truck_assignments"
    __table_args__ = (
        CheckConstraint(
            """
            status IN (
                'ACTIVE',
                'ENDED'
            )
            """,
            name="ck_driver_truck_assignments_status",
        ),
        Index(
            "ix_driver_truck_assignments_driver_id",
            "driver_id",
        ),
        Index(
            "ix_driver_truck_assignments_truck_id",
            "truck_id",
        ),
        Index(
            "ix_driver_truck_assignments_status",
            "status",
        ),
        Index(
            "ix_driver_truck_assignments_started_at",
            "started_at",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.drivers.id"),
        nullable=False,
    )

    truck_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.trucks.id"),
        nullable=False,
    )

    status = db.Column(
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )

    started_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    ended_at = db.Column(db.DateTime, nullable=True)

    end_reason = db.Column(db.String(240), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    ended_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    driver = db.relationship(
        "Driver",
        back_populates="assignments",
    )

    truck = db.relationship(
        "Truck",
        back_populates="assignments",
    )

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="joined",
    )

    ended_by = db.relationship(
        "User",
        foreign_keys=[ended_by_user_id],
        lazy="joined",
    )


# =========================================================
# PERMISOS DE SALIDA
# =========================================================
class DriverExitPermission(db.Model):
    __tablename__ = "driver_exit_permissions"
    __table_args__ = (
        CheckConstraint(
            """
            status IN (
                'AUTHORIZED',
                'RETURNED',
                'CANCELLED'
            )
            """,
            name="ck_driver_exit_permissions_status",
        ),
        Index(
            "ix_driver_exit_permissions_driver_id",
            "driver_id",
        ),
        Index(
            "ix_driver_exit_permissions_truck_id",
            "truck_id",
        ),
        Index(
            "ix_driver_exit_permissions_status",
            "status",
        ),
        Index(
            "ix_driver_exit_permissions_departure_at",
            "departure_at",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.drivers.id"),
        nullable=False,
    )

    truck_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.trucks.id"),
        nullable=True,
    )

    departure_at = db.Column(db.DateTime, nullable=False)

    reason = db.Column(db.String(240), nullable=False)
    destination = db.Column(db.String(240), nullable=True)

    expected_return_at = db.Column(db.DateTime, nullable=True)
    actual_return_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(
        db.String(20),
        nullable=False,
        default="AUTHORIZED",
        server_default="AUTHORIZED",
    )

    notes = db.Column(db.Text, nullable=True)

    authorized_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    returned_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    driver = db.relationship(
        "Driver",
        back_populates="exit_permissions",
    )

    truck = db.relationship(
        "Truck",
        back_populates="exit_permissions",
    )

    authorized_by = db.relationship(
        "User",
        foreign_keys=[authorized_by_user_id],
        lazy="joined",
    )

    returned_by = db.relationship(
        "User",
        foreign_keys=[returned_by_user_id],
        lazy="joined",
    )


# =========================================================
# INCIDENTES: DAÑADO / VARADO
# =========================================================
class TransportIncident(db.Model):
    __tablename__ = "transport_incidents"
    __table_args__ = (
        CheckConstraint(
            "incident_type IN ('DAMAGED', 'STRANDED')",
            name="ck_transport_incidents_type",
        ),
        CheckConstraint(
            """
            status IN (
                'OPEN',
                'FOLLOW_UP',
                'RESOLVED',
                'CANCELLED'
            )
            """,
            name="ck_transport_incidents_status",
        ),
        Index(
            "ix_transport_incidents_driver_id",
            "driver_id",
        ),
        Index(
            "ix_transport_incidents_truck_id",
            "truck_id",
        ),
        Index(
            "ix_transport_incidents_status",
            "status",
        ),
        Index(
            "ix_transport_incidents_next_follow_up_at",
            "next_follow_up_at",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.drivers.id"),
        nullable=True,
    )

    truck_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.trucks.id"),
        nullable=False,
    )

    incident_type = db.Column(db.String(20), nullable=False)

    occurred_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    location = db.Column(db.String(240), nullable=True)
    description = db.Column(db.Text, nullable=False)

    status = db.Column(
        db.String(20),
        nullable=False,
        default="OPEN",
        server_default="OPEN",
    )

    last_follow_up_at = db.Column(db.DateTime, nullable=True)
    next_follow_up_at = db.Column(db.DateTime, nullable=True)

    resolution = db.Column(db.Text, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)

    reported_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    resolved_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=db.text("now()"),
    )

    driver = db.relationship(
        "Driver",
        back_populates="incidents",
    )

    truck = db.relationship(
        "Truck",
        back_populates="incidents",
    )

    reported_by = db.relationship(
        "User",
        foreign_keys=[reported_by_user_id],
        lazy="joined",
    )

    resolved_by = db.relationship(
        "User",
        foreign_keys=[resolved_by_user_id],
        lazy="joined",
    )

    follow_ups = db.relationship(
        "TransportIncidentFollowUp",
        back_populates="incident",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="TransportIncidentFollowUp.contacted_at.desc()",
    )


# =========================================================
# SEGUIMIENTOS DE INCIDENTES
# =========================================================
class TransportIncidentFollowUp(db.Model):
    __tablename__ = "transport_incident_follow_ups"
    __table_args__ = (
        Index(
            "ix_transport_incident_follow_ups_incident_id",
            "incident_id",
        ),
        Index(
            "ix_transport_incident_follow_ups_contacted_at",
            "contacted_at",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    incident_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.transport_incidents.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    contacted_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    contact_name = db.Column(db.String(180), nullable=True)
    current_situation = db.Column(db.Text, nullable=False)
    repair_estimate = db.Column(db.String(240), nullable=True)

    notes = db.Column(db.Text, nullable=True)

    next_follow_up_at = db.Column(db.DateTime, nullable=True)

    resolved = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.text("false"),
    )

    created_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    incident = db.relationship(
        "TransportIncident",
        back_populates="follow_ups",
    )

    created_by = db.relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="joined",
    )


# =========================================================
# HISTORIAL DE CAMBIOS DOCUMENTALES
# =========================================================
class TransportDocumentChange(db.Model):
    __tablename__ = "transport_document_changes"
    __table_args__ = (
        CheckConstraint(
            "entity_type IN ('DRIVER', 'TRUCK', 'APM')",
            name="ck_transport_document_changes_entity_type",
        ),
        Index(
            "ix_transport_document_changes_entity",
            "entity_type",
            "entity_id",
        ),
        Index(
            "ix_transport_document_changes_changed_at",
            "changed_at",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    entity_type = db.Column(db.String(20), nullable=False)
    entity_id = db.Column(db.Integer, nullable=False)

    document_type = db.Column(db.String(50), nullable=False)

    field_name = db.Column(db.String(80), nullable=False)

    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)

    changed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    changed_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    notes = db.Column(db.Text, nullable=True)

    changed_by = db.relationship(
        "User",
        foreign_keys=[changed_by_user_id],
        lazy="joined",
    )


# =========================================================
# ADJUNTOS
# =========================================================
class TransportAttachment(db.Model):
    __tablename__ = "transport_attachments"
    __table_args__ = (
        CheckConstraint(
            """
            (
                CASE WHEN driver_id IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN truck_id IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN assignment_id IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN exit_permission_id IS NOT NULL THEN 1 ELSE 0 END +
                CASE WHEN incident_id IS NOT NULL THEN 1 ELSE 0 END
            ) = 1
            """,
            name="ck_transport_attachments_one_target",
        ),
        Index(
            "ix_transport_attachments_driver_id",
            "driver_id",
        ),
        Index(
            "ix_transport_attachments_truck_id",
            "truck_id",
        ),
        Index(
            "ix_transport_attachments_assignment_id",
            "assignment_id",
        ),
        Index(
            "ix_transport_attachments_exit_permission_id",
            "exit_permission_id",
        ),
        Index(
            "ix_transport_attachments_incident_id",
            "incident_id",
        ),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    driver_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.drivers.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    truck_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.trucks.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    assignment_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.driver_truck_assignments.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    exit_permission_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.driver_exit_permissions.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    incident_id = db.Column(
        db.Integer,
        db.ForeignKey(
            f"{SCHEMA}.transport_incidents.id",
            ondelete="CASCADE",
        ),
        nullable=True,
    )

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False)

    storage_path = db.Column(db.Text, nullable=False)

    mime_type = db.Column(db.String(120), nullable=True)
    size_bytes = db.Column(db.BigInteger, nullable=True)

    description = db.Column(db.String(240), nullable=True)

    uploaded_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    uploaded_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        server_default=db.text("now()"),
    )

    uploaded_by = db.relationship(
        "User",
        foreign_keys=[uploaded_by_user_id],
        lazy="joined",
    )