"""IF-CLOSURE-001 RED: ``trac check trace --version v0.7`` closure rendering.

Interfaces §1i: the v0.7 trace command emits per-AC candidate-bound records
(``ac / outlet / nodes / baseline_evidence / mutation_evidence /
candidate_digest / full_pass_evidence / status``) plus a top-level
``closure="candidate-bound"`` and overall ``status``. The approved-AC set is a
real fact of the seeded host repo (v0.7 ``acceptance.md``), so under GREEN the
records list is non-empty and the per-record field-set loop actually executes;
missing evidence only degrades per-record status to ``fail`` -- it never
suppresses records. Architecture §1.0.9 and the FR-0264-02 wiring pin the other
side: v0.6-and-earlier versions keep the classic trace JSON untouched and an
unknown version fails closed on the existing failure channel -- no
``closure``/``records`` key may leak into non-v0.7 output (schema isolation,
integration regression ``test_v06_trace_output_version_isolated_no_closure_leak``).

RED note (T-013): today only the classic path exists, so v0.7 output misses
the contract keys (behavior gap) while the classic isolation probes pass as
invariant guards. Missing-key gaps are asserted with explicit contract tokens
(IF-CLOSURE-001 §1i/§2c) so every red is an assertion failure, never a bare
KeyError.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tracks import paths
from tracks.cli.main import _cmd_check_trace

CLASSIC_KEYS = {"status", "hard_errors", "warnings"}
CLOSURE_KEYS = ("closure", "records")
SECTION_1I_FIELDS = (
    "ac",
    "outlet",
    "nodes",
    "baseline_evidence",
    "mutation_evidence",
    "candidate_digest",
    "full_pass_evidence",
    "status",
)

_V07_SCHEMA_TOKEN = (
    "IF-CLOSURE-001 §2c/§1i: trac check trace --version v0.7 --json does not "
    "carry the candidate-bound closure schema"
)

_UNKNOWN_VERSION_TOKEN = (
    "interfaces §1h/§2c: an unknown version must fail closed on the existing "
    "CLI failure channel and must not fall back to v0.7 closure output"
)

# Approved-AC seed (test-plan §2.4 synthetic fixture): the acceptance document
# is the canonical approval artifact; its AC set drives one record per AC.
_ACCEPTANCE_V07 = """---
acc_id: ACC-007
status: draft
sha:
---

# Candidate-bound closure acceptance (v0.7-A)

## FR-0265 executable trace candidate-bound closure

### AC-FR0265-01

- closure link observable

### AC-FR0265-02

- blocking conditions fail closed

### AC-FR0265-03

- same-candidate digest consistency enforced
"""


def _seed_version_dir(repo: Path, version: str) -> Path:
    home = paths.tracks_home(repo)
    vdir = paths.version_dir(home, version)
    vdir.mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    return vdir


def _seed_v07_with_approved_acs(repo: Path) -> Path:
    """Seed the v0.7 version dir with the approved-AC acceptance document."""
    vdir = _seed_version_dir(repo, "v0.7")
    (vdir / "acceptance.md").write_text(_ACCEPTANCE_V07, encoding="utf-8")
    return vdir


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


# §1i TRACKS-TRACE v0.7 --json exposes candidate-bound closure schema
def test_v07_trace_json_carries_candidate_bound_closure(host_repo, capsys):
    repo = host_repo
    _seed_v07_with_approved_acs(repo)
    rc = _cmd_check_trace(repo, ["--json", "--version", "v0.7"])
    assert rc == 0 or rc == 1
    payload = _payload(capsys)
    # §1i top-level schema: status + closure="candidate-bound"
    assert payload["status"] in ("pass", "fail")
    assert CLOSURE_KEYS[0] in payload, f"{_V07_SCHEMA_TOKEN}: top-level closure key absent"
    assert payload["closure"] == "candidate-bound"
    assert CLOSURE_KEYS[1] in payload, (
        f"{_V07_SCHEMA_TOKEN}: top-level records key absent"
    )
    assert isinstance(payload["records"], list)


# §1i TRACKS-TRACE every approved AC yields a record carrying the full field set
def test_v07_trace_json_records_carry_section_i_fields(host_repo, capsys):
    repo = host_repo
    _seed_v07_with_approved_acs(repo)
    rc = _cmd_check_trace(repo, ["--json", "--version", "v0.7"])
    assert rc == 0 or rc == 1
    payload = _payload(capsys)
    required = set(SECTION_1I_FIELDS)
    assert CLOSURE_KEYS[1] in payload, (
        f"{_V07_SCHEMA_TOKEN}: top-level records key absent"
    )
    # Approved ACs are seeded, so records exist regardless of evidence state.
    assert payload["records"], f"{_V07_SCHEMA_TOKEN}: records empty despite approved ACs"
    for record in payload["records"]:
        missing = required - set(record)
        assert not missing, f"record for {record.get('ac')!r} misses §1i fields {missing}"
    recorded_acs = {record["ac"] for record in payload["records"]}
    assert {"AC-FR0265-01", "AC-FR0265-02", "AC-FR0265-03"} <= recorded_acs


# interfaces §1h/§2c guard: unknown versions fail closed without closure keys.
def test_unknown_version_trace_fails_closed_without_closure_leak(host_repo, capsys):
    repo = host_repo
    _seed_version_dir(repo, "v0.6")
    rc = _cmd_check_trace(repo, ["--json", "--version", "v9.9"])
    assert rc != 0, f"{_UNKNOWN_VERSION_TOKEN}: unknown version exited 0"
    out = capsys.readouterr().out
    if out.strip():
        payload = json.loads(out)
        for key in CLOSURE_KEYS:
            assert key not in payload, f"{_UNKNOWN_VERSION_TOKEN}: {key} leaked"


# FR-0264-02 wiring: early versions never leak v0.7 closure keys (guard).
@pytest.mark.parametrize("version", ["v0.4", "v0.6"])
def test_early_version_trace_json_has_no_closure_leak(version, host_repo, capsys):
    repo = host_repo
    _seed_version_dir(repo, version)
    _cmd_check_trace(repo, ["--json", "--version", version])
    payload = _payload(capsys)
    for key in CLOSURE_KEYS:
        assert key not in payload
    assert set(payload) >= CLASSIC_KEYS
