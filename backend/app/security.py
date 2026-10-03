"""PyroLens Security: password hashing, role hierarchy, and token utilities."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

try:
    import jwt
except ImportError:
    jwt = None

from .config import get_settings

# Role hierarchy: admin includes analyst & viewer, analyst includes viewer
ROLE_HIERARCHY = {
    "admin": {"admin", "analyst", "viewer"},
    "analyst": {"analyst", "viewer"},
    "viewer": {"viewer"},
}

ITERATIONS = 100_000


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a cryptographically secure random salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        ITERATIONS,
    )
    return f"pbkdf2_sha256${ITERATIONS}${salt}${key.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against stored PBKDF2 hash using constant-time comparison."""
    if not hashed_password or not plain_password:
        return False
    parts = hashed_password.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        _, iterations_str, salt, stored_hash = parts
        try:
            iterations = int(iterations_str)
        except ValueError:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            plain_password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(candidate.hex(), stored_hash)
    # Check argon2 fallback if hash was created by argon2
    if hashed_password.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher
            return PasswordHasher().verify(hashed_password, plain_password)
        except Exception:
            return False
    # Check legacy plain match (for development only if password was plain text)
    settings = get_settings()
    if settings.environment == "development" and plain_password == hashed_password:
        return True
    return False


def has_role_permission(user_role: str, *required_roles: str) -> bool:
    """Check if user_role has sufficient permission based on role hierarchy."""
    user_permissions = ROLE_HIERARCHY.get(user_role.lower(), {user_role.lower()})
    return any(role.lower() in user_permissions for role in required_roles)


def create_access_token(
    subject: str,
    role: str = "analyst",
    expires_delta: timedelta | None = None,
    extra_claims: dict | None = None,
) -> tuple[str, datetime]:
    """Generate a signed JWT token."""
    settings = get_settings()
    if jwt is None:
        raise RuntimeError("PyJWT is not installed")
    delta = expires_delta or timedelta(hours=8)
    expires = datetime.now(timezone.utc) + delta
    payload = {
        "sub": subject,
        "role": role,
        "roles": [role],
        "exp": expires,
        "iat": datetime.now(timezone.utc),
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires

