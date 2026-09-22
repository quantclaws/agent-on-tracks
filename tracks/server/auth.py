"""Local single-user auth, session and CSRF (IF-WEBAUTH-001).

Password provisioning (scrypt), login/logout, session issue/resolve and
CSRF checks per interfaces §1f. The session token is stored only as its
sha256; the CSRF credential is a 256-bit random hex value kept in the
``sessions.csrf_hash`` column and compared constant-time against the
``X-Trac-CSRF`` header. Plaintext passwords and session tokens never touch
disk or logs.

``db`` is the service.db writer connection (interfaces §1c); the auth and
sessions tables hold no plaintext.

Contract token: IF-WEBAUTH-001.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class Session:
    actor: str
    actor_class: str  # "human" for HTTP sessions (§1f.5)
    csrf_token: str
    expires_at: str
    token: str | None = None  # session cookie value (never persisted)


_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_LEN = 16
_SCRYPT_DKLEN = 32
# 128 * r * n = 32 MiB for the locked params; OpenSSL's 32 MiB default check
# includes the p/blockterm overhead, so raise the ceiling explicitly.
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SESSION_TTL_DAYS = 7  # rolling 7-day expiration (§1f.2)
_LOCAL_ACTOR = "local-user"  # 本机单用户 (interfaces §1f.1)

_HASH_PREFIX = "scrypt$"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=_SCRYPT_MAXMEM,
        dklen=_SCRYPT_DKLEN,
    )


def provision_password(db: Any, password: str) -> None:
    """First-start password provisioning (scrypt hash into the auth table)."""
    salt = secrets.token_bytes(_SCRYPT_SALT_LEN)
    digest = _derive_key(password, salt)
    stored = f"{_HASH_PREFIX}{salt.hex()}${digest.hex()}"
    db.execute(
        "INSERT OR REPLACE INTO auth (actor, password_hash, created_at) VALUES (?, ?, ?)",
        (_LOCAL_ACTOR, stored, _utcnow()),
    )
    db.commit()


def verify_password(db: Any, password: str) -> bool:
    """Constant-time password verification against the stored scrypt hash."""
    row = db.execute("SELECT password_hash FROM auth LIMIT 1").fetchone()
    if row is None or not row[0]:
        return False
    try:
        prefix, salt_hex, digest_hex = str(row[0]).split("$")
        if prefix != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(_derive_key(password, salt), expected)


def issue_session(db: Any, actor: str) -> Session:
    """Create a session (token + CSRF, digests persisted) and return it.

    The client gets the session token (cookie) and the CSRF credential; the
    server persists only the token's sha256 plus the 64-hex CSRF value.
    """
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_hex(32)
    now = _utcnow()
    expires = (datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)).isoformat()
    db.execute(
        "INSERT INTO sessions (token_hash, actor, csrf_hash, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (_sha256_hex(token), actor, csrf, now, expires),
    )
    db.commit()
    return Session(
        actor=actor,
        actor_class="human",
        csrf_token=csrf,
        expires_at=expires,
        token=token,
    )


def resolve_session(db: Any, token: str | None) -> Session | None:
    """Resolve a session cookie value; None when missing/expired."""
    if not isinstance(token, str) or not token:
        return None
    row = db.execute(
        "SELECT actor, csrf_hash, expires_at FROM sessions WHERE token_hash = ?",
        (_sha256_hex(token),),
    ).fetchone()
    if row is None:
        return None
    if (row[2] or "") < _utcnow():
        return None
    return Session(
        actor=row[0],
        actor_class="human",
        csrf_token=row[1],
        expires_at=row[2],
    )


def check_csrf(session: Session, header_token: str | None) -> bool:
    """Constant-time CSRF check for mutating endpoints (§1f.3)."""
    if not isinstance(header_token, str) or not header_token:
        return False
    return hmac.compare_digest(session.csrf_token, header_token)
