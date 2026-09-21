"""T-004 RED: local auth, command guard and adjudication ownership
(IF-WEBAUTH-001 / IF-CMDGUARD-001).

Devon-owned unit RED for the T-004 delivery slice:

- ``tracks/server/auth.py``: scrypt password provisioning/verification,
  session issue/resolve and CSRF comparison (interfaces §1f.1-3);
- ``tracks/server/guard.py``: closed-kind / schema validation, actor_class
  partition and realpath scope enforcement (interfaces §1f.5, §1g.1-2);
- the FR-0314.4 adjudication-ownership determination in
  ``tracks/discuss/gate.py`` (pure, shared with the service side via lazy
  import) wired into the ``set-status resolved`` CLI path
  ``tracks/discuss/cli.py`` (interfaces §1f.4).

The scaffold bodies of auth.py/guard.py still raise their IF- stub token and
``gate.adjudication_owner`` does not exist yet; every failing node
deliberately guards that stub/symbol state
into a real ``AssertionError`` so the records classify as assertion_failure
(no stub_token, no assembly errors). Auth units pass the real service.db
connection per interfaces §1c (auth/sessions tables); nothing here mocks the
system under test.

AC: FR-0314 — TRACKS-TRACE IF-WEBAUTH-001 / IF-CMDGUARD-001.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tracks.discuss import cli as discuss_cli
from tracks.discuss import gate as discuss_gate
from tracks.discuss.locate import token_for
from tracks.discuss.parser import parse_threads
from tracks.server import auth, guard

_SERVICE_SCHEMA = (
    "CREATE TABLE auth (actor TEXT PRIMARY KEY, password_hash TEXT, created_at TEXT);"
    "CREATE TABLE sessions (token_hash TEXT PRIMARY KEY, actor TEXT, csrf_hash TEXT,"
    " created_at TEXT, expires_at TEXT);"
)

_HUMAN_REQUEST = "# Story\n\n> **Agent:** 本轮实现完成，请 @Human 裁决是否接受。\n"
_REVIEWER_REQUEST = "# Story\n\n> **Agent:** 请 @Sage 裁决措辞是否合适。\n"
_NO_REQUEST = "# Story\n\n> **Agent:** 自问自答，本轮没有外部裁决需求。\n"
_REPLY_ONLY_REQUEST = "# Story\n\n> **Agent:** 继续推进。\n>> **Sage:** 请 @Human 裁决此条。\n"


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_sha256_hex(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _calling(call, label: str):
    """Call a scaffold seam; a NotImplementedError stub becomes an assertion."""
    try:
        return call()
    except NotImplementedError as exc:
        raise AssertionError(f"{label} is still a stub: {exc}") from None


def _rejected(call):
    """Run a guard call expecting GuardRejection; stub/silent states assert."""
    try:
        call()
    except guard.GuardRejection as rejection:
        return rejection
    except NotImplementedError as exc:
        raise AssertionError(f"guard is still a stub: {exc}") from None
    raise AssertionError("guard accepted a payload the contract requires rejecting")


def _status_of(path: Path, thread_id: str) -> str:
    threads = parse_threads(path.read_text(encoding="utf-8"))
    return next(t for t in threads if t.thread_id == thread_id).status


def _adjudication_owner(text: str, thread_id: str):
    fn = getattr(discuss_gate, "adjudication_owner", None)
    assert fn is not None, (
        "discuss.gate.adjudication_owner missing - FR-0314.4 requires the "
        "adjudication-ownership determination in tracks/discuss/gate.py"
    )
    return fn(text, thread_id)


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    home = tmp_path / "service-home"
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "service.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SERVICE_SCHEMA)
    conn.commit()
    return conn


def _seed_session(db: sqlite3.Connection, token: str, *, actor: str, expires_at: str) -> None:
    db.execute(
        "INSERT INTO sessions (token_hash, actor, csrf_hash, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (_sha256_hex(token), actor, "c" * 64, _now_iso(), expires_at),
    )
    db.commit()


def _resolve_cli(tmp_path: Path, thread_id: str, operator: str) -> int:
    text = (tmp_path / "story.md").read_text(encoding="utf-8")
    thread = next(t for t in parse_threads(text) if t.thread_id == thread_id)
    return discuss_cli.run_discuss(
        tmp_path,
        [
            "set-status",
            "--file",
            "story.md",
            "--thread-id",
            thread_id,
            "--token",
            json.dumps(token_for(thread)),
            "--status",
            "resolved",
            "--operator",
            operator,
        ],
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- guard: closed kind / schema validation (interfaces §1g.1) ----------------


# AC-FR0314-03@v0.9 TRACKS-TRACE IF-CMDGUARD-001 structured commands accepted
def test_guard_accepts_structured_payload():
    _calling(lambda: guard.validate_command_payload("pause_run", {"run_id": "R1"}), "guard")
    _calling(
        lambda: guard.validate_command_payload(
            "create_run",
            {
                "project_id": "P1",
                "journey": "feature",
                "version": "v0.9",
                "story": "as a user",
                "issue": None,
                "target": None,
                "preempt": False,
            },
        ),
        "guard",
    )


# AC-FR0314-03@v0.9 TRACKS-TRACE IF-CMDGUARD-001 arbitrary shell kind rejected
def test_guard_rejects_unknown_shell_kind():
    rejection = _rejected(
        lambda: guard.validate_command_payload("run_shell", {"command": "rm -rf /"})
    )
    assert rejection.reason == "guard_blocked"


# AC-FR0314-03@v0.9 TRACKS-TRACE IF-CMDGUARD-001 free-form exec field rejected
def test_guard_rejects_free_form_execution_field():
    rejection = _rejected(
        lambda: guard.validate_command_payload("pause_run", {"run_id": "R1", "shell": "rm -rf /"})
    )
    assert rejection.reason in {"guard_blocked", "validation_failed"}


# AC-FR0314-03@v0.9 TRACKS-TRACE IF-CMDGUARD-001 param type mismatch rejected
def test_guard_rejects_param_type_mismatch():
    rejection = _rejected(lambda: guard.validate_command_payload("pause_run", {"run_id": 123}))
    assert rejection.reason in {"guard_blocked", "validation_failed"}


# -- guard: actor_class partition (interfaces §1f.5) ---------------------------


# AC-FR0314-02@v0.9 TRACKS-TRACE IF-CMDGUARD-001 system actor blocked on human kind
def test_guard_blocks_system_actor_on_human_kind():
    rejection = _rejected(
        lambda: guard.check_actor_class("record_stage_approval", "system")
    )
    assert rejection.reason == "forbidden_actor"


# AC-FR0314-02@v0.9 TRACKS-TRACE IF-CMDGUARD-001 agent actor blocked on release decision
def test_guard_blocks_agent_actor_on_release_decision():
    rejection = _rejected(
        lambda: guard.check_actor_class("record_release_decision", "agent")
    )
    assert rejection.reason == "forbidden_actor"


# AC-FR0314-02@v0.9 TRACKS-TRACE IF-CMDGUARD-001 human actor allowed on human kind
def test_guard_allows_human_actor_on_human_kind():
    _calling(lambda: guard.check_actor_class("pause_run", "human"), "guard")
    _calling(lambda: guard.check_actor_class("create_run", "human"), "guard")


# AC-FR0314-02@v0.9 TRACKS-TRACE IF-CMDGUARD-001 human blocked on system kind
def test_guard_blocks_human_actor_on_system_kind():
    rejection = _rejected(lambda: guard.check_actor_class("drive_run", "human"))
    assert rejection.reason == "forbidden_actor"


# AC-FR0314-02@v0.9 TRACKS-TRACE IF-CMDGUARD-001 system actor allowed on drive_run
def test_guard_allows_system_actor_on_drive_run():
    _calling(lambda: guard.check_actor_class("drive_run", "system"), "guard")


# -- guard: realpath access scope (interfaces §1g.2) ---------------------------


# AC-FR0315-02@v0.9 TRACKS-TRACE IF-SECRECY-001 path inside permitted root allowed
def test_guard_scope_allows_path_inside_root(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    inside = root / ".tracks" / "evidence.json"
    inside.parent.mkdir(parents=True)
    inside.write_text("{}", encoding="utf-8")
    _calling(lambda: guard.check_path_scope(inside, [root.resolve()]), "guard")


# AC-FR0315-02@v0.9 TRACKS-TRACE IF-SECRECY-001 path outside root rejected
def test_guard_scope_rejects_path_outside_root(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret", encoding="utf-8")
    rejection = _rejected(lambda: guard.check_path_scope(outside, [root.resolve()]))
    assert rejection.reason == "scope_violation"


# AC-FR0315-02@v0.9 TRACKS-TRACE IF-SECRECY-001 symlink escape is scope_violation
def test_guard_scope_rejects_symlink_escape(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("secret", encoding="utf-8")
    link = root / "link.txt"
    link.symlink_to(secret)
    rejection = _rejected(lambda: guard.check_path_scope(link, [root.resolve()]))
    assert rejection.reason == "scope_violation"


# -- auth: scrypt provision / verify (interfaces §1f.1) ------------------------


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 provision enables verification
def test_auth_provision_then_verify_password(db):
    _calling(lambda: auth.provision_password(db, "correct horse"), "auth")
    assert _calling(lambda: auth.verify_password(db, "correct horse"), "auth") is True
    assert _calling(lambda: auth.verify_password(db, "wrong horse"), "auth") is False


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 no user verifies false
def test_auth_verify_password_unprovisioned_is_false(db):
    assert _calling(lambda: auth.verify_password(db, "anything"), "auth") is False


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 plaintext never persisted
def test_auth_provision_stores_hash_not_plaintext(db):
    password = "hunter2-unique-value"
    _calling(lambda: auth.provision_password(db, password), "auth")
    row = db.execute("SELECT password_hash FROM auth").fetchone()
    assert row is not None, "provisioning must persist the auth row"
    stored = row["password_hash"]
    assert stored and isinstance(stored, str)
    assert stored != password
    assert password not in stored


# -- auth: session issue / resolve / csrf (interfaces §1f.2-3) -----------------


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 session issued with digests only
def test_auth_issue_session_returns_session_and_persists_hashes(db):
    before = datetime.now(timezone.utc)
    session = _calling(lambda: auth.issue_session(db, "local-user"), "auth")
    assert session.actor == "local-user"
    assert session.actor_class == "human"
    assert isinstance(session.csrf_token, str) and session.csrf_token
    expiry = datetime.fromisoformat(session.expires_at)
    assert before + timedelta(days=6) < expiry
    assert expiry <= datetime.now(timezone.utc) + timedelta(days=8)
    rows = db.execute("SELECT token_hash, actor, csrf_hash FROM sessions").fetchall()
    assert len(rows) == 1
    assert rows[0]["actor"] == "local-user"
    assert _is_sha256_hex(rows[0]["token_hash"])
    assert _is_sha256_hex(rows[0]["csrf_hash"])


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 session resolves by cookie token
def test_auth_resolve_session_roundtrips_seeded_row(db):
    token = "session-token-1"
    csrf = "c" * 64
    created = _now_iso()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    db.execute(
        "INSERT INTO sessions (token_hash, actor, csrf_hash, created_at, expires_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (_sha256_hex(token), "local-user", csrf, created, expires),
    )
    db.commit()
    resolved = _calling(lambda: auth.resolve_session(db, token), "auth")
    assert resolved is not None
    assert resolved.actor == "local-user"
    assert resolved.actor_class == "human"
    assert resolved.csrf_token == csrf
    assert resolved.expires_at == expires


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 expired/unknown/missing -> None
def test_auth_resolve_session_none_for_expired_unknown_missing(db):
    stale = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    _seed_session(db, "stale-token", actor="local-user", expires_at=stale)
    assert _calling(lambda: auth.resolve_session(db, "stale-token"), "auth") is None
    assert _calling(lambda: auth.resolve_session(db, "no-such-token"), "auth") is None
    assert _calling(lambda: auth.resolve_session(db, None), "auth") is None


# AC-FR0314-01@v0.9 TRACKS-TRACE IF-WEBAUTH-001 CSRF header compared to session
def test_auth_check_csrf_constant_time_match():
    session = auth.Session(
        actor="local-user",
        actor_class="human",
        csrf_token="csrf-cred",
        expires_at="2030-01-01T00:00:00+00:00",
    )
    assert _calling(lambda: auth.check_csrf(session, "csrf-cred"), "auth") is True
    assert _calling(lambda: auth.check_csrf(session, "csrf-nope"), "auth") is False
    assert _calling(lambda: auth.check_csrf(session, None), "auth") is False


# -- discuss gate: adjudication ownership determination (interfaces §1f.4) -----


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 @Human request binds the owner
def test_gate_adjudication_owner_for_human_request():
    assert _adjudication_owner(_HUMAN_REQUEST, "T-001") == "Human"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 @reviewer request binds the owner
def test_gate_adjudication_owner_for_reviewer_request():
    assert _adjudication_owner(_REVIEWER_REQUEST, "T-001") == "Sage"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 no request -> no owner
def test_gate_adjudication_owner_none_without_request():
    assert _adjudication_owner(_NO_REQUEST, "T-001") is None


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 reply mention is not a request
def test_gate_adjudication_owner_ignores_reply_mention():
    assert _adjudication_owner(_REPLY_ONLY_REQUEST, "T-001") is None


# -- discuss cli: set-status resolved honors ownership (interfaces §1f.4) ------


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 agent self-resolve is void
def test_cli_agent_self_resolve_of_requested_thread_is_void(tmp_path: Path):
    doc = tmp_path / "story.md"
    doc.write_text(_HUMAN_REQUEST, encoding="utf-8")
    rc = _resolve_cli(tmp_path, "T-001", "Agent")
    assert rc != 0, "the agent initiator must not self-resolve a @Human-requested thread"
    assert _status_of(doc, "T-001") == "open"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 reviewer request same void
def test_cli_agent_self_resolve_of_reviewer_thread_is_void(tmp_path: Path):
    doc = tmp_path / "story.md"
    doc.write_text(_REVIEWER_REQUEST, encoding="utf-8")
    rc = _resolve_cli(tmp_path, "T-001", "Agent")
    assert rc != 0, "the agent initiator must not self-resolve a @reviewer-requested thread"
    assert _status_of(doc, "T-001") == "open"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 unrelated operator refused
def test_cli_unrelated_operator_cannot_resolve_requested_thread(tmp_path: Path):
    doc = tmp_path / "story.md"
    doc.write_text(_HUMAN_REQUEST, encoding="utf-8")
    rc = _resolve_cli(tmp_path, "T-001", "Sage")
    assert rc != 0
    assert _status_of(doc, "T-001") == "open"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 CLI keeps the operator==initiator rule
def test_cli_requested_party_alone_is_still_refused_on_cli(tmp_path: Path):
    doc = tmp_path / "story.md"
    doc.write_text(_HUMAN_REQUEST, encoding="utf-8")
    rc = _resolve_cli(tmp_path, "T-001", "Human")
    assert rc != 0, "the CLI keeps the existing operator==initiator consistency rule (FR-090)"
    assert _status_of(doc, "T-001") == "open"


# AC-FR0314-04@v0.9 TRACKS-TRACE IF-WEBAUTH-001 unrequested threads keep resolving
def test_cli_unrequested_thread_resolves_by_initiator(tmp_path: Path):
    doc = tmp_path / "story.md"
    doc.write_text(_NO_REQUEST, encoding="utf-8")
    rc = _resolve_cli(tmp_path, "T-001", "Agent")
    assert rc == 0
    assert _status_of(doc, "T-001") == "resolved"
