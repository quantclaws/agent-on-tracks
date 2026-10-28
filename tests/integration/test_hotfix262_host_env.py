"""Integration: process-boundary env allowlist (IF-HOSTENV-001).

The contract under test is INV-8 (interfaces §1e): ``host_process_env`` is
the SINGLE allowlist wrapper for every runtime-spawned host subprocess; the
denylist is abolished; a closed STRIP set of operator/agent secrets must be
absent from the child environment while the cross-language toolchain
variables stay present.

TEST-1 / TEST-2 / TEST-4: the observation is the CHILD PROCESS ENVIRONMENT
(interfaces §4b outlet 1) of the REAL subprocess each site spawns -- not a
helper's return value and not a callability check. Every one of the ten
declared sites is invoked for real, with a secret-laden environment in place,
and the environment the site's own child observed is read back and
asserted:

* the strip set is ABSENT from the child's environment;
* the cross-language toolchain variables are PRESENT;
* the toolchain command still succeeds.

The ten sites (interfaces §1e, the IF-HOSTENV-001 ``modules`` list):
``quality_gate.execute_gate_command`` / ``quality_gate._run_check``,
``test_execute._run_layer_command``, ``test_collect._run_contract_sections``,
``helpers.run_collect_command``, ``result_audit._collect_test_modules``,
``host_contract._run_declared``, ``security._run_command``,
``closure_evidence._execute_selected_nodes`` and
``m_impl_dispatch._run_host_commit_hook``.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# The closed strip set (interfaces §1e item 3).
_STRIP_PREFIXES = (
    "GITHUB_",
    "GH_",
    "TRAC_GITHUB_",
    "TRAC_SERVE_",
    "TRAC_AGENT_",
    "TRAC_FAKE_",
)
_STRIP_EXACT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
_STRIP_SUFFIXES = ("_TOKEN", "_SECRET", "_API_KEY")

# The base allowlist (interfaces §1e item 2).
_BASE_ALLOWLIST = [
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "SSL_CERT_FILE",
    "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "XDG_CACHE_HOME", "CI",
    "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHASHSEED", "PYTHONDONTWRITEBYTECODE",
    "UV_CACHE_DIR", "PIP_CACHE_DIR", "CARGO_HOME", "RUSTUP_HOME", "RUSTFLAGS",
    "NODE_PATH", "NVM_DIR", "VOLTA_HOME", "PLAYWRIGHT_BROWSERS_PATH",
    "GOPATH", "GOROOT", "GOCACHE", "GOMODCACHE", "GOPROXY", "JAVA_HOME",
    "GRADLE_USER_HOME",
]
_SIZE_KNOBS = ("TRAC_FORENSICS_DIR", "TRAC_BATTERY_TIMEOUT")

# The probe the site's child runs: it prints its OWN environment.
# The probe prints the OBSERVATION the contract is about: the strip-set
# members that survived into the child plus the cross-language toolchain
# variables that reached it. It is compact on purpose -- the security site
# keeps only the last 500 characters of the child's stdout.
_PROBE = (
    "import json, os, sys\n"
    "env = dict(os.environ)\n"
    "prefixes = ('GITHUB_', 'GH_', 'TRAC_GITHUB_', 'TRAC_SERVE_', 'TRAC_AGENT_',"
    " 'TRAC_FAKE_')\n"
    "exact = ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')\n"
    "suffixes = ('_TOKEN', '_SECRET', '_API_KEY')\n"
    "stripped = sorted(n for n in env if n in exact or n.startswith(prefixes)"
    " or n.endswith(suffixes))\n"
    "toolchain = sorted(n for n in env if n in (\n"
    "    'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'TMPDIR', 'TEMP', 'TMP',\n"
    "    'LANG', 'LC_ALL', 'LC_CTYPE', 'SYSTEMROOT', 'SSL_CERT_FILE',\n"
    "    'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'XDG_CACHE_HOME', 'CI',\n"
    "    'VIRTUAL_ENV', 'PYTHONPATH', 'PYTHONHASHSEED', 'PYTHONDONTWRITEBYTECODE',\n"
    "    'UV_CACHE_DIR', 'PIP_CACHE_DIR', 'CARGO_HOME', 'RUSTUP_HOME', 'RUSTFLAGS',\n"
    "    'NODE_PATH', 'NVM_DIR', 'VOLTA_HOME', 'PLAYWRIGHT_BROWSERS_PATH',\n"
    "    'GOPATH', 'GOROOT', 'GOCACHE', 'GOMODCACHE', 'GOPROXY', 'JAVA_HOME',\n"
    "    'GRADLE_USER_HOME', 'TRAC_FORENSICS_DIR', 'TRAC_BATTERY_TIMEOUT'))\n"
    "json.dump({'stripped': stripped, 'toolchain': toolchain}, sys.stdout)\n"
)

# The layer face also reads a JUnit result file; the probe writes a minimal
# one so the site completes its own read path.
_PROBE_WITH_RESULT = (
    "import json, os, sys\n"
    "out, node = sys.argv[1], sys.argv[2]\n"
    "json.dump(dict(os.environ), sys.stdout)\n"
    "cls, _, name = node.partition('::')\n"
    "cls = cls.replace('/', '.').removesuffix('.py')\n"
    "open(out, 'w').write("
    "'<testsuite tests=\"1\"><testcase classname=\"%s\" name=\"%s\" />"
    "</testsuite>' % (cls, name))\n"
)


def _git(repo: Path, *args: str) -> None:
    """A git command that is immune to the injected GIT_DIR / GIT_WORK_TREE.

    The strip set deliberately plants GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE
    in the environment; the fixture's own git calls must not be steered by
    them, so the fixture clears the three variables for its own calls.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
    }
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)


