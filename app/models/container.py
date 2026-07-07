# app/models/container.py
from datetime import datetime
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Container(db.Model):
    __tablename__ = "containers"
    __table_args__ = (
        db.UniqueConstraint("site_id", "code", name="uq_containers_site_code"),
        db.Index("ix_containers_site_code", "site_id", "code"),
        db.Index("ix_containers_site_in_yard", "site_id", "is_in_yard"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id"),
        nullable=False,
        index=True,
    )

    code = db.Column(db.String(13), nullable=False)
    size = db.Column(db.String(10), nullable=False)
    year = db.Column(db.Integer, nullable=True)
    status_notes = db.Column(db.Text, nullable=True)
    evacuation_destination = db.Column(db.String(100), nullable=True)
    evacuation_type = db.Column(db.String(50), nullable=True)
    evacuation_notes = db.Column(db.Text, nullable=True)
    gate_in_origin_port = db.Column(db.String(20), nullable=True)

    is_in_yard = db.Column(db.Boolean, nullable=False, default=True)

    dispatch_status = db.Column(
        db.String(30),
        nullable=False,
        default="NORMAL",
    )

    dispatch_marked_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    dispatch_marked_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    mounted_at = db.Column(
        db.DateTime(timezone=True),
        nullable=True,
    )

    mounted_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    position = db.relationship(
        "ContainerPosition",
        back_populates="container",
        uselist=False,
        cascade="all, delete-orphan",
        lazy=True,
    )

    movements = db.relationship(
        "Movement",
        back_populates="container",
        lazy=True,
    )

    site = db.relationship(
        "Site",
        backref=db.backref("containers", lazy=True),
        lazy=True,
    )

    is_fils = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )


class ContainerPosition(db.Model):
    __tablename__ = "container_positions"
    __table_args__ = (
        db.Index("ix_container_positions_bay", "bay_id"),
        {"schema": SCHEMA},
    )

    container_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.containers.id", ondelete="CASCADE"),
        primary_key=True,
    )

    bay_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.yard_bays.id"),
        nullable=False,
    )

    depth_row = db.Column(db.Integer, nullable=False)
    tier = db.Column(db.Integer, nullable=False)

    placed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    placed_by_user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id"),
        nullable=True,
    )

    container = db.relationship("Container", back_populates="position")
    bay = db.relationship("YardBay", lazy=True)