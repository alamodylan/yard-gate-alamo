# app/blueprints/tica/__init__.py

from flask import Blueprint


tica_bp = Blueprint(
    "tica",
    __name__,
    url_prefix="/tica",
)


from app.blueprints.tica import routes  # noqa: E402, F401