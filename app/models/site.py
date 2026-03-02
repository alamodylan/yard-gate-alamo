# app/models/site.py
from app.extensions import db

SCHEMA = "yard_gate_alamo"


class Site(db.Model):
    __tablename__ = "sites"
    __table_args__ = {"schema": SCHEMA}

    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(20), nullable=False, unique=True)  # MAERSK, COYOL, CALDERA, LIMON
    name = db.Column(db.String(80), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<Site {self.code} ({self.id})>"


class UserSite(db.Model):
    __tablename__ = "user_sites"
    __table_args__ = (
        db.UniqueConstraint("user_id", "site_id", name="uq_user_site"),
        {"schema": SCHEMA},
    )

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"),
        nullable=False,
    )

    site_id = db.Column(
        db.Integer,
        db.ForeignKey(f"{SCHEMA}.sites.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Relaciones útiles (no rompen nada si User/Site existen)
    user = db.relationship("User", backref=db.backref("user_sites", lazy=True))
    site = db.relationship("Site", backref=db.backref("user_sites", lazy=True))

    def __repr__(self) -> str:
        return f"<UserSite user_id={self.user_id} site_id={self.site_id}>"