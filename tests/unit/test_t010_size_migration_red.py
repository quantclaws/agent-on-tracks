"""T-010 RED: size-migration pin for the C0302 line governance (round 19).

Devon-owned unit RED for the T-010 ISLAND_GATE_2 repair batch (M2 joint
ruling; round-19 replan after plan_defect seq 12036): the D1 residual —
``tracks/supervisor/service.py`` must migrate its cohesive blocks into the
existing declared supervisor modules (``intake.py`` / ``db.py``) and land
within the C0302 module-line budget. The failing node pins the contract
the next GREEN must deliver:

- module size (architecture NFR-06b; ``pyproject.toml``
  ``[tool.pylint.format] max-module-lines = 1200`` — the pre-commit + CI
  ``lint`` gate over ``tracks``): ``service.py`` and ``projections.py``
  each stay at <=1200 physical module lines. Today ``service.py`` sits at
  1426 (the 2113->1426 in-file reduction is exhausted; the remaining mass
  is exactly the declared cross-module migration), so the node is the
  legal red until the migration completes. ``projections.py`` already
  complies (1246->1099) and stays pinned there;

- import face (interfaces §1b / IF-CMDSVC-001, IF-GATEUI-001,
  IF-WEBGATE-001): the size migration keeps the service module's public
  import face alive — the module imports, and ``CommandService`` plus the
  closed ``SERVICE_COMMAND_KINDS`` set stay importable from
  ``tracks.supervisor.service`` (``guard.py`` and the HTTP command face
  import them; D1's contract is 行为与 import 面零变更). This node is the
  regression guard the GREEN migration must not break.

M-IMPL RED discipline: the defect is present in the product tree (1426 >
1200), so the node fails as a plain AssertionError against the documented
threshold — no stubs, no assembly errors, no ``pytest.fail``. Nothing
here mocks the system under test: the size read is the module source on
disk; the import face is the real module object.

The D2/D3 WIP adaptations (``test_t003`` superset criterion,
``test_t005`` INSERT OR IGNORE lease fixture) are absorbed into this R
slot via the worktree diff and are not modified by this round.

AC: FR-0342 — TRACKS-TRACE IF-GATEUI-001, IF-WEBGATE-001.
"""

from __future__ import annotations

from pathlib import Path

import tracks.supervisor.service

# C0302 contract (architecture NFR-06b; pyproject [tool.pylint.format]
# max-module-lines=1200 — the pre-commit + CI lint gate over `tracks`).
_MAX_MODULE_LINES = 1200

_REPO_ROOT = Path(tracks.supervisor.service.__file__).resolve().parents[2]


# AC-FR0342-02@v0.11 TRACKS-TRACE IF-GATEUI-001 both modules within C0302
def test_service_and_projections_within_c0302_module_line_budget():
    subjects = {
        "tracks/supervisor/service.py": _REPO_ROOT / "tracks" / "supervisor" / "service.py",
        "tracks/server/projections.py": _REPO_ROOT / "tracks" / "server" / "projections.py",
    }
    for rel, path in subjects.items():
        assert path.is_file(), f"fixture: {rel} must exist in the product tree"
        lines = len(path.read_text(encoding="utf-8").splitlines())
        assert lines <= _MAX_MODULE_LINES, (
            f"{rel} carries {lines} module lines — over the C0302 budget of"
            f" {_MAX_MODULE_LINES} (architecture NFR-06b): the D1 cohesive-block"
            " migration into the existing supervisor modules (intake.py / db.py,"
            " interfaces §1b; 行为与 import 面零变更) is still due"
        )


# AC-FR0342-02@v0.11 TRACKS-TRACE IF-WEBGATE-001 import face survives the migration
def test_service_public_import_face_alive_after_migration():
    assert callable(tracks.supervisor.service.CommandService), (
        "CommandService must stay importable from tracks.supervisor.service"
        " (guard.py / the HTTP command face import it; D1 keeps the import"
        " surface unchanged)"
    )
    kinds = tracks.supervisor.service.SERVICE_COMMAND_KINDS
    assert "record_triage" in kinds and "record_review" in kinds, (
        "the closed SERVICE_COMMAND_KINDS set keeps the v0.11 gate kinds"
        " (interfaces §1b #17/18) after the size migration"
    )
