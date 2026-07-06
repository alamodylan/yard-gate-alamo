# app/utils/permissions.py
from functools import wraps

from flask import abort
from flask_login import current_user


ROLE_PERMISSIONS = {
    "admin": {"*"},

    "supervision": {
        "map.view",
        "map.actions",
        "map_config.view",

        "gate_in.view",
        "gate_in.actions",

        "gate_out.view",
        "gate_out.actions",

        "inventory.view",
        "inventory.actions",

        "empty_list.view",
        "empty_list.actions",

        "dispatch.requests.view",
        "dispatch.requests.actions",
        "dispatch.assigned.view",
        "dispatch.assigned.actions",
        "dispatch.new_request",
        "dispatch.agenda.view",
        "dispatch.agenda.actions",
        "dispatch.prelist.view",
        "dispatch.prelist.actions",
        "dispatch.mounted.view",
        "dispatch.mounted.actions",

        "chassis.view",
        "chassis.actions",
        "tires.view",
        "tires.actions",

        "eir.view",
        "eir.actions",
        "eir.confirm",
        "eir.pdf",

        "reports.view",
        "reports.export",

        "audit.view",
    },

    "patio": {
        "map.view",
        "map.actions",

        "gate_in.view",
        "gate_in.actions",

        "gate_out.view",
        "gate_out.actions",

        "inventory.view",
        "empty_list.view",

        "dispatch.requests.view",
        "dispatch.assigned.view",
        "dispatch.agenda.view",
        "dispatch.prelist.view",

        "eir.view",
        "eir.actions",
    },

    "inspeccion": {
        "gate_in.view",
        "gate_in.actions",

        "gate_out.view",
        "gate_out.actions",

        "inventory.view",
        "empty_list.view",

        "dispatch.requests.view",
        "dispatch.assigned.view",
        "dispatch.agenda.view",
        "dispatch.prelist.view",

        "eir.view",
        "eir.actions",
    },

    "control_equipo": {
        "inventory.view",
        "inventory.actions",

        "empty_list.view",
        "empty_list.actions",

        "eir.view",
        "reports.view",
    },

    "despachador": {
        "inventory.view",
        "empty_list.view",

        "dispatch.requests.view",
        "dispatch.requests.actions",
        "dispatch.assigned.view",
        "dispatch.assigned.actions",
        "dispatch.new_request",
        "dispatch.agenda.view",
        "dispatch.prelist.view",

        "eir.view",
    },

    "operador": {
        "map.view",
        "inventory.view",
        "empty_list.view",
        "dispatch.agenda.view",
        "dispatch.prelist.view",
    },

    "taller": {
        # Pendiente definir
    },

    "tracking": {
        "dispatch.prelist.view",
        "tracking.view",
        "tracking.inventory.view",
        "tracking.requests.view",
        "tracking.assigned.view",
    },

    # Compatibilidad temporal con usuarios viejos.
    # Se comporta como patio para no romper operación existente.
    "predio": {
        "map.view",
        "map.actions",

        "gate_in.view",
        "gate_in.actions",

        "gate_out.view",
        "gate_out.actions",

        "inventory.view",
        "empty_list.view",

        "dispatch.requests.view",
        "dispatch.assigned.view",
        "dispatch.agenda.view",
        "dispatch.prelist.view",

        "eir.view",
        "eir.actions",
    },
}


def user_has_permission(user, permission: str) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False

    role = (getattr(user, "role", "") or "").strip().lower()
    permissions = ROLE_PERMISSIONS.get(role, set())

    return "*" in permissions or permission in permissions


def require_permission(permission: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not user_has_permission(current_user, permission):
                abort(403)

            return view_func(*args, **kwargs)

        return wrapper

    return decorator