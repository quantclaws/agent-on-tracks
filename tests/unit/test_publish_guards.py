"""Pure-function coverage for the M-PUBLISH gates (IF-PUBLISH-001/002).

``assert_agent_forbidden`` is the Runtime-only actor gate; ``_operation_records``
is the all-plan preflight that opens tag/merge/release/artifact while keeping
zero-side-effect failure semantics for unknown/malformed steps.
"""

from __future__ import annotations

import hashlib

import pytest

from tests.unit.helpers import git_repo
from tracks.executor.publish import PublishBlocked, assert_agent_forbidden
from tracks.executor.publish_runtime import _operation_records

_DIGEST = "sha256:" + "a" * 64


def _plan(*steps: str) -> dict:
    return {"journey": "feature", "steps": list(steps)}


def _records(repo, *steps):
    return _operation_records(repo, _plan(*steps), _DIGEST)


# -- agent gate ---------------------------------------------------------------


# AC-FR0275-03@v0.8: the Runtime-only actor gate is pure and fail-closed.
@pytest.mark.parametrize("actor", ["runtime", "human", "Runtime", "HUMAN", " human "])
def test_assert_agent_forbidden_allows_runtime_and_human(actor):
    assert assert_agent_forbidden(actor) is None


@pytest.mark.parametrize("actor", ["Devon", "archer", "Prism", "shield", "general"])
def test_assert_agent_forbidden_blocks_agent_roles(actor):
    with pytest.raises(PublishBlocked) as excinfo:
        assert_agent_forbidden(actor)
    assert excinfo.value.reason == "agent_forbidden"


@pytest.mark.parametrize(
    "actor", [None, "", "   ", 7, ["runtime"], {"actor": "human"}]
)
def test_assert_agent_forbidden_rejects_malformed_actors(actor):
    with pytest.raises(PublishBlocked) as excinfo:
        assert_agent_forbidden(actor)
    assert excinfo.value.reason == "malformed_actor"


# -- operation preflight ------------------------------------------------------


# AC-FR0275-01/04@v0.8: all four kinds preflight with kind-specific rules.
def test_operation_records_accepts_all_four_kinds(tmp_path):
    repo = git_repo(tmp_path)
    dist = repo / "dist"
    dist.mkdir()
    wheel = dist / "demo-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")

    records, error = _records(
        repo, "merge:main", "tag:v1.0", "artifact:dist/*.whl", "release:v1.0"
    )

    assert error is None
    assert [record["operation_kind"] for record in records] == [
        "merge",
        "tag",
        "artifact",
        "release",
    ]
    artifact = records[2]
    assert artifact["target"] == "dist/*.whl"
    assert artifact["release_target"] == "v1.0"
    assert artifact["artifact_path"] == str(wheel.resolve())
    assert artifact["name"] == wheel.name
    assert artifact["size"] == len(b"wheel-bytes")
    assert artifact["sha256_local"] == hashlib.sha256(b"wheel-bytes").hexdigest()


def test_operation_records_release_must_bind_a_planned_tag(tmp_path):
    repo = git_repo(tmp_path)

    records, error = _records(repo, "release:v1.0")
    assert records is None and error == "malformed"

    records, error = _records(repo, "tag:v1.0", "release:v2.0")
    assert records is None and error == "malformed"


def test_operation_records_release_target_must_be_valid_tag_ref(tmp_path):
    repo = git_repo(tmp_path)
    records, error = _records(repo, "tag:bad..tag", "release:bad..tag")
    assert records is None and error == "malformed"


def test_operation_records_merge_target_must_be_valid_branch_ref(tmp_path):
    repo = git_repo(tmp_path)

    records, error = _records(repo, "merge:release/1.x")
    assert error is None and records[0]["operation_kind"] == "merge"

    records, error = _records(repo, "merge:bad..branch")
    assert records is None and error == "malformed"


def test_operation_records_artifact_requires_exactly_one_file(tmp_path):
    repo = git_repo(tmp_path)
    (repo / "dist").mkdir()

    records, error = _records(repo, "tag:v1.0", "artifact:dist/*.whl")
    assert records is None and error == "malformed"

    (repo / "dist" / "one.whl").write_bytes(b"one")
    records, error = _records(repo, "tag:v1.0", "artifact:dist/*.whl")
    assert error is None and records[1]["name"] == "one.whl"

    (repo / "dist" / "two.whl").write_bytes(b"two")
    records, error = _records(repo, "tag:v1.0", "artifact:dist/*.whl")
    assert records is None and error == "malformed"


def test_operation_records_artifact_must_bind_a_planned_tag(tmp_path):
    repo = git_repo(tmp_path)
    (repo / "dist").mkdir()
    (repo / "dist" / "one.whl").write_bytes(b"one")

    records, error = _records(repo, "artifact:dist/*.whl")
    assert records is None and error == "malformed"


# AC-FR0275-04@v0.8: unknown/malformed steps fail before any effect.
def test_operation_records_unknown_kind_is_unknown_operation(tmp_path):
    repo = git_repo(tmp_path)
    records, error = _records(repo, "webhook:foo")
    assert records is None and error == "unknown_operation"


def test_operation_records_validates_every_step_before_returning(tmp_path):
    repo = git_repo(tmp_path)
    records, error = _records(repo, "tag:v1.0", "merge:bad..branch")
    assert records is None and error == "malformed"


def test_operation_records_no_steps_is_unknown_operation(tmp_path):
    repo = git_repo(tmp_path)
    records, error = _operation_records(
        repo, {"journey": "feature", "steps": []}, _DIGEST
    )
    assert records is None and error == "unknown_operation"
