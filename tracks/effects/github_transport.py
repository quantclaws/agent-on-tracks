"""GitHub effect boundary core transport (FR-0200, FR-0330).

Env (`GITHUB_TOKEN`, `TRAC_GITHUB_REPO`, `TRAC_GITHUB_PROJECT`) is read by the
backend/API helpers in this module; kernel `decide()`/`project()` never see it.
The fake channel (`TRAC_AGENT_BACKEND=fake` / `TRAC_FAKE_SIMULATE` / missing
token) uses a deterministic stand-in that never touches the network.

This module owns the classified error, live TLS (IF-TLS-001), bounded
transport retries, the network-panel proxy fallback, the issue backends, and
the shared JSON request helpers. The public surface remains
``tracks.effects.github`` (re-exported).
"""

from __future__ import annotations

import functools
import http.client
import json
import os
import socket
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tracks.executor.hotfix import HostIssue

# HTTP status -> GithubIssuesError classification (shared by _request/_get).
_HTTP_ERROR_CLASSES = {401: "auth", 403: "rate_limit", 404: "not_found", 429: "rate_limit"}

# Transient transport retry (live GitHub reached over flaky TLS/connections:
# observed ssl.UNEXPECTED_EOF on the direct channel). Bounded: three total
# attempts with 0.5s/1s backoff; HTTP status errors are never retried and
# keep their existing classification.
_NETWORK_ATTEMPTS = 3
_NETWORK_BACKOFF_S = (0.5, 1.0)


def _facade():
    """The re-exporting facade, resolved lazily to keep imports cycle-free.

    Moved code calls patchable seams (``_get_any``, ``_api_*``, ...) through
    this handle so monkeypatching ``tracks.effects.github`` keeps working.
    """
    from tracks.effects import github

    return github


class GithubIssuesError(RuntimeError):
    """Classified GitHub failure: auth | network | rate_limit | not_found."""

    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


# -- IF-TLS-001 live channel TLS + credential prerequisites (FR-0330) ---------

_OPS_PREREQ_POINTER = (
    "GITHUB_TOKEN not set — see the live GitHub journey prerequisites "
    "section in docs/getting-started/installation.md "
    "(GITHUB_TOKEN / TRAC_GITHUB_REPO / TLS & TRAC_GITHUB_CA_BUNDLE)"
)


def _missing_token_error() -> GithubIssuesError:
    """Classified missing_token pointing at the ops prereq (1r.4.2)."""
    return GithubIssuesError("missing_token", _OPS_PREREQ_POINTER)


def _tls_ssl_context() -> ssl.SSLContext:
    """The explicit verifying TLS context for every live HTTPS call point
    (interfaces 1r.4.1): certifi bundle by default, explicit
    ``TRAC_GITHUB_CA_BUNDLE`` override; per call, never silently downgraded."""
    cafile = os.environ.get("TRAC_GITHUB_CA_BUNDLE") or _certifi_bundle()
    try:
        return ssl.create_default_context(cafile=cafile)
    except OSError as exc:
        raise GithubIssuesError("network", f"CA bundle unusable ({cafile}): {exc}") from exc


def _certifi_bundle() -> str:
    """The pinned certifi CA bundle path. Lazy import: isolated stand-in
    envs import this module without the live-channel dep; only the TLS call
    path may require it (fail-closed, never silently)."""
    try:
        import certifi  # runtime dep (pyproject pinned certifi==2026.7.22)
    except ImportError as exc:
        raise GithubIssuesError("network", "certifi is required for the "
            "live TLS channel — see docs/getting-started/installation.md"
        ) from exc
    return certifi.where()


