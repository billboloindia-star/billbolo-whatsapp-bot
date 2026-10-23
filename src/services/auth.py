"""Password hashing and JWT session handling for the web app."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import HTTPException, Request
from passlib.context import CryptContext

from src.config import settings

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 30

# Super admin: this phone number bypasses all usage quotas, regardless of how
# it's formatted at signup (+91, spaces, etc.) - compared by digit suffix.
ADMIN_PHONE_SUFFIX = "8123422932"


def is_admin_phone(phone_number: str) -> bool:
    """Check if a phone number matches the super admin, ignoring formatting."""
    digits = "".join(c for c in (phone_number or "") if c.isdigit())
    return digits.endswith(ADMIN_PHONE_SUFFIX)


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage."""
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored hash."""
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def create_jwt(user_id: str) -> str:
    """Issue a signed session token for a user."""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> Optional[str]:
    """Validate a session token and return the user_id, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_current_user_id(request: Request) -> str:
    """FastAPI dependency: extract and validate the bearer token, return user_id.

    Raises HTTPException(401) if missing or invalid - callers depending on this
    get automatic auth enforcement without repeating the header-parsing logic.
    """
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    token = auth_header[7:].strip()
    user_id = decode_jwt(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return user_id
