"""SHIELD_FIX manifest allowed_paths must carry the Shield layout domain.

Regression for run 01M0S0FQ v0.7 boundary (2026-08-29): the SHIELD_FIX
dispatch inherited the product task's manifest (T-017:
authenticity_existing.py only) while the diagnosed defects lived in frozen
test files, including root-level test support (tests/hotfix_support.py)
named without its tests/ prefix in the diagnosis evidence. The over-reach
auditor (manifest.allowed_paths) rolled back every legal Shield test write;
`trac retry` could not change the scope, so the boundary could never close.
"""

from pathlib import Path

from tracks.executor.m_impl_runtime import MImplRuntimeMixin

_REPO = Path(__file__).resolve().parents[2]


def _runtime(repo_path=None):
    rt = object.__new__(MImplRuntimeMixin)
    rt.repo = repo_path or _REPO
    return rt


def test_shield_allowed_paths_absorb_layout_domain():
    """A product-task manifest must not bound Shield's test writes: the
    layout domain ([layout.shield], including the tests/ root for support
    files like conftest.py / hotfix_support.py) is unioned in front."""
    rt = _runtime()
    assignment = {
        "manifest": {
            "allowed_paths": ["tracks/executor/authenticity_existing.py"],
            "forbidden_paths": [".tracks/projects/**"],
        }
    }
    rt._align_shield_allowed_to_layout(assignment)
    allowed = assignment["manifest"]["allowed_paths"]
    # product entry survives (harmless union) ...
    assert "tracks/executor/authenticity_existing.py" in allowed
    # ... and every layout dir precedes it (tests/ root now included)
    from tracks.project import layout_paths

    layout = layout_paths(_REPO, "shield")
    assert all(d in allowed for d in layout)
    assert "tests/" in layout


def test_shield_allowed_paths_no_manifest_noop():
    rt = _runtime()
    assignment = {"manifest": None}
    rt._align_shield_allowed_to_layout(assignment)
    assert assignment == {"manifest": None}


def test_shield_allowed_paths_dedup():
    rt = _runtime()
    assignment = {"manifest": {"allowed_paths": ["tests/integration/"]}}
    rt._align_shield_allowed_to_layout(assignment)
    allowed = assignment["manifest"]["allowed_paths"]
    assert allowed.count("tests/integration/") == 1
