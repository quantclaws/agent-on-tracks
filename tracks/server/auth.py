"""Local single-user auth, session and CSRF (IF-WEBAUTH-001).

Password provisioning (scrypt), login/logout, session issue/resolve and
CSRF checks per interfaces §1f. Token and CSRF values are stored only as
sha256 digests; plaintext passwords/tokens never touch disk or logs.

Contract token: IF-WEBAUTH-001.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Session:
    actor: str
    actor_class: str  # "human" for HTTP sessions (§1f.5)
    csrf_token: str
    expires_at: str


def provision_password(password: str, db: Any) -> None:
    """First-start password provisioning (scrypt hash into the auth table)."""
    raise NotImplementedError("IF-WEBAUTH-001")


def verify_password(db: Any, password: str) -> bool:
    """Constant-time password verification against the stored scrypt hash."""
    raise NotImplementedError("IF-WEBAUTH-001")


def issue_session(db: Any, actor: str) -> Session:
    """Create a session (token + CSRF, digests persisted) and return it."""
    raise NotImplementedError("IF-WEBAUTH-001")


def resolve_session(db: Any, token: str | None) -> Session | None:
    """Resolve a session cookie value; None when missing/expired."""
    raise NotImplementedError("IF-WEBAUTH-001")


def check_csrf(session: Session, header_token: str | None) -> bool:
    """Constant-time CSRF check for mutating endpoints (§1f.3)."""
    raise NotImplementedError("IF-WEBAUTH-001")
