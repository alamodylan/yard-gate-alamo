from typing import Optional, Dict, Any
from app.extensions import db
from app.models.audit import AuditLog

def audit_log(user_id: int, action: str, entity_type: Optional[str] = None, entity_id: Optional[int] = None, meta: Optional[Dict[str, Any]] = None):
    row = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        meta=meta or {},
    )
    db.session.add(row)