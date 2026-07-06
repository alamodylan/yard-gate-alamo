# app/models/dispatch.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class DispatchContainerSize(db.Model):
    __tablename__ = "dispatch_container_sizes"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), nullable=False, unique=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class ShippingLine(db.Model):
    __tablename__ = "shipping_lines"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(100), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)


class DispatchRequest(db.Model):
    __tablename__ = "dispatch_requests"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    request_type = db.Column(db.String(20), nullable=False, default="DESPACHO")
    booking = db.Column(db.String(50), nullable=True)
    shipping_line = db.Column(db.String(80), nullable=False)
    requires_gps = db.Column(db.Boolean, nullable=False, default=False)

    client_name = db.Column(db.String(200), nullable=True)
    product_name = db.Column(db.String(200), nullable=True)

    chassis_type = db.Column(db.String(20), nullable=True)
    port_out = db.Column(db.String(20), nullable=True)

    special_instructions = db.Column(db.Text, nullable=True)

    status = db.Column(db.String(30), nullable=False, default="PENDIENTE")

    requested_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    requested_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    lines = db.relationship(
        "DispatchRequestLine",
        back_populates="request",
        cascade="all, delete-orphan",
        lazy=True,
    )


class DispatchRequestLine(db.Model):
    __tablename__ = "dispatch_request_lines"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    request_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.dispatch_requests.id", ondelete="CASCADE"),
        nullable=False,
    )

    container_size = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)

    load_date = db.Column(db.Date, nullable=False)
    load_time = db.Column(db.Time, nullable=True)

    condition_type = db.Column(db.String(20), nullable=False, default="CARGADO")

    status = db.Column(db.String(30), nullable=False, default="PENDIENTE")

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    request = db.relationship(
        "DispatchRequest",
        back_populates="lines",
        lazy=True,
    )

    assignments = db.relationship(
        "DispatchAssignment",
        back_populates="line",
        cascade="all, delete-orphan",
        lazy=True,
    )


class DispatchAssignment(db.Model):
    __tablename__ = "dispatch_assignments"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    request_line_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.dispatch_request_lines.id", ondelete="CASCADE"),
        nullable=False,
    )

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id"),
        nullable=False,
    )

    assigned_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    assigned_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    status = db.Column(db.String(30), nullable=False, default="ASIGNADO")
    assignment_notes = db.Column(db.Text, nullable=True)

    eir_id = db.Column(db.Integer, nullable=True)
    chassis_id = db.Column(db.Integer, nullable=True)

    mounted_at = db.Column(db.DateTime(timezone=True), nullable=True)

    mounted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    line = db.relationship(
        "DispatchRequestLine",
        back_populates="assignments",
        lazy=True,
    )

    container = db.relationship(
        "Container",
        lazy=True,
    )


class UserNotification(db.Model):
    __tablename__ = "user_notifications"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)

    related_type = db.Column(db.String(50), nullable=True)
    related_id = db.Column(db.Integer, nullable=True)

    is_read = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    read_at = db.Column(db.DateTime(timezone=True), nullable=True)

class GpsDevice(db.Model):
    __tablename__ = "gps_devices"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    gps_number = db.Column(db.String(50), nullable=False, unique=True)
    status = db.Column(db.String(30), nullable=False, default="DISPONIBLE")
    battery_range = db.Column(db.String(20), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class GpsAssignment(db.Model):
    __tablename__ = "gps_assignments"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
    )

    dispatch_request_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.dispatch_requests.id"),
        nullable=False,
    )

    dispatch_request_line_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.dispatch_request_lines.id"),
        nullable=True,
    )

    dispatch_assignment_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.dispatch_assignments.id"),
        nullable=True,
    )

    gps_device_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.gps_devices.id"),
        nullable=False,
    )

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id"),
        nullable=True,
    )

    chassis_id = db.Column(db.Integer, nullable=True)

    status = db.Column(db.String(30), nullable=False, default="ASIGNADO")

    assigned_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=False,
    )

    assigned_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    released_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    gps_device = db.relationship("GpsDevice", lazy=True)
    request = db.relationship("DispatchRequest", lazy=True)
    line = db.relationship("DispatchRequestLine", lazy=True)
    assignment = db.relationship("DispatchAssignment", lazy=True)
    container = db.relationship("Container", lazy=True)