from datetime import datetime
from app.extensions import db
from app.models.ticket import TicketPrint

def build_ticket_payload(app_name: str, movement, container) -> str:
    # Payload simple de texto (útil para reimpresión idéntica)
    # Luego puedes migrar a ESC/POS si haces Print Agent.
    when = movement.occurred_at.strftime("%Y-%m-%d %H:%M:%S")
    loc = ""
    if movement.bay_code:
        loc = f"{movement.bay_code} F{(movement.depth_row or 0):02d} N{movement.tier or 0}"

    lines = []
    lines.append(app_name)
    lines.append("-" * 28)
    lines.append(f"Fecha/Hora: {when}")
    lines.append(f"Mov: {movement.movement_type}")
    lines.append(f"Cont: {container.code}")
    lines.append(f"Tam: {container.size}")
    if loc:
        lines.append(f"Ubi: {loc}")
    if movement.driver_name:
        lines.append(f"Chofer: {movement.driver_name}")
    if movement.truck_plate:
        lines.append(f"Placa: {movement.truck_plate}")
    if movement.notes:
        lines.append("-" * 28)
        lines.append(movement.notes[:300])
    lines.append("-" * 28)
    return "\n".join(lines)

def register_ticket_print(movement_id: int, printed_by_user_id: int, payload: str) -> TicketPrint:
    row = TicketPrint(
        movement_id=movement_id,
        printed_by_user_id=printed_by_user_id,
        ticket_payload=payload,
        printed_at=datetime.utcnow(),
    )
    db.session.add(row)
    return row
