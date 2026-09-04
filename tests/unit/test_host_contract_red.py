"""T-008 RED: host contract unit anchors (IF-HOSTCONTRACT-001).

Declares the red obligations for tracks/executor/host_contract.py
(FR-0269 / FR-0281 / NFR-0147; interfaces §1e):

1. load_host_contract parses the ``[host-contract.*]`` closed-set tables of a
   project.toml into the frozen declaration dataclasses and
   validate_host_contract accepts a well-formed contract (empty error tuple,
   including the two-level operations TARGET references). Structural
   violations fail closed with ValueError: missing
   ``[host-contract].version=1``, unknown host-contract table, unknown gate
   kind, unknown result channel, and an unknown placeholder is reported by
   validate_host_contract as a non-empty error tuple.

2. Gate results normalize to ``tracks-gate-result`` v1: the exit_code channel
   is synthesized by the Runtime (status = exit == 0) with machine evidence
   (exit/stdout/stderr) in the summary; the file channel parses the ``{result}``
   JSON blob; a missing file, illegal JSON, a missing schema/version/status
   field or a wrong schema/version value are malformed (fail-closed status).

3. resolve_placeholders resolves the base closed set ({version} {major}
   {minor} {n} {ulid} {artifact} {prefix} {prefix_bin}) from run version
   facts; execute_gate runs a declared command shlex-split with shell=False
   under the contract timeout, substituting declared placeholders only
   (an unknown placeholder fails closed) and returning the normalized result.
"""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

from tracks.executor.host_contract import (
    GATE_RESULT_PROTOCOL,
    GATE_RESULT_VERSION,
    LocalGateDecl,
    execute_gate,
    load_host_contract,
    parse_gate_result,
    resolve_placeholders,
    validate_host_contract,
)

_VALID_TOML = """\
[host-contract]
version = 1
language = "python"
toolchain = "cpython>=3.10"
install = "tool-pkg install -e ."

[[host-contract.local_gate]]
kind = "quality"
source = "guard_registry"
categories = ["lint_format", "static_analysis"]
result_channel = "exit_code"
timeout_seconds = 60

[[host-contract.local_gate]]
kind = "trace"
command = "tool-a check trace --version {version}"
result_channel = "exit_code"
timeout_seconds = 60

[host-contract.version_scheme]
feature_tag = "v{minor}.0"
patch_line = "v{minor}.{n}"
prerelease_tag = "v{minor}.{n}-pre.{ulid}"

[host-contract.build]
command = "tool-a build -o dist"
artifact = "dist/*.pkg"

[host-contract.smoke]
steps = [
  "tool-a install --prefix {prefix} {artifact}",
  "{prefix_bin}/tool-a --help",
]

[[host-contract.security_scan]]
id = "audit"
tool = "scanner"
tool_version = "1.0"
install = ""
command = "scanner check"
result_channel = "exit_code"
threshold = "findings=0"
timeout_seconds = 60

[host-contract.ci]
repo_env = "CI_REPO"
workflow = "ci.yml"
required_checks = ["verify"]
conclusion = "success"

[host-contract.tracker]
kind = "github"

[host-contract.operations.feature]
steps = ["tag:{feature_tag}", "release:{patch_line}"]
requires = ["verify"]
"""


class _StubStillRed(AssertionError):
    """A contract surface still raising NotImplementedError stays red."""


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _guard(fn, *args):
    try:
        return fn(*args)
    except NotImplementedError as exc:
        raise _StubStillRed(
            f"assertion failure: {fn.__name__} still raises NotImplementedError "
            f"(IF-HOSTCONTRACT-001 stub): {exc}"
        ) from exc


