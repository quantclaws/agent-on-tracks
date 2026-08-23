"""project.toml contract parsing + layout gate (FR-0120).

Direct tests for ``validate_layout`` (the M-DESIGN EXIT layout gate added in
8f7cf04) and for the Fake Archer's ``_write_project_contract`` producing a
layout-valid contract — the regression that previously stalled every Fake
M-DESIGN journey at a ``check=layout`` escalation.
"""

from tracks import paths
from tracks.effects.fake import FakeBackend
from tracks.project import load_contract, validate_layout


def _contract_path(repo):
    home = paths.tracks_home(repo)
    home.mkdir(parents=True, exist_ok=True)
    return paths.project_toml_path(home)


def _write_contract(repo, text):
    path = _contract_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


_BASE = (
    "[integration]\n"
    'framework = "pytest"\n'
    'paths = ["tests/integration/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/integration/"\n'
    'run = ".venv/bin/python -m pytest tests/integration/ --tb=short -q --junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"\n'
    'cwd = "."\n\n'
    "[unit]\n"
    'framework = "pytest"\n'
    'paths = ["tests/unit/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/unit/"\n'
    'run = ".venv/bin/python -m pytest tests/unit/ --tb=short -q --junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"\n'
    'cwd = "."\n\n'
    "[e2e]\n"
    'framework = "pytest"\n'
    'paths = ["tests/e2e/"]\n'
    'collect = ".venv/bin/python -m pytest --collect-only -q tests/e2e/"\n'
    'run = ".venv/bin/python -m pytest tests/e2e/ --tb=short -q --junitxml={result}"\n'
    'run_selected = ".venv/bin/python -m pytest {nodes} --tb=short -q --junitxml={result}"\n'
    'cwd = "."\n\n'
    "[nightly]\n"
    'schedule = "0 3 * * *"\n'
    'workflow = ".github/workflows/nightly.yml"\n'
    'job = "nightly-regression"\n'
    'layers = ["unit", "integration", "e2e"]\n'
    'purpose = "scheduled FULL-suite regression"\n'
)


def test_validate_layout_valid(tmp_path):
    _write_contract(
        tmp_path,
        _BASE + "\n[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        'writable = ["tests/integration/"]\n',
    )
    assert validate_layout(tmp_path) is None


def test_validate_layout_missing_contract(tmp_path):
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "unreadable" in reason


def test_validate_layout_malformed_contract(tmp_path):
    _write_contract(tmp_path, "[integration\nthis is not toml")
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "unreadable" in reason


def test_validate_layout_missing_layout_section(tmp_path):
    _write_contract(tmp_path, _BASE)
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "[layout]" in reason
    assert "devon" in reason and "shield" in reason


def test_validate_layout_missing_devon(tmp_path):
    _write_contract(
        tmp_path,
        _BASE + '\n[layout]\n\n[layout.shield]\nwritable = ["tests/integration/"]\n',
    )
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "[layout.devon]" in reason


def test_validate_layout_empty_devon(tmp_path):
    _write_contract(
        tmp_path,
        _BASE + "\n[layout]\n\n"
        "[layout.devon]\n"
        "writable = []\n\n"
        "[layout.shield]\n"
        'writable = ["tests/integration/"]\n',
    )
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "[layout.devon]" in reason


def test_validate_layout_missing_shield(tmp_path):
    _write_contract(
        tmp_path,
        _BASE + '\n[layout]\n\n[layout.devon]\nwritable = ["tracks/", "tests/unit/"]\n',
    )
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "[layout.shield]" in reason


def test_validate_layout_empty_shield(tmp_path):
    _write_contract(
        tmp_path,
        _BASE + "\n[layout]\n\n"
        "[layout.devon]\n"
        'writable = ["tracks/", "tests/unit/"]\n\n'
        "[layout.shield]\n"
        "writable = []\n",
    )
    reason = validate_layout(tmp_path)
    assert reason is not None
    assert "[layout.shield]" in reason


def test_fake_archer_contract_is_layout_valid(tmp_path):
    """Regression for 8f7cf04: the Fake Archer's generated project.toml must
    declare non-empty [layout.devon]/[layout.shield] writable lists, else the
    M-DESIGN EXIT layout gate escalates every Fake journey.

    Shield's writable list must cover the directories the fake Shield writes
    into — tests/integration/ and tests/e2e/ at minimum, and the complete
    host Shield scope the fake mirrors (incl. tests/e2e_live/,
    tests/assets/, tests/counterexamples/) — so a Shield e2e test write is
    never mis-audited as over_reach."""
    FakeBackend(tmp_path, "v0.1")._write_project_contract()
    assert validate_layout(tmp_path) is None
    contract = load_contract(tmp_path)
    assert contract.layout is not None
    assert contract.layout.devon.writable == ["tracks/", "tests/unit/"]
    assert contract.layout.shield.writable == [
        "tests/integration/",
        "tests/e2e/",
        "tests/e2e_live/",
        "tests/assets/",
        "tests/counterexamples/",
    ]


# ---------------------------------------------------------------------------
# B4 (issue #5): optional [lint] contract section
# ---------------------------------------------------------------------------


def test_lint_section_optional_parsed_and_degrades(tmp_path):
    """B4 (issue #5): [lint].check parsed when present, None when absent or
    malformed — a broken [lint] must never make the contract unloadable."""
    from tracks.project import lint_check_command

    _write_contract(tmp_path, _BASE)
    assert load_contract(tmp_path).lint is None
    assert lint_check_command(tmp_path) is None

    _write_contract(tmp_path, _BASE + '\n[lint]\ncheck = ".venv/bin/ruff check"\n')
    contract = load_contract(tmp_path)
    assert contract.lint is not None
    assert contract.lint.check == ".venv/bin/ruff check"
    assert lint_check_command(tmp_path) == ".venv/bin/ruff check"

    _write_contract(tmp_path, _BASE + "\n[lint]\ncheck = 123\n")
    assert load_contract(tmp_path).lint is None
