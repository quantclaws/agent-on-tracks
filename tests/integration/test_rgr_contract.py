"""RGR git state and M-IMPL Red classification via IF-IMPL-004."""

import subprocess

import pytest

from tracks.executor.rgr import (
    classify_red,
    create_green_commit,
    create_red_ref,
    verify_lineage,
)


def _sha(repo, revision="HEAD"):
    return subprocess.run(
        ["git", "rev-parse", revision],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


RED_DIFF = """diff --git a/tests/unit/test_feature.py b/tests/unit/test_feature.py
new file mode 100644
--- /dev/null
+++ b/tests/unit/test_feature.py
@@ -0,0 +1,2 @@
+def test_feature():
+    assert feature() == 1
"""

GREEN_DIFF = """diff --git a/tracks/feature.py b/tracks/feature.py
new file mode 100644
--- /dev/null
+++ b/tracks/feature.py
@@ -0,0 +1,2 @@
+def feature():
+    return 1
"""


@pytest.mark.integration
# AC-FR0070-03@v0.5 TRACKS-TRACE immutable private R ref exists
# AC-FR0070-04@v0.5 TRACKS-TRACE compare-and-set preserves R
# AC-FR0120-01@v0.5 TRACKS-TRACE G parent and trailers
# AC-FR0120-02@v0.5 TRACKS-TRACE ref trailer event lineage proof
# AC-FR0200-02@v0.5 TRACKS-TRACE git lineage survives replay
def test_rgr_git_contract_happy_and_immutable(host_repo):
    base = _sha(host_repo)
    red = create_red_ref(str(host_repo), "RUN", "T-001", 1, RED_DIFF, base)
    duplicate = create_red_ref(str(host_repo), "RUN", "T-001", 1, RED_DIFF, base)
    assert red.created is True and duplicate.created is False
    assert _sha(host_repo, red.ref) == red.sha
    green = create_green_commit(
        str(host_repo), "RUN", "T-001", 1, GREEN_DIFF, base, red.sha
    )
    events = [
        {"seq": 10, "type": "red.checkpointed", "payload": {"r_sha": red.sha}},
        {"seq": 11, "type": "green.committed", "payload": {"g_sha": green.sha}},
    ]
    proof = verify_lineage(str(host_repo), "RUN", "T-001", 1, green.sha, events)
    assert green.parent == base
    assert green.trailers == {
        "Tracks-Task": "T-001",
        "Tracks-Attempt": 1,
        "Tracks-R": red.sha,
    }
    assert proof.r_before_g and proof.r_ref_exists
    assert proof.g_trailers_valid and proof.event_order_valid


@pytest.mark.integration
# AC-FR0080-01@v0.5 TRACKS-TRACE only assertion and symbol Reds are legal
def test_m_impl_red_classification_excludes_stub_tokens():
    assert classify_red("node", 1, "E assert 1 == 2", "") == "assertion_failure"
    assert classify_red("node", 1, "", "NameError: missing") == "symbol_missing"
    assert classify_red(
        "node", 1, "", 'NotImplementedError("IF-IMPL-004")'
    ) == "unclassified"
