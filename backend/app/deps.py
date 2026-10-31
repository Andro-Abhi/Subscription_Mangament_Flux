from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuditLog, Role, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        data = decode_token(creds.credentials)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    user = db.get(User, int(data["sub"]))
    if not user:
        raise HTTPException(401, "User no longer exists")
    return user


def require_roles(*roles: Role):
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "You do not have permission to do this")
        return user
    return checker


admin_only = require_roles(Role.admin)
staff_or_admin = require_roles(Role.admin, Role.staff)


def audit(db: Session, user: User | None, action: str, entity: str, entity_id: int | None, detail: str = ""):
    db.add(AuditLog(user_id=user.id if user else None, action=action, entity=entity,
                    entity_id=entity_id, detail=detail))