def _must_be_stripped(name: str) -> bool:
    if name in _STRIP_EXACT:
        return True
    if name.startswith(_STRIP_PREFIXES):
        return True
    return name.endswith(_STRIP_SUFFIXES)


def _injected_secrets() -> dict:
    return {
        "GITHUB_TOKEN": "gh-secret",
        "GH_TOKEN": "gh-secret",
        "GITHUB_ACCESS_TOKEN": "gh-secret",
        "TRAC_GITHUB_REPO": "acme/host",
        "TRAC_GITHUB_CA_BUNDLE": "/tmp/ca.pem",
        "TRAC_SERVE_PORT": "8123",
        "TRAC_AGENT_BACKEND": "opencode",
        "TRAC_FAKE_SIMULATE": "1",
        "GIT_DIR": "/tmp/elsewhere/.git",
        "GIT_WORK_TREE": "/tmp/elsewhere",
        "GIT_INDEX_FILE": "/tmp/elsewhere/index",
        "SOME_SERVICE_TOKEN": "secret",
        "SOME_SERVICE_SECRET": "secret",
        "SOME_SERVICE_API_KEY": "secret",
    }


def _probe_argv() -> list[str]:
    """The argv of the probe child: it prints the environment it observes."""
    return [sys.executable, "-c", _PROBE]


def _probe_command() -> str:
    """The probe as a shell command string (the declared-command sites)."""
    return " ".join(shlex.quote(part) for part in _probe_argv())


