# app/services/yard_logic.py
from typing import Optional, Tuple
from app.extensions import db
from app.models.container import ContainerPosition
from app.models.yard import YardBay


def find_first_free_slot(bay_id: int) -> Optional[Tuple[int, int]]:
    """
    Retorna (depth_row, tier) del slot libre óptimo.

    REGLA DEFINITIVA DEL PREDIO:
    - Se llena de ADENTRO hacia AFUERA
      (depth_row más alto primero)
    - La altura (tier) se asigna automáticamente
      desde abajo hacia arriba (1 -> max_tiers)
    """

    bay = YardBay.query.get(bay_id)
    if not bay or not bay.is_active:
        return None

    # Slots ocupados
    occupied = set(
        db.session.query(
            ContainerPosition.depth_row,
            ContainerPosition.tier
        )
        .filter(ContainerPosition.bay_id == bay_id)
        .all()
    )

    # 1) depth_row: del fondo hacia el frente
    for depth_row in range(bay.max_depth_rows, 0, -1):
        # 2) tier: de abajo hacia arriba
        for tier in range(1, bay.max_tiers + 1):
            if (depth_row, tier) not in occupied:
                return (depth_row, tier)

    return None