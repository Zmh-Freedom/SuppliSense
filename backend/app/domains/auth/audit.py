"""
Audit logging service. Failure must never break the main flow.
"""

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.auth.audit_repo import create_log

logger = get_logger(__name__)


def log_action(
    action: str,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    if not settings.USE_PG_USERS:
        return
    try:
        create_log(
            action=action,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception as exc:
        logger.error("audit_log_write_failed", action=action, user_id=user_id, error=str(exc))
