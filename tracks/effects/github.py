"""GitHub Issues effect boundary (FR-0200, design D-04~D-07).

Env (`GITHUB_TOKEN`, `TRAC_GITHUB_REPO`, `TRAC_GITHUB_PROJECT`) is read ONLY
at this effects boundary (D-05); kernel `decide()`/`project()` never see it.
The fake channel (`TRAC_AGENT_BACKEND=fake` / `TRAC_FAKE_SIMULATE` / missing
token) uses a deterministic stand-in that never touches the network.

Split granularity (D-04): one Issue per FR and per NFR — title
`[FR-XXXX] 标题`, body = item text + its AC list + the baseline digest.
Issues are requirement-tracking identities, not execution units (D-07).
Live TLS (IF-TLS-001): every HTTPS call point drives urllib with an explicit
verifying certifi context; ``TRAC_GITHUB_CA_BUNDLE`` overrides the bundle.

The implementation is split across cohesive sibling modules
(``github_transport``, ``github_issues``, ``github_ci``, ``github_close``,
``github_release``); this module is the stable public surface and re-exports
every previously available name.
"""

# ruff: noqa: F401  (facade module: every import below is a deliberate re-export)

from __future__ import annotations

import functools
import http
import json
import os
import re
import socket
import sqlite3
import ssl
import time
import urllib
from pathlib import Path
from typing import TYPE_CHECKING

from tracks.effects.github_ci import (
    _ci_observed_payload,
    _readback_run_outcome,
    _select_workflow_run,
    _workflow_job_page,
    _workflow_jobs,
    _workflow_jobs_complete,
    _workflow_matches,
    _workflow_run_error,
    judge_ci_binding,
    readback_ci_run,
)
from tracks.effects.github_close import (
    _create_milestone_verified,
    _ensure_milestone,
    _issue_api_repo,
    _list_milestones,
    _patch_milestone_closed,
    _resolve_milestone_number,
    close_issue,
    close_project_milestone,
    ensure_project_milestone,
)
from tracks.effects.github_issues import (
    _AC_HEAD,
    _ACC_SECTION,
    _ITEM_HEAD,
    _ac_index,
    _item_blocks,
    _mapping_key_matches,
    _reuse_mapping,
    create_issue_verified,
    find_authoritative_mapping,
    find_remote_issue,
    issue_items,
    list_issues,
    persist_issue_mapping,
    readback_issue,
    reject_fake_artifact,
)
from tracks.effects.github_release import (
    _release_payload,
    create_release_api,
    list_release_assets,
    readback_release,
    upload_release_asset,
)
from tracks.effects.github_transport import (
    _HTTP_ERROR_CLASSES,
    _NETWORK_ATTEMPTS,
    _NETWORK_BACKOFF_S,
    _OPS_PREREQ_POINTER,
    _PROXY_SETTING_KEY,
    _PROXY_TYPES,
    FakeIssueBackend,
    GithubBackend,
    GithubIssuesError,
    _api_base,
    _api_json,
    _api_request,
    _certifi_bundle,
    _get_any,
    _is_transient_transport_error,
    _load_proxy_config,
    _missing_token_error,
    _normalized_proxy,
    _proxy_config,
    _proxy_urlopen,
    _proxy_urlopen_once,
    _read_exact,
    _require_token,
    _socks5_address,
    _socks5_connect,
    _Socks5HTTPSConnection,
    _Socks5HTTPSHandler,
    _tls_ssl_context,
    _urlopen_transport_retry,
    _urlopen_with_fallback,
    select_issue_backend,
)
from tracks.frontmatter import split_frontmatter
