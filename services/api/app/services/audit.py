from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import AuditLog, SystemLog

log = logging.getLogger("clipx")


def audit(db: Session, actor: str, action: str, target: str | None = None, ip: str | None = None,
          **detail: Any) -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, ip=ip, detail=_jsonable(detail)))


def syslog(db: Session, level: str, component: str, event: str, message: str,
           session_id: UUID | None = None, **detail: Any) -> None:
    getattr(log, level.lower(), log.info)("[%s] %s: %s", component, event, message)
    db.add(SystemLog(level=level.upper(), component=component, event=event, message=message,
                     session_id=session_id, detail=_jsonable(detail)))


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = [x if isinstance(x, (str, int, float, bool)) or x is None else str(x) for x in v]
        elif isinstance(v, dict):
            out[k] = _jsonable(v)
        else:
            out[k] = str(v)
    return out