class FakeIssueBackend:
    """Deterministic stand-in (D-05): no network; issues recorded in
    `.tracks/runtime/issues.json`. `TRAC_FAKE_SIMULATE` key `github:create`
    injects failures per create call (`fail` token, `|`-sequenced)."""

    def __init__(self, repo: Path, version: str):
        self.repo = repo
        self.version = version
        self.project = os.environ.get("TRAC_GITHUB_PROJECT", "fake-project")
        self._path = repo / ".tracks" / "runtime" / "issues.json"
        self._host_path = repo / ".tracks" / "runtime" / "host-issues.json"
        self._calls = 0

    def _token(self) -> str:
        raw = os.environ.get("TRAC_FAKE_SIMULATE", "")
        seq = None
        for part in raw.replace(",", ";").split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() == "github:create":
                    seq = [t.strip() for t in v.split("|")]
        if not seq:
            return "ok"
        token = seq[min(self._calls, len(seq) - 1)]
        self._calls += 1
        return token

    def create_issue(self, title: str, body: str, labels: list) -> str:
        if self._token() == "fail":
            raise GithubIssuesError("network", "simulated network failure")
        issues = {}
        if self._path.exists():
            issues = json.loads(self._path.read_text(encoding="utf-8"))
        issue_id = f"FAKE-{len(issues) + 1}"
        issues[issue_id] = {"title": title, "body": body, "labels": labels}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(issues, ensure_ascii=False, indent=1), encoding="utf-8")
        return issue_id

    def add_to_project(self, issue_id: str, project: str) -> None:
        pass  # deterministic no-op: association is implied by self.project

    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """Read channel (IF-HOTFIX-003): the host issue seed at
        ``.tracks/runtime/host-issues.json`` (interfaces.md §3b schema);
        missing file/key yields None (PRECHECK P-1 issue_not_found); no
        ``TRAC_FAKE_SIMULATE`` injection here (ARCH-006 §3.1)."""
        from tracks.executor.hotfix import HostIssue

        if not self._host_path.exists():
            return None
        try:
            data = json.loads(self._host_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        entry = data.get(str(issue_number))
        if not isinstance(entry, dict):
            return None
        labels = entry.get("labels") or []
        if not isinstance(labels, list):
            labels = []
        return HostIssue(
            number=issue_number,
            title=entry.get("title", ""),
            body=entry.get("body", ""),
            labels=tuple(str(label) for label in labels),
        )


def _is_transient_transport_error(exc: Exception) -> bool:
    """True for SSL/connection/timeout failures worth a bounded retry.

    ``HTTPError`` is deliberately excluded (its 4xx/5xx classification is
    terminal at this layer), and a ``URLError`` wrapping an HTTP error never
    reaches here because ``HTTPError`` is caught first.
    """
    return isinstance(exc, (ssl.SSLError, urllib.error.URLError, TimeoutError, ConnectionError))


# -- network-panel proxy fallback (settings window) --------------------------
# Direct connectivity always wins; the settings' ``network.proxy`` row is only
# consulted after the direct channel's bounded transient retries are
# exhausted. Everything here fails open: a missing home/table/row or an
# invalid value keeps the pre-existing direct-only behavior.

_PROXY_SETTING_KEY = "network.proxy"
_PROXY_TYPES = frozenset({"http", "https", "socks5"})


def _normalized_proxy(raw: object) -> dict | None:
    """Validated proxy config or None (same rules as the settings API)."""
    if not isinstance(raw, dict):
        return None
    proxy_type = raw.get("type")
    host = raw.get("host")
    port = raw.get("port")
    fallback = raw.get("github_fallback")
    if proxy_type not in _PROXY_TYPES:
        return None
    if not isinstance(host, str) or not host or len(host) > 255:
        return None
    if "://" in host or any(char.isspace() for char in host):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    if not isinstance(fallback, bool):
        return None
    return {
        "type": proxy_type,
        "host": host,
        "port": port,
        "github_fallback": fallback,
    }


def _proxy_config() -> dict | None:
    """Read ``network.proxy`` from the worker's service.db (read-only).

    ``TRAC_SERVE_HOME`` is set in the worker environment; CLI and other
    no-home call sites keep direct-only behavior. Missing home, missing
    store/table/row, or a malformed value all return None (fail-open).
    """
    home = os.environ.get("TRAC_SERVE_HOME")
    if not home:
        return None
    try:
        return _load_proxy_config(Path(home) / "service.db")
    except Exception:
        return None


def _load_proxy_config(db_path: Path) -> dict | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_PROXY_SETTING_KEY,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        raw = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return _normalized_proxy(raw)


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise OSError("SOCKS5 proxy closed the connection")
        data.extend(chunk)
    return bytes(data)


def _socks5_address(host: str) -> bytes:
    """ATYP-prefixed target address: IPv4/IPv6 literal or domain name."""
    for family, atyp in ((socket.AF_INET, b"\x01"), (socket.AF_INET6, b"\x04")):
        try:
            packed = socket.inet_pton(family, host)
        except OSError:
            continue
        return atyp + packed
    encoded = host.encode("idna")
    if not encoded or len(encoded) > 255:
        raise OSError("SOCKS5 target host is not encodable")
    return b"\x03" + bytes([len(encoded)]) + encoded


def _socks5_connect(
    proxy: dict, host: str, port: int, timeout: object
) -> socket.socket:
    """Minimal no-auth SOCKS5 CONNECT; returns the tunneled socket."""
    sock = socket.create_connection((proxy["host"], proxy["port"]), timeout=timeout)
    try:
        sock.sendall(b"\x05\x01\x00")
        if _read_exact(sock, 2) != b"\x05\x00":
            raise OSError("SOCKS5 proxy rejected the no-auth method")
        sock.sendall(
            b"\x05\x01\x00"
            + _socks5_address(host)
            + int(port).to_bytes(2, "big")
        )
        head = _read_exact(sock, 4)
        if head[0] != 0x05 or head[1] != 0x00:
            raise OSError(f"SOCKS5 CONNECT failed (reply {head[1]})")
        atyp = head[3]
        if atyp == 0x01:
            _read_exact(sock, 4)
        elif atyp == 0x03:
            _read_exact(sock, _read_exact(sock, 1)[0])
        elif atyp == 0x04:
            _read_exact(sock, 16)
        else:
            raise OSError("SOCKS5 reply carries an unknown address type")
        _read_exact(sock, 2)
        return sock
    except Exception:
        sock.close()
        raise


class _Socks5HTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection whose TCP leg is a SOCKS5 CONNECT tunnel."""

    def __init__(self, host: str, port: int | None = None, *, proxy: dict, **kwargs):
        super().__init__(host, port, **kwargs)
        self._proxy = proxy

    def connect(self) -> None:
        sock = _socks5_connect(self._proxy, self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(
                sock, server_hostname=self.host
            )
        except Exception:
            sock.close()
            raise


class _Socks5HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, proxy: dict, context: ssl.SSLContext):
        super().__init__(context=context)
        self._proxy = proxy

    def https_open(self, req):
        return self.do_open(
            functools.partial(_Socks5HTTPSConnection, proxy=self._proxy),
            req,
            context=self._context,
        )


def _proxy_urlopen_once(req: urllib.request.Request, proxy: dict):
    """One proxy attempt for an already-failed direct request.

    http/https proxies ride urllib's ProxyHandler; socks5 uses the minimal
    CONNECT connection above (no PySocks dependency). A response body is
    decoded exactly like the direct channel; transport failures propagate to
    the caller.
    """
    context = _tls_ssl_context()
    if proxy["type"] == "socks5":
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _Socks5HTTPSHandler(proxy, context)
        )
    else:
        proxy_url = f"{proxy['type']}://{proxy['host']}:{proxy['port']}"
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
            urllib.request.HTTPSHandler(context=context),
        )
    with opener.open(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _proxy_urlopen(req: urllib.request.Request, proxy: dict):
    """Proxy attempt with the same bounded transient retry as direct.

    HTTP status errors are terminal (never retried); SSL/connection/timeout
    failures including the SOCKS handshake's ``OSError`` get three total
    attempts with 0.5s/1s backoff.
    """
    attempt = 0
    while True:
        try:
            return _proxy_urlopen_once(req, proxy)
        except urllib.error.HTTPError:
            raise
        except (ssl.SSLError, urllib.error.URLError, TimeoutError,
                ConnectionError, OSError):
            attempt += 1
            if attempt >= _NETWORK_ATTEMPTS:
                raise
            time.sleep(_NETWORK_BACKOFF_S[attempt - 1])


def _urlopen_transport_retry(req: urllib.request.Request):
    """urlopen with bounded retry on transient transport errors.

    Non-transient errors keep the pre-existing classification path; the
    final transport failure is classified ``network`` exactly as before.
    """
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(
                req, timeout=30, context=_tls_ssl_context()
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError:
            raise
        except (ssl.SSLError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            attempt += 1
            if attempt >= _NETWORK_ATTEMPTS or not _is_transient_transport_error(exc):
                raise
            time.sleep(_NETWORK_BACKOFF_S[attempt - 1])


def _urlopen_with_fallback(req: urllib.request.Request) -> dict:
    """Direct bounded retries, then the configured proxy fallback.

    HTTP status errors are terminal on both channels and never fall back. A
    final transport failure consults ``network.proxy`` only when
    ``github_fallback`` is enabled; a proxy ``HTTPError`` propagates as-is,
    any other proxy failure annotates and re-raises the original direct error.
    """
    try:
        return _urlopen_transport_retry(req)
    except urllib.error.HTTPError:
        raise
    except (ssl.SSLError, urllib.error.URLError, TimeoutError, ConnectionError) as direct_error:
        proxy = _proxy_config()
        if proxy is None or not proxy.get("github_fallback"):
            raise
        try:
            return _proxy_urlopen(req, proxy)
        except urllib.error.HTTPError:
            raise
        except Exception as proxy_error:
            if hasattr(direct_error, "add_note"):
                direct_error.add_note(f"proxy fallback also failed: {proxy_error}")
            raise direct_error from proxy_error


def _api_base() -> str:
    """Explicit stand-in override or the real GitHub REST base."""
    return os.environ.get("TRAC_GITHUB_API_BASE", "https://api.github.com").rstrip("/")


def _get_any(req) -> dict:
    """Module-level request path shared by the read/verify helpers.

    Returns the decoded JSON body; direct transient failures are retried
    boundedly and then, with an enabled fallback, retried through the
    configured proxy — the same channel semantics as ``GithubBackend``.
    Every call carries the explicit IF-TLS-001 verifying SSL context and the
    fixed 30s timeout (interfaces 1r.4.1)."""
    return _urlopen_with_fallback(req)


class GithubBackend:
    """Live channel: GitHub REST via GITHUB_TOKEN / TRAC_GITHUB_REPO (owner/name);
    Project association via TRAC_GITHUB_PROJECT."""

    def __init__(self, repo: Path, version: str):
        self.token = os.environ["GITHUB_TOKEN"]
        self.gh_repo = os.environ.get("TRAC_GITHUB_REPO", "")
        self.project = os.environ.get("TRAC_GITHUB_PROJECT", "")
        if not self.gh_repo:
            raise GithubIssuesError("not_found", "TRAC_GITHUB_REPO not set (owner/name)")

    def _urlopen(self, req: urllib.request.Request) -> dict:
        """Perform a JSON request and classify HTTP/URL errors (shared by
        ``_request``/``_get``); raises ``GithubIssuesError`` classified.
        Every call carries the explicit IF-TLS-001 verifying SSL context.
        Direct transient failures are retried a bounded number of times, then
        the shared proxy fallback applies before ``network`` classification."""
        try:
            return _urlopen_with_fallback(req)
        except urllib.error.HTTPError as e:
            cls = _HTTP_ERROR_CLASSES.get(e.code, "network")
            raise GithubIssuesError(cls, f"HTTP {e.code}: {e.reason}") from e
        except (ssl.SSLError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if isinstance(e, urllib.error.URLError):
                raise GithubIssuesError("network", str(e.reason)) from e
            raise GithubIssuesError("network", str(e)) from e

    def _request(self, url: str, payload: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
            },
        )
        return self._urlopen(req)

    def _get(self, url: str) -> dict:
        """GET helper (IF-HOTFIX-003): read-only, same auth/error pattern
        as ``_request`` with ``method=GET``."""
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json"},
        )
        return self._urlopen(req)

    def create_issue(self, title: str, body: str, labels: list) -> str:
        data = self._request(
            f"{_api_base()}/repos/{self.gh_repo}/issues",
            {"title": title, "body": body, "labels": labels},
        )
        return str(data["number"])

    def add_to_project(self, issue_id: str, project: str) -> None:
        if not project:
            return
        self._request(
            f"{_api_base()}/projects/columns/{project}/cards",
            {"content_id": int(issue_id), "content_type": "Issue"},
        )

    def fetch_issue(self, issue_number: int) -> HostIssue | None:
        """Read channel (IF-HOTFIX-003): ``GET /repos/{repo}/issues/{n}``
        (ARCH-006 §3.1). 404 maps to None (PRECHECK issue_not_found);
        auth/rate-limit/network failures raise classified (fail-closed)."""
        from tracks.executor.hotfix import HostIssue

        try:
            data = self._get(
                f"{_api_base()}/repos/{self.gh_repo}/issues/{issue_number}"
            )
        except GithubIssuesError as exc:
            if exc.classification == "not_found":
                return None
            raise
        labels = []
        for label in data.get("labels") or []:
            if isinstance(label, dict) and label.get("name"):
                labels.append(str(label["name"]))
        return HostIssue(
            number=issue_number,
            title=data.get("title", ""),
            body=data.get("body", ""),
            labels=tuple(labels),
        )


def select_issue_backend(repo: Path, version: str):
    """Boundary selection (D-05): explicit fake channel -> stand-in. A live
    channel without GITHUB_TOKEN fails closed with the classified
    ``missing_token`` error (AC-FR0270-03) pointing at the ops prerequisites
    — a fake stand-in is only selected deliberately, never by missing secrets."""
    if (
        os.environ.get("TRAC_FAKE_SIMULATE")
        or os.environ.get("TRAC_AGENT_BACKEND", "").strip().lower() == "fake"
    ):
        return FakeIssueBackend(repo, version)
    if not os.environ.get("GITHUB_TOKEN"):
        raise _missing_token_error()
    return GithubBackend(repo, version)


def _require_token() -> str:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise _missing_token_error()
    return token


def _api_json(req: urllib.request.Request) -> tuple[object, str | None, int | None]:
    """Perform a JSON request, returning (data, error, http_status)."""
    try:
        return _facade()._get_any(req), None, None
    except urllib.error.HTTPError as exc:
        cls = _HTTP_ERROR_CLASSES.get(exc.code, "network")
        return None, f"{cls}: HTTP {exc.code}: {exc.reason}", exc.code
    except urllib.error.URLError as exc:
        return None, f"network: {exc.reason}", None
    except GithubIssuesError as exc:  # e.g. unusable CA bundle (IF-TLS-001)
        return None, f"{exc.classification}: {exc}", None
    except (ValueError, UnicodeError) as exc:
        return None, f"malformed: {exc}", None


def _api_request(url: str, method: str, payload: dict | None = None) -> urllib.request.Request:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(url, data=data, method=method, headers=headers)
