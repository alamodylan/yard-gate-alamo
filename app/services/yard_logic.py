from typing import Optional, Tuple

from app.extensions import db
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay


def find_first_free_slot(bay_id: int) -> Optional[Tuple[int, int]]:
    """
    Retorna (depth_row, tier) del slot libre óptimo.

    Regla:
    - Buscar de ADENTRO hacia AFUERA:
      max_depth_rows -> 1
    - Buscar de abajo hacia arriba:
      1 -> max_tiers
    - Solo toma como ocupados contenedores realmente en patio.
    """

    bay = YardBay.query.get(bay_id)

    if not bay or not bay.is_active:
        return None

    max_depth_rows = int(bay.max_depth_rows or 0)
    max_tiers = int(bay.max_tiers or 0)

    if max_depth_rows < 1 or max_tiers < 1:
        return None

    occupied = {
        (int(p.depth_row), int(p.tier))
        for p in (
            db.session.query(ContainerPosition)
            .join(Container, Container.id == ContainerPosition.container_id)
            .filter(
                ContainerPosition.bay_id == bay.id,
                Container.is_in_yard == True,  # noqa: E712
                Container.site_id == bay.site_id,
            )
            .all()
        )
    }

    for depth_row in range(max_depth_rows, 0, -1):
        for tier in range(1, max_tiers + 1):
            if (depth_row, tier) not in occupied:
                return depth_row, tier

    return None