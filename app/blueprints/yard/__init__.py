from flask import Blueprint

yard_bp = Blueprint(
    "yard",
    __name__,
    template_folder="../../templates"
)

from . import routes  # noqa: F401
