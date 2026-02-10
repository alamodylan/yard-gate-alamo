from flask import render_template, request
from flask_login import login_required
from app.extensions import db
from app.models.container import Container, ContainerPosition
from app.models.yard import YardBay

# Si tu app usa blueprint: inventory_bp
from app.blueprints.inventory import inventory_bp


@inventory_bp.get("/inventory")
@login_required
def inventory_index():
    in_yard = request.args.get("in_yard")  # "1" / "0" / None

    q = (
        db.session.query(Container, ContainerPosition, YardBay)
        .outerjoin(ContainerPosition, ContainerPosition.container_id == Container.id)
        .outerjoin(YardBay, YardBay.id == ContainerPosition.bay_id)
    )

    if in_yard == "1":
        q = q.filter(Container.is_in_yard == True)  # noqa: E712
    elif in_yard == "0":
        q = q.filter(Container.is_in_yard == False)  # noqa: E712

    q = q.order_by(Container.updated_at.desc())

    rows = q.all()

    items = []
    for c, pos, bay in rows:
        items.append({
            "id": c.id,
            "code": c.code,
            "size": c.size,
            "year": c.year,
            "is_in_yard": c.is_in_yard,
            "status_notes": c.status_notes,
            "position": None if not pos else {
                "bay_code": bay.code if bay else None,
                "depth_row": pos.depth_row,
                "tier": pos.tier,
            }
        })

    return render_template(
        "inventory/index.html",
        items=items,
        in_yard=in_yard
    )