def _write_contract(tmp_path: Path, text: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "project.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _blob(**overrides) -> bytes:
    payload = {
        "schema": GATE_RESULT_PROTOCOL,
        "version": GATE_RESULT_VERSION,
        "status": "passed",
        "exit_code": 0,
        "summary": {"k": "v"},
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def test_load_and_validate_host_contract_schema(tmp_path):
    contract = _guard(load_host_contract, _write_contract(tmp_path, _VALID_TOML))
    header = (
        contract.contract_version,
        contract.language,
        contract.toolchain,
        contract.install,
    )
    checks = [
        ("[host-contract] header not projected", header == (1, "python", "cpython>=3.10", "tool-pkg install -e .")),
        ("local_gate decls not projected", len(contract.local_gates) == 2),
    ]
    quality, trace = contract.local_gates
    checks.extend(
        [
            (
                "quality gate decl wrong",
                (quality.kind, quality.source, quality.categories, quality.result_channel, quality.timeout_seconds)
                == ("quality", "guard_registry", ("lint_format", "static_analysis"), "exit_code", 60),
            ),
            (
                "trace gate command not projected",
                (trace.kind, trace.result_channel, "{version}" in trace.command) == ("trace", "exit_code", True),
            ),
            (
                "version_scheme templates not projected",
                (contract.version.feature_tag, contract.version.patch_line, contract.version.prerelease_tag)
                == ("v{minor}.0", "v{minor}.{n}", "v{minor}.{n}-pre.{ulid}"),
            ),
            (
                "build/smoke not projected",
                (contract.build_command, contract.build_artifact, contract.smoke)
                == ("tool-a build -o dist", "dist/*.pkg", ("tool-a install --prefix {prefix} {artifact}", "{prefix_bin}/tool-a --help")),
            ),
            (
                "security scan decl not projected",
                (len(contract.security_scans), contract.security_scans[0].scan_id, contract.security_scans[0].threshold)
                == (1, "audit", "findings=0"),
            ),
            (
                "ci/tracker binding not projected",
                (contract.ci.get("repo_env"), contract.ci.get("conclusion"), contract.tracker.get("kind"))
                == ("CI_REPO", "success", "github"),
            ),
            (
                "operations.feature not projected",
                contract.operations.get("feature") is not None
                and contract.operations["feature"].steps == ("tag:{feature_tag}", "release:{patch_line}")
                and contract.operations["feature"].requires == ("verify",),
            ),
        ]
    )
    errors = _guard(validate_host_contract, contract, tmp_path)
    checks.append(("a well-formed contract must validate clean", errors == ()))
    # Two-level TARGET refs are legal; an unknown placeholder must be reported.
    bogus = _guard(
        load_host_contract,
        _write_contract(
            tmp_path / "alt",
            _VALID_TOML.replace(
                "tool-a check trace --version {version}",
                "tool-a check {bogus}",
            ),
        ),
    )
    checks.append(
        ("unknown placeholder must surface in validate errors", _guard(validate_host_contract, bogus, tmp_path) != ()),
    )
    mutations = [
        ("missing [host-contract].version=1 must fail closed", _VALID_TOML.replace("version = 1\n", "")),
        ("unknown host-contract table must fail closed", _VALID_TOML + "[host-contract.bogus]\nkey = 1\n"),
        ("unknown gate kind must fail closed", _VALID_TOML.replace('kind = "quality"', 'kind = "lint_extra"')),
        (
            "unknown result channel must fail closed",
            _VALID_TOML.replace('result_channel = "exit_code"', 'result_channel = "socket"', 1),
        ),
    ]
    for label, text in mutations:
        try:
            _guard(load_host_contract, _write_contract(tmp_path, text))
            _fail(label)
        except ValueError:
            pass
    for label, ok in checks:
        if not ok:
            _fail(label)


def test_gate_result_normalized_v1():
    passed = _guard(parse_gate_result, None, "exit_code", 0)
    failed = _guard(parse_gate_result, None, "exit_code", 3)
    parsed = _guard(parse_gate_result, _blob(), "file", None)
    bad_json = _guard(parse_gate_result, b"not json{{", "file", None)
    no_file = _guard(parse_gate_result, None, "file", None)
    missing_schema = _guard(
        parse_gate_result,
        json.dumps({"version": 1, "status": "passed"}).encode("utf-8"),
        "file",
        None,
    )
    missing_status = _guard(
        parse_gate_result,
        json.dumps({"schema": GATE_RESULT_PROTOCOL, "version": 1}).encode("utf-8"),
        "file",
        None,
    )
    wrong_version = _guard(parse_gate_result, _blob(version=2), "file", None)
    wrong_schema = _guard(parse_gate_result, _blob(schema="other-protocol"), "file", None)
    checks = [
        ("exit_code channel: 0 must synthesize passed", (passed.status, passed.exit_code) == ("passed", 0)),
        ("exit_code channel: 3 must synthesize failed", (failed.status, failed.exit_code) == ("failed", 3)),
        ("exit_code summary must carry machine evidence", {"exit", "stdout", "stderr"} <= set(passed.summary)),
        ("file channel: valid v1 blob must pass through", (parsed.status, parsed.summary) == ("passed", {"k": "v"})),
        ("file channel: failed blob must stay failed", _guard(parse_gate_result, _blob(status="failed"), "file", None).status == "failed"),
        ("file channel: illegal JSON must be malformed", bad_json.status == "malformed"),
        ("file channel: missing file must be malformed", no_file.status == "malformed"),
        ("file channel: missing schema must be malformed", missing_schema.status == "malformed"),
        ("file channel: missing status must be malformed", missing_status.status == "malformed"),
        ("file channel: wrong version must be malformed", wrong_version.status == "malformed"),
        ("file channel: wrong schema must be malformed", wrong_schema.status == "malformed"),
    ]
    for label, ok in checks:
        if not ok:
            _fail(label)


def test_placeholders_and_execute_per_contract(tmp_path):
    facts = {"version": "v0.8.1", "major": "0", "minor": "8", "n": "1", "ulid": "ULID1"}
    stage = str(tmp_path / "stage")
    resolved = _guard(resolve_placeholders, facts, "dist/app.pkg", stage)
    checks = [
        ("{version} not resolved", resolved.get("version") == "v0.8.1"),
        (
            "{major}/{minor}/{n}/{ulid} not resolved",
            (resolved.get("major"), resolved.get("minor"), resolved.get("n"), resolved.get("ulid"))
            == ("0", "8", "1", "ULID1"),
        ),
        ("{artifact} not resolved", resolved.get("artifact") == "dist/app.pkg"),
        ("{prefix} not resolved", resolved.get("prefix") == stage),
        ("{prefix_bin} must derive under prefix", str(resolved.get("prefix_bin", "")).startswith(stage)),
    ]
    py = shlex.quote(sys.executable)
    # T008-RED-FIND-1 re-pin: @TARGET@ carries only the quoted element; the
    # template's surrounding brackets form the single-layer list literal
    # (nested brackets produced [["v0.8.1"]], unsatisfiable under argv[1:]).
    tpl = py + " -c 'import sys; sys.exit(0 if sys.argv[1:] == [@TARGET@] else 1)' {version}"
    decl = LocalGateDecl(
        kind="trace",
        source="command",
        command=tpl.replace("@TARGET@", '"v0.8.1"'),
        categories=(),
        result_channel="exit_code",
        timeout_seconds=30,
    )
    result = _guard(execute_gate, decl, tmp_path, resolved)
    checks.append(("declared command honoring {version} must pass", (result.status, result.exit_code) == ("passed", 0)))
    fail_decl = replace(decl, command=tpl.replace("@TARGET@", '"vX"'))
    result = _guard(execute_gate, fail_decl, tmp_path, resolved)
    checks.append(
        (
            "failing declared command must synthesize failed with evidence",
            result.status == "failed" and result.exit_code == 1 and {"exit", "stdout", "stderr"} <= set(result.summary),
        ),
    )
    bogus_decl = replace(decl, command=py + " -c 'import sys' {bogus}")
    try:
        _guard(execute_gate, bogus_decl, tmp_path, resolved)
        _fail("unknown placeholder must fail closed")
    except ValueError:
        pass
    slow_decl = replace(
        decl,
        command=py + " -c 'import time; time.sleep(3)'",
        timeout_seconds=1,
    )
    result = _guard(execute_gate, slow_decl, tmp_path, resolved)
    checks.append(("contract timeout must fail closed", result.status == "failed"))
    for label, ok in checks:
        if not ok:
            _fail(label)
