"""IF-RUNCONTRACT-001 contract-loader slice (D-41 Slice A RED; v6 result channel).

Pins the project.toml execution-contract surface the atomic implementation
slice activates (interfaces.md §1m): flat per-layer ``run_selected`` members
with the ``{nodes}`` placeholder, a new ``[unit]`` section, and fail-closed
``ContractError`` when a layer's ``run_selected`` is missing -- Runtime must
never fall back to appending nodeids to ``run``.

v6 correction (machine-readable per-node result): every ``run`` AND
``run_selected`` template must contain the ``{result}`` placeholder EXACTLY
ONCE (Runtime-provided unique writable JUnit XML path), and ``run_selected``
must contain ``{nodes}`` exactly once. Missing or duplicated placeholders are
contract_error fail-closed at load time -- Runtime substitutes only declared
``{nodes}``/``{result}`` plus cwd/argv0 and never injects ``--junitxml`` or
concurrency flags itself.
"""

from __future__ import annotations

import pytest

from tracks.project import ContractError, load_contract


def _write_contract(repo, body):
    toml = repo / ".tracks" / "projects" / "project.toml"
    toml.parent.mkdir(parents=True, exist_ok=True)
    toml.write_text(body, encoding="utf-8")
    return toml


_MINIMAL_UNIT_SECTION = (
    "[unit]\n"
    'framework = "pytest"\n'
    'paths = ["tests/unit/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
    'run = ".venv/bin/python -m pytest tests/unit/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)

_INTEGRATION_SECTION = (
    "[integration]\n"
    'framework = "pytest"\n'
    'paths = ["tests/integration/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
    'run = ".venv/bin/python -m pytest tests/integration/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)


# AC-FR0255-01: every layer declares run/run_selected; worker/dist flags and
# the --junitxml={result} flag are embedded in the command strings by Archer
# alone (no separate keys, no Runtime-injected junit flags).
def test_sections_expose_run_selected_contract(tmp_path):
    # Mechanical fixture update (D-41 atomic schema slice): the loader
    # requires ALL THREE layers + [nightly], so the body below is the full
    # atomic schema; the assertions target the [integration]/[unit] surface.
    _write_contract(tmp_path, _full_atomic_schema_body())
    contract = load_contract(tmp_path)
    assert getattr(contract.integration, "run_selected", None) == (
        ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope "
        "--junitxml={result}"
    )
    unit = getattr(contract, "unit", None)
    assert unit is not None, "project contract does not expose the [unit] section"
    assert "{nodes}" in unit.run_selected
    # v6: the machine-readable result placeholder is part of every layer's
    # run AND run_selected template, exactly once each.
    for section in (unit, contract.integration):
        assert section.run.count("{result}") == 1, (
            "run must embed {result} exactly once "
            "(Runtime-provided JUnit XML path; no Runtime-injected --junitxml)"
        )
        assert section.run_selected.count("{result}") == 1, (
            "run_selected must embed {result} exactly once"
        )
        assert section.run_selected.count("{nodes}") == 1


# v6/interfaces §1m: a layer missing the {result} placeholder in run OR
# run_selected is contract_error fail-closed at load time -- a template without
# it would force Runtime to parse ambiguous pytest stdout text or inject
# --junitxml itself, both of which are defect behavior.
def _strip_result_placeholder(section_body: str, key: str) -> str:
    """Remove ' --junitxml={result}' from exactly the named key's template."""
    lines = []
    prefix = f"{key} = "
    for line in section_body.splitlines(keepends=True):
        if line.startswith(prefix):
            line = line.replace(" --junitxml={result}", "", 1)
        lines.append(line)
    return "".join(lines)


@pytest.mark.parametrize("section", ["integration", "unit"])
@pytest.mark.parametrize("key", ["run", "run_selected"])
def test_missing_result_placeholder_fails_closed_contract_error(tmp_path, section, key):
    bodies = {
        "integration": (_INTEGRATION_SECTION, _MINIMAL_UNIT_SECTION),
        "unit": (_MINIMAL_UNIT_SECTION, _INTEGRATION_SECTION),
    }
    target, other = bodies[section]
    _write_contract(tmp_path, _strip_result_placeholder(target, key) + other)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert "{result}" in excinfo.value.reason
    assert key in excinfo.value.reason


# v6/interfaces §1m: a duplicated placeholder is equally malformed -- exactly
# once means exactly once; duplication would make substitution ambiguous.
def _duplicate_placeholder(section_body: str, key: str, placeholder: str) -> str:
    """Duplicate ``placeholder`` inside exactly the named key's TOML string value."""
    lines = []
    prefix = f"{key} = "
    mutated = False
    for line in section_body.splitlines(keepends=True):
        if line.startswith(prefix):
            head, _, tail = line.partition('"')
            value, sep, rest = tail.rpartition('"')
            assert sep, f"{key} template is not a quoted TOML string: {line!r}"
            assert placeholder in value, (
                f"{placeholder} not found inside {key}'s string value"
            )
            line = (
                head
                + '"'
                + value.replace(placeholder, placeholder + " " + placeholder, 1)
                + sep
                + rest
            )
            mutated = True
        lines.append(line)
    assert mutated, f"no TOML string value line found for key {key!r}"
    return "".join(lines)


@pytest.mark.parametrize(
    ("template_field", "placeholder"),
    [
        ("run", "{result}"),
        ("run_selected", "{result}"),
        ("run_selected", "{nodes}"),
    ],
)
def test_duplicated_placeholder_fails_closed_contract_error(tmp_path, template_field, placeholder):
    pristine_count = (_MINIMAL_UNIT_SECTION + _INTEGRATION_SECTION).count(placeholder)
    body = (
        _duplicate_placeholder(_MINIMAL_UNIT_SECTION, template_field, placeholder)
        + _INTEGRATION_SECTION
    )
    # Guard against a silent no-op mutation: exactly one extra occurrence of
    # the placeholder must exist in the mutated body before load is attempted,
    # otherwise a missing ContractError would not prove anything.
    assert body.count(placeholder) == pristine_count + 1, (
        f"mutation failed to duplicate {placeholder} in [{template_field}]"
    )
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert placeholder in excinfo.value.reason


# AC-FR0255-01/interfaces §1m: a layer missing run_selected is contract_error
# fail-closed at load time -- no vacuous None that a caller could fall back
# through by appending nodeids to run.
@pytest.mark.parametrize("section", ["integration", "unit"])
def test_missing_run_selected_fails_closed_contract_error(tmp_path, section):
    no_sel_integration = (
        "[integration]\n"
        'framework = "pytest"\n'
        'paths = ["tests/integration/"]\n'
        'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
        'run = ".venv/bin/python -m pytest tests/integration/ -q --junitxml={result}"\n'
        'cwd = "."\n\n'
    )
    bodies = {
        "integration": no_sel_integration + _MINIMAL_UNIT_SECTION,
        "unit": no_sel_integration
        + _MINIMAL_UNIT_SECTION.replace(
            'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
            '--junitxml={result}"\n',
            "",
        ),
    }
    _write_contract(tmp_path, bodies[section])
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert "run_selected" in excinfo.value.reason


# -- D-41 atomic schema slice (interfaces §1m/§1n): once the schema slice is
# active in the loader, ALL THREE flat layer sections ([unit]/[integration]/
# [e2e]) are required -- an undeclared layer is a contract defect, not an
# optional convenience (the FULL chain and nightly CI run all three). The
# [nightly] section is likewise required and validated at load time:
# schedule/workflow/job/purpose must be non-empty strings (no str(int)
# coercion), and `layers` must declare exactly unit+integration+e2e ONCE EACH
# (order canonicalizes; missing/duplicate layers fail closed).

_E2E_SECTION = (
    "[e2e]\n"
    'framework = "pytest"\n'
    'paths = ["tests/e2e/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
    'run = ".venv/bin/python -m pytest tests/e2e/ -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} -q -n 8 --dist loadscope '
    '--junitxml={result}"\n'
    'cwd = "."\n\n'
)

_NIGHTLY_SECTION_BODY = (
    "[nightly]\n"
    'schedule = "0 3 * * *"\n'
    'workflow = ".github/workflows/nightly.yml"\n'
    'job = "nightly-regression"\n'
    'layers = ["unit", "integration", "e2e"]\n'
    'purpose = "scheduled FULL-suite regression"\n\n'
)


def _full_atomic_schema_body():
    return (
        _INTEGRATION_SECTION
        + _MINIMAL_UNIT_SECTION
        + _E2E_SECTION
        + _NIGHTLY_SECTION_BODY
    )


def test_full_atomic_schema_loads_with_typed_nightly_section(tmp_path):
    body = _full_atomic_schema_body()
    _write_contract(tmp_path, body)
    contract = load_contract(tmp_path)
    for section_name in ("unit", "integration", "e2e"):
        assert getattr(contract, section_name) is not None, (
            f"the atomic schema declares [{section_name}] as a required layer"
        )
        assert "{nodes}" in getattr(contract, section_name).run_selected
    nightly = getattr(contract, "nightly", None)
    assert nightly is not None, "the atomic schema requires the [nightly] section"
    assert nightly.schedule == "0 3 * * *"
    assert nightly.job == "nightly-regression"
    assert tuple(nightly.layers) == ("unit", "integration", "e2e")


@pytest.mark.parametrize("section", ["unit", "integration", "e2e"])
def test_missing_layer_section_fails_closed_contract_error(tmp_path, section):
    sections = {
        "integration": _INTEGRATION_SECTION,
        "unit": _MINIMAL_UNIT_SECTION,
        "e2e": _E2E_SECTION,
    }
    body = "".join(body for name, body in sections.items() if name != section)
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert f"[{section}]" in excinfo.value.reason, (
        f"a missing [{section}] layer must be a named contract error "
        f"(got: {excinfo.value.reason})"
    )


def test_missing_nightly_section_fails_closed_contract_error(tmp_path):
    body = _INTEGRATION_SECTION + _MINIMAL_UNIT_SECTION + _E2E_SECTION
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert "[nightly]" in excinfo.value.reason


@pytest.mark.parametrize("key", ["schedule", "workflow", "job", "layers", "purpose"])
def test_nightly_missing_required_key_fails_closed(tmp_path, key):
    lines = [
        line
        for line in _NIGHTLY_SECTION_BODY.splitlines(keepends=True)
        if not line.startswith(f"{key} = ")
    ]
    stripped = "".join(lines)
    assert f"{key} =" not in stripped, "mutation failed to drop the key"
    body = _INTEGRATION_SECTION + _MINIMAL_UNIT_SECTION + _E2E_SECTION + stripped
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert key in excinfo.value.reason, (
        f"a [nightly] section missing '{key}' must be a named contract error "
        f"(got: {excinfo.value.reason})"
    )


@pytest.mark.parametrize(
    ("layers_literal", "why"),
    [
        ('layers = ["unit", "gpu-suite"]\n', "unknown-layer"),
        ("layers = []\n", "empty-layers"),
        ('layers = "unit"\n', "not-a-list"),
        ('layers = ["unit", 1]\n', "non-string-entry-no-coercion"),
        ('layers = ["unit", "unit", "integration"]\n', "duplicate-layer"),
        ('layers = ["unit", "integration"]\n', "missing-layer"),
    ],
)
def test_nightly_layers_validated_against_declared_layers(tmp_path, layers_literal, why):
    nightly = "\n".join(
        line if not line.startswith("layers = ") else layers_literal
        for line in _NIGHTLY_SECTION_BODY.splitlines(keepends=True)
    )
    body = _INTEGRATION_SECTION + _MINIMAL_UNIT_SECTION + _E2E_SECTION + nightly
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert "layers" in excinfo.value.reason, (
        f"[nightly].layers validation ({why}) must fail closed as a contract "
        f"error naming the key (got: {excinfo.value.reason})"
    )


# [nightly].layers may declare the three layers in any order; the loader
# canonicalizes to the declared test-layer order (unit, integration, e2e).
def test_nightly_layers_order_canonicalizes(tmp_path):
    nightly = "\n".join(
        line
        if not line.startswith("layers = ")
        else 'layers = ["e2e", "integration", "unit"]\n'
        for line in _NIGHTLY_SECTION_BODY.splitlines(keepends=True)
    )
    body = _INTEGRATION_SECTION + _MINIMAL_UNIT_SECTION + _E2E_SECTION + nightly
    _write_contract(tmp_path, body)
    contract = load_contract(tmp_path)
    assert tuple(contract.nightly.layers) == ("unit", "integration", "e2e")


# schedule/workflow/job/purpose must be non-empty STRINGS: numeric or
# whitespace-only values are contract_error (never str()-coerced).
@pytest.mark.parametrize(
    ("key", "bad_value"),
    [
        ("schedule", "7"),
        ("schedule", '""'),
        ("workflow", "42"),
        ("job", "0"),
        ("purpose", '""'),
        ("purpose", '"   "'),
    ],
)
def test_nightly_string_keys_reject_non_string_and_empty_values(tmp_path, key, bad_value):
    nightly = "\n".join(
        line if not line.startswith(f"{key} = ") else f"{key} = {bad_value}\n"
        for line in _NIGHTLY_SECTION_BODY.splitlines(keepends=True)
    )
    assert f"{key} = {bad_value}\n" in nightly, "mutation failed to replace the value"
    body = _INTEGRATION_SECTION + _MINIMAL_UNIT_SECTION + _E2E_SECTION + nightly
    _write_contract(tmp_path, body)
    with pytest.raises(ContractError) as excinfo:
        load_contract(tmp_path)
    assert key in excinfo.value.reason, (
        f"[nightly].{key}={bad_value} must be a named contract error "
        f"(got: {excinfo.value.reason})"
    )
