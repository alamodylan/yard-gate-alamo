from flask import Blueprint

dispatch_bp = Blueprint(
    "dispatch",
    __name__,
    url_prefix="/dispatch",
)

from . import routes  # noqa: E402,F401