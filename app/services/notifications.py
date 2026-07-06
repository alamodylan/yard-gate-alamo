from datetime import datetime

from app.extensions import db
from app.models.dispatch import UserNotification
from app.models.user import User
from app.models.site import UserSite


def _normalize_roles(roles):
    return {
        (role or "").strip().lower()
        for role in roles
        if (role or "").strip()
    }


def users_for_notification_roles(site_id: int, roles: set[str]):
    roles = _normalize_roles(roles)

    if not site_id or not roles:
        return []

    query = (
        db.session.query(User)
        .outerjoin(UserSite, UserSite.user_id == User.id)
        .filter(
            User.is_active == True,  # noqa: E712
            User.role.in_(roles),
        )
    )

    users = query.all()

    result = []
    seen_ids = set()

    for user in users:
        role = (user.role or "").strip().lower()

        if user.id in seen_ids:
            continue

        if role == "admin" or user.can_access_site(site_id):
            result.append(user)
            seen_ids.add(user.id)

    return result


def create_notifications_for_roles(
    *,
    site_id: int,
    roles: set[str],
    title: str,
    message: str,
    related_type: str | None = None,
    related_id: int | None = None,
    exclude_user_ids: set[int] | None = None,
):
    exclude_user_ids = exclude_user_ids or set()

    final_roles = set(roles or set())
    final_roles.add("admin")

    users = users_for_notification_roles(site_id, final_roles)

    created = []

    for user in users:
        if user.id in exclude_user_ids:
            continue

        notification = UserNotification(
            site_id=site_id,
            user_id=user.id,
            title=title,
            message=message,
            related_type=related_type,
            related_id=related_id,
            is_read=False,
            created_at=datetime.utcnow(),
        )

        db.session.add(notification)
        created.append(notification)

    return created


def notification_url(notification):
    related_type = (notification.related_type or "").strip().upper()
    related_id = notification.related_id

    if related_type in {"DISPATCH_REQUEST", "CONTAINER_REQUEST"} and related_id:
        return "dispatch.request_detail", {"request_id": related_id}

    if related_type in {"DISPATCH_ASSIGNMENT", "CONTAINER_ASSIGNED"}:
        return "dispatch.assigned_requests", {}

    if related_type == "GPS_REQUEST":
        return "dispatch.gps_requests", {}

    if related_type == "GPS_ASSIGNED":
        return "dispatch.gps_assigned", {}

    if related_type == "EMPTY_LIST":
        return "inventory.evacuation_list", {}

    return None, {}