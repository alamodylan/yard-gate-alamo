from datetime import datetime, timezone
import pytz

from app.extensions import db
from app.models.ticket import TicketPrint

CR_TZ = pytz.timezone("America/Costa_Rica")


def _to_cr(dt: datetime | None) -> datetime | None:
    """
    Convierte un datetime a hora de Costa Rica.
    Si viene naive (sin tzinfo), asumimos que está en UTC (porque usas datetime.utcnow()).
    """
    if not dt:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(CR_TZ)


def build_ticket_payload(app_name: str, movement, container) -> str:
    # Payload simple de texto (útil para reimpresión idéntica)
    # Nota: este payload es lo que imprime la Epson vía print agent.

    when_cr = _to_cr(getattr(movement, "occurred_at", None))
    when_str = when_cr.strftime("%d/%m/%Y %H:%M:%S") if when_cr else "—"

    loc = ""
    if getattr(movement, "bay_code", None):
        loc = f"{movement.bay_code} F{int(movement.depth_row or 0):02d} N{int(movement.tier or 0)}"

    lines: list[str] = []

    # ===== ENCABEZADO PROFESIONAL =====
    lines.append("=" * 32)
    lines.append("  ALAMO TERMINALES MARITIMAS")
    lines.append("        YARD GATE ALAMO")
    lines.append("=" * 32)

    lines.append(f"Fecha/Hora (CR): {when_str}")
    lines.append(f"Mov: {movement.movement_type}")
    lines.append(f"Cont: {container.code}")
    lines.append(f"Tam: {container.size}")

    if loc:
        lines.append(f"Ubi: {loc}")

    if getattr(movement, "driver_name", None):
        lines.append(f"Chofer: {movement.driver_name}")

    if getattr(movement, "driver_id_doc", None):
        lines.append(f"Cedula: {movement.driver_id_doc}")

    if getattr(movement, "truck_plate", None):
        lines.append(f"Placa: {movement.truck_plate}")

    if getattr(movement, "notes", None):
        lines.append("-" * 32)
        lines.append(str(movement.notes)[:300])

    # Bloque de firma
    lines.append("-" * 32)
    lines.append("Firma transportista:")
    lines.append("____________________________")
    lines.append("Nombre: _____________________")
    lines.append("Cedula: _____________________")
    lines.append("" * 32)
    lines.append("" * 32)
    lines.append("-" * 32)
    lines.append("-" * 32)
    lines.append("-" * 32)
    lines.append("-" * 32)
    lines.append("-" * 32)

    # Construimos el payload
    payload = "\n".join(lines)

    # Avance limpio de papel (solo saltos reales)
    payload = payload.rstrip() + ("\n" * 16)
    lines.append("-" * 32)

    return payload



def register_ticket_print(movement_id: int, printed_by_user_id: int, payload: str) -> TicketPrint:
    row = TicketPrint(
        movement_id=movement_id,
        printed_by_user_id=printed_by_user_id,
        ticket_payload=payload,
        printed_at=datetime.utcnow(),  # se queda en UTC (está bien para auditoría)
    )
    db.session.add(row)
    return row

