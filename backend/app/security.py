import hashlib
import re
import secrets
from datetime import datetime, timedelta

import bcrypt
import jwt

from .config import settings

SPECIAL = re.compile(r"[^A-Za-z0-9]")


def validate_password(pw: str) -> list[str]:
    """Rules from the brief: more than 8 chars, upper, lower, special."""
    errors = []
    if len(pw) <= 8:
        errors.append("must be more than 8 characters")
    if not re.search(r"[A-Z]", pw):
        errors.append("must contain an uppercase letter")
    if not re.search(r"[a-z]", pw):
        errors.append("must contain a lowercase letter")
    if not SPECIAL.search(pw):
        errors.append("must contain a special character")
    return errors


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int, role: str) -> str:
    exp = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp}, settings.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])


def new_reset_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
