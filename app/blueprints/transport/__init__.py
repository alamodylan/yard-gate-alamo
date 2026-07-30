# app/blueprints/transport/__init__.py

from flask import Blueprint


transport_bp = Blueprint(
    "transport",
    __name__,
    url_prefix="/transport",
)


from app.blueprints.transport import routes  # noqa: E402, F401