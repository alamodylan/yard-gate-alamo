from flask import Blueprint

yard_bp = Blueprint(
    "yard",
    __name__,
    template_folder="../../templates"
)

# =====================================================
# BASE
# =====================================================
from . import routes  # noqa: F401

# =====================================================
# RUTAS SEPARADAS
# =====================================================
from . import routes_yard_api  # noqa: F401
from . import routes_gate_in  # noqa: F401
from . import routes_gate_out  # noqa: F401
from . import routes_eir  # noqa: F401
from . import routes_chassis  # noqa: F401
from . import routes_tires  # noqa: F401
from . import routes_reports  # noqa: F401
from . import routes_print  # noqa: F401
