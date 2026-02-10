from flask import Blueprint

inventory_bp = Blueprint(
    "inventory",
    __name__,
    url_prefix=""
)

from app.blueprints.inventory import routes  # noqa