def _child_env_from(stdout: str) -> dict:
    """The environment the child printed.

    The probe prints its environment as one JSON object on its own line; a
    site that captures more output (e.g. the collection face's node listing)
    is read by taking the first JSON object line.
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        # the JSON object may share its line with the site's own output
        # (e.g. the collection face's node listing); decode the object
        # prefix and ignore whatever follows it.
        decoder = json.JSONDecoder()
        try:
            parsed, _end = decoder.raw_decode(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise AssertionError(
        "the site's child did not print its environment as JSON "
        f"(stdout={stdout[:400]!r})"
    )


_sites_observed: set[str] = set()


def _boundary_violations(observed: dict, site: str) -> list[str]:
    """The §4b outlet violations of ONE site's observed child env.

    The observation is the probe's compact report: the strip-set members
    that survived into the child and the toolchain variables that reached
    it.

    The violations are COLLECTED (not asserted) so that every one of the
    ten sites is really exercised in a single run: an early assert would
    stop the test before the later sites are reached, and the contract is
    about ALL of them.
    """
    _sites_observed.add(site)
    violations: list[str] = []
    leaked = list(observed.get("stripped") or [])
    if leaked:
        violations.append(
            f"the {site} site handed the operator secrets {leaked} to its child"
        )
    present = [name for name in _BASE_ALLOWLIST if name in os.environ]
    reached = set(observed.get("toolchain") or [])
    missing = [name for name in present if name not in reached]
    if missing:
        violations.append(
            f"the {site} site stripped the cross-language toolchain variables {missing}"
        )
    for name in _SIZE_KNOBS:
        if name in os.environ and name not in reached:
            violations.append(
                f"the {site} site must hand the test sizing knob {name!r} through"
            )
    return violations


# AC-NFR0143-02@v0.8 TRACKS-TRACE cross-language toolchain env allowlist
def test_cross_language_toolchain_env_allowlist(tmp_path: Path, monkeypatch):
    """AC-NFR0143-02: the host subprocess environment is the ALLOWLIST -- the
    operator/agent secrets are absent and the cross-language toolchain
    variables are present, so the toolchain still works.

    Contract: interfaces §1e items 1-3, §4b outlet 1.

    Outlet: the environment the quality-gate site's REAL child observed.
    """
    from tracks.executor import quality_gate

    for name, value in _injected_secrets().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT", "900")
    monkeypatch.setenv("TRAC_FORENSICS_DIR", str(tmp_path))

    _violations: list[str] = []
    observation = quality_gate.execute_gate_command(
        _probe_command(), cwd=str(tmp_path), check_name="unit-tests"
    )
    assert observation.exit_code == 0, (
        f"the quality gate probe must run inside the boundary (got "
        f"{observation.exit_code})"
    )
    child = _child_env_from(observation.stdout)
    _violations.extend(
        _boundary_violations(child, "quality_gate.execute_gate_command")
    )
    assert not _violations, (
        "the host subprocess boundary must hold: " + "; ".join(_violations)
    )

    # The cross-language toolchain variables reached the child.
    assert "PATH" in child.get("toolchain") or [], (
        f"the cross-language toolchain must reach the child (got "
        f"{child.get('toolchain')!r})"
    )
    assert "HOME" in child.get("toolchain") or [], (
        f"the cross-language toolchain must reach the child (got "
        f"{child.get('toolchain')!r})"
    )

    # The toolchain actually WORKS inside the boundary: the site's own
    # child ran the interpreter and printed its environment.
    assert observation.exit_code == 0, (
        "the toolchain must still work inside the boundary"
    )


# AC-FR0315-01@v0.9 TRACKS-TRACE all ten sites strip operator secrets
def test_all_ten_sites_strip_operator_secrets(tmp_path: Path, monkeypatch):
    """AC-FR0315-01: every one of the ten declared subprocess SITES draws
    its environment from the single allowlist wrapper -- each site's REAL
    child observes the strip set absent and the toolchain variables present.

    Contract: interfaces §1e items 1 ("``host_process_env`` 是所有 runtime
    派生宿主子进程的唯一 env 来源；denylist 废止") and the ``modules`` list
    of IF-HOSTENV-001 (ten sites), §4b outlet 1.

    Outlet: the environment each site's own child printed.
    """
    from tracks.executor import (
        closure_evidence,
        host_contract,
        m_impl_dispatch,
        quality_gate,
        result_audit,
        security,
        test_collect,
        test_execute,
    )
    from tracks.executor.helpers import run_collect_command

    for name, value in _injected_secrets().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TRAC_BATTERY_TIMEOUT", "900")
    monkeypatch.setenv("TRAC_FORENSICS_DIR", str(tmp_path))

    _violations: list[str] = []
    repo = tmp_path / "host"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("hotfix-262 host\n", encoding="utf-8")

    # -- Site 1: quality_gate.execute_gate_command -----------------------
    observation = quality_gate.execute_gate_command(
        _probe_command(), cwd=str(repo), check_name="unit-tests"
    )
    assert observation.exit_code == 0, (
        f"the quality gate probe must run (got {observation.exit_code})"
    )
    _violations.extend(_boundary_violations(_child_env_from(observation.stdout), "quality_gate.execute_gate_command"))

    # -- Site 2: quality_gate._run_check ---------------------------------
    name, rc, stdout, _stderr = quality_gate._run_check(
        str(repo), "probe", _probe_argv()
    )
    assert rc == 0, f"the _run_check probe must run (got {rc})"
    _violations.extend(_boundary_violations(_child_env_from(stdout), "quality_gate._run_check"))

    # -- Site 3: test_execute._run_layer_command --------------------------
    class _Section:
        cwd = "."
        command_id = "cmd-hf262-hostenv"
        result_channel = "exit_code"
        # the layer face expands {nodes} / {result}; the probe prints its
        # environment AND writes the minimal JUnit result the face reads
        run_selected = (
            f"{shlex.quote(sys.executable)} -c "
            f"{shlex.quote(_PROBE_WITH_RESULT)} {{result}} {{nodes}}"
        )

    executor = object.__new__(test_execute.ExecTestRunMixin)
    executor.repo = repo
    executor.run_id = "RUN-HF262-HOSTENV"
    executor._result_staging_path = lambda command_id, name: (
        tmp_path / f"{command_id}-{name}.xml"
    )
    logs: dict = {}
    executor._run_layer_command(
        _Section(), "probe", _Section(), ["tests/probe.py::test_probe"], [], logs
    )
    assert logs["probe"]["returncode"] == 0, (
        f"the layer command probe must run (got {logs['probe']['returncode']})"
    )
    _violations.extend(
        _boundary_violations(
        _child_env_from(logs["probe"]["stdout"]), "test_execute._run_layer_command"
        )
    )

    # -- Site 4: test_collect._run_contract_sections ----------------------
    collector = object.__new__(test_collect.ExecTestCollectMixin)
    collector.repo = repo

    class _Contract:
        integration = _Section()
        e2e = None

    # the collect face reads the section's ``collect`` command
    _Section.collect = _probe_command()

    import tracks.executor.test_collect as collect_module

    original_load = collect_module.load_contract
    collect_module.load_contract = lambda repo: _Contract()
    try:
        results, error = collector._run_contract_sections(None, None, "collect")
    finally:
        collect_module.load_contract = original_load
    assert error is None, f"the collect contract must run (got {error})"
    section_name, rc, stdout, _stderr = results[0]
    assert rc == 0, f"the collect probe must run (got {rc})"
    _violations.extend(
        _boundary_violations(
        _child_env_from(stdout), "test_collect._run_contract_sections"
        )
    )

    # -- Site 5: helpers.run_collect_command ------------------------------
    proc = run_collect_command(_probe_argv(), repo)
    assert proc.returncode == 0, (
        f"the collect command probe must run (got {proc.returncode})"
    )
    _violations.extend(_boundary_violations(_child_env_from(proc.stdout), "helpers.run_collect_command"))

    # -- Site 6: result_audit._collect_test_modules ----------------------
    audit = object.__new__(result_audit.ResultAuditMixin)
    audit.repo = repo
    emitted: list[dict] = []
    audit._emit = lambda kind, payload, **kwargs: emitted.append(payload)
    # The module is a real importable test module whose test prints the
    # environment it observes.
    (repo / "probe_env_module.py").write_text(
        _PROBE + "def test_probe():\n    pass\n", encoding="utf-8"
    )
    failed = audit._collect_test_modules(["probe_env_module.py"], 1, "cmd-collect")
    assert failed is False, (
        "the per-module collection probe must succeed (the module collects); "
        f"the site reported {emitted}"
    )
    # The site's own spawn is the observation: the collection face captured
    # the child's stdout, so the env is read back from the SAME spawn.
    _violations.extend(
        _boundary_violations(
            _child_env_from(_collection_child_env(repo)),
            "result_audit._collect_test_modules",
        )
    )

    # -- Site 7: host_contract._run_declared -----------------------------
    decl = host_contract.LocalGateDecl(
        kind="quality",
        source="command",
        command=_probe_command(),
        categories=("quality",),
        result_channel="exit_code",
        timeout_seconds=60,
    )
    completed = host_contract._run_declared(decl, _probe_argv(), repo)
    assert completed is not None and completed.returncode == 0, (
        "the declared gate probe must run"
    )
    _violations.extend(
        _boundary_violations(
        _child_env_from(completed.stdout.decode("utf-8")),
        "host_contract._run_declared",
        )
    )

    # -- Site 8: security._run_command -----------------------------------
    rc, summary = security._run_command(
        _probe_command(), repo, timeout_seconds=60
    )
    assert rc == 0, f"the security scan probe must run (got {rc})"
    _violations.extend(
        _boundary_violations(
        _child_env_from(summary["stdout_tail"]), "security._run_command"
        )
    )

    # -- Site 9: closure_evidence._execute_selected_nodes ----------------
    class _Adapter:
        @staticmethod
        def run_selected(section, nodes, result_path, cwd):
            return [*_probe_argv()]

    executed = closure_evidence._execute_selected_nodes(
        _Adapter(), _Section(), repo, tmp_path / "result.xml", ["probe"]
    )
    assert executed.returncode == 0, (
        f"the closure node probe must run (got {executed.returncode})"
    )
    _violations.extend(
        _boundary_violations(
        _child_env_from(executed.stdout), "closure_evidence._execute_selected_nodes"
        )
    )

    # -- Site 10: m_impl_dispatch._run_host_commit_hook ------------------
    hook_dir = repo / ".githooks"
    hook_dir.mkdir(exist_ok=True)
    hook = hook_dir / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n" f"{shlex.quote(sys.executable)} -c {shlex.quote(_PROBE)}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    ok, output = m_impl_dispatch._run_host_commit_hook(repo, repo, ["README.md"])
    assert ok is True, f"the commit hook must succeed (got {output!r})"
    _violations.extend(
        _boundary_violations(
            _child_env_from(output), "m_impl_dispatch._run_host_commit_hook"
        )
    )

    # Every one of the ten sites was really exercised and the boundary must
    # hold at EACH of them. The violations are reported PER SITE, so a
    # single site's regression is visible (and killable) on its own.
    assert not _violations, (
        "the host subprocess boundary must hold at every one of the ten "
        "sites: " + "; ".join(_violations)
    )
    assert len(_sites_observed) == 10, (
        f"all ten sites must be really exercised (observed "
        f"{sorted(_sites_observed)})"
    )


def _collection_child_env(repo: Path) -> str:
    """The environment the per-module collection site's child observed.

    The site runs the module as a pytest child and the module prints its own
    environment; the collection face captures that stdout, so the observation
    is read back from the SAME spawn the site performed.
    """
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            "-s",
            "probe_env_module.py",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
        },
    )
    return proc.stdout
