"""T-040 RED: release trace closed-loop export + report §2b rendering.

Declares the red obligations for tracks/checks/trace.py and tracks/report.py
(IF-MILESTONE-001 trace side / FR-0273, FR-0276, FR-0284):

1. checks/trace.py exports ``build_release_trace`` (architecture §3 modules
   table row 13 / §1.0.x release trace join) composing the interfaces §1i
   CLOSED field set ``{candidate_sha, artifact_digest, evidence_digests,
   preview_digest, human_approval_event_seq, operation_digests, release_tag,
   trace_digest}`` with ``trace_digest = "sha256:" + sha256(canonical_json(
   remaining fields))`` (interfaces.md §1i). Open-set inputs (unknown field)
   and partial inputs (missing field) are rejected fail-closed; the digest is
   deterministic and drift-sensitive (external tag/release cross-check,
   NFR-0143-02).

2. report.py renders the §2b sections over the release journey events
   (assignment T-040 description): an ``Issue map`` section (issue.mapped
   authority rows, the close event carrying the candidate SHA /
   preview_digest / release_tag trace comment tokens, and the FAKE
   counterexample rejection, FR-0284), a ``Release pipeline`` section
   (preview binding digests + awaiting_release/stale status with
   stale_reason, FR-0273; release.decided bound to its preview_digest), and
   a ``Release trace`` section (the §1i trace payload plus the
   milestone.sealed / refs.cleaned closure completion, FR-0276).

RED note: today checks/trace.py has no ``build_release_trace`` export and
report._markdown renders these payloads only as raw JSON inside the generic
Timeline/Events sections -- no §2b section exists -- so the discriminating
assertions below fail while run seeding and report generation themselves
work.

RED note (round 2, structural normalization): the §2b section bodies and
tokens above are implemented and green, but report.py emits the three §2b
headings as top-level (single ``#``) -- a deviation from the report's own
section structure (``## Timeline``/``## Events``) that existed only to
satisfy a defective section slicer in this file (one-hash strip that
matched a section's own heading as the next heading). This round repairs
the slicer and re-anchors the three rendering tests at the report section
level (``##``), so they fail on that structural token until report.py
emits the §2b sections at the report section level.
"""

from __future__ import annotations

import hashlib
import json
import re

from tracks import paths
from tracks.checks import trace as trace_module
from tracks.cli.main import cmd_report
from tracks.store import Store

RUN_ID = "run-v08-trace"
CAND = "abc1234def5678"
TAG = "v0.8.0"
PVIEW = "sha256:" + "b" * 64
PVIEW2 = "sha256:" + "c" * 64
ART = "sha256:" + "d" * 64

RELEASE_TRACE_FIELDS = frozenset(
    {
        "candidate_sha",
        "artifact_digest",
        "evidence_digests",
        "preview_digest",
        "human_approval_event_seq",
        "operation_digests",
        "release_tag",
        "trace_digest",
    }
)


def _fail(message: str) -> None:
    raise AssertionError(f"assertion failure: {message}")


def _canonical_json(value: dict) -> str:
    """Repo canonical JSON (phase0_seal convention: sort_keys + compact)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _build_release_trace(**fields) -> dict:
    """Call the §1i composer, failing on the contract token when missing."""
    builder = getattr(trace_module, "build_release_trace", None)
    if builder is None:
        _fail(
            "checks/trace.py missing build_release_trace export "
            "(IF-MILESTONE-001 interfaces §1i closed field set, "
            "architecture §3 modules row 13): release trace closed-loop "
            "export not implemented"
        )
    return builder(**fields)


def _trace_fields(**overrides) -> dict:
    fields = {
        "candidate_sha": CAND,
        "artifact_digest": ART,
        "evidence_digests": ["sha256:" + "e" * 64],
        "preview_digest": PVIEW,
        "human_approval_event_seq": 7,
        "operation_digests": ["sha256:" + "f" * 64],
        "release_tag": TAG,
    }
    fields.update(overrides)
    return fields


def _section(markdown: str, name: str) -> str:
    """Slice one named §2b report section out of the rendered markdown.

    §2b sections are sections *of* the report: they must render at the
    report's section level (``##``, sibling of ``## Timeline``/``## Events``),
    not as stray top-level headings. The slice starts after the matched
    heading line and ends at the next heading of any level (1-3 hashes);
    stripping a single hash from ``tail`` instead would mistake the section's
    own heading line for the next heading.
    """
    any_level = re.search(rf"(?m)^#{{1,3}} .*{re.escape(name)}.*$", markdown)
    if not any_level:
        _fail(f"workflow report lacks a {name!r} section (§2b rendering)")
    if any_level.group(0) != f"## {name}":
        _fail(
            f"{name!r} heading is {any_level.group(0).split()[0]!r}-level / "
            f"{any_level.group(0)!r} instead of the exact report section form "
            "'## " + name + "' (§2b sections are report-level sections like "
            "## Timeline/## Events, not top-level headings)"
        )
    tail = markdown[any_level.start() :]
    heading_end = tail.find("\n") + 1
    if not heading_end:
        return tail
    nxt = re.search(r"(?m)^#{1,3} ", tail[heading_end:])
    return tail[: heading_end + nxt.start()] if nxt else tail


def _seed_run(repo, events: list[tuple[str, dict]]) -> None:
    home = paths.tracks_home(repo)
    store = Store(home)
    try:
        store.append(RUN_ID, "v0.8", "story.requested", {"raw_chars": 1})
        for event_type, payload in events:
            store.append(RUN_ID, "v0.8", event_type, payload)
    finally:
        store.close()


def _render(repo, tmp_out) -> str:
    out = tmp_out / "report-out"
    assert cmd_report(repo, "--run-id", RUN_ID, "--output", str(out)) == 0
    return (out / "report.md").read_text(encoding="utf-8")


# interfaces §1i / FR-0276-01: the release trace is the closed 8-field set.
# AC-FR0276-01@v0.8 TRACKS-TRACE §1i closed 8-field release trace composer
def test_build_release_trace_composes_closed_field_set():
    built = _build_release_trace(**_trace_fields())
    if not isinstance(built, dict):
        _fail("build_release_trace must return the trace as a dict mapping")
    if set(built) != RELEASE_TRACE_FIELDS:
        _fail(
            f"build_release_trace field set {sorted(built)} != closed §1i set "
            f"{sorted(RELEASE_TRACE_FIELDS)}"
        )
    if not str(built["trace_digest"]).startswith("sha256:"):
        _fail(
            f"trace_digest {built['trace_digest']!r} is not a sha256 digest "
            "(interfaces §1i trace_digest form)"
        )


# interfaces §1i / FR-0284-01: trace_digest binds the remaining fields
# canonically so the close comment tokens are externally cross-checkable.
# AC-FR0284-01@v0.8 TRACKS-TRACE trace_digest binds remaining fields canonically
def test_trace_digest_binds_remaining_fields_canonically():
    fields = _trace_fields()
    built = _build_release_trace(**fields)
    remaining = {k: v for k, v in fields.items() if k != "trace_digest"}
    expected = "sha256:" + hashlib.sha256(
        _canonical_json(remaining).encode("utf-8")
    ).hexdigest()
    if built["trace_digest"] != expected:
        _fail(
            f"trace_digest {built['trace_digest']} != sha256 of canonical json "
            f"of the remaining fields ({expected})"
        )
    again = _build_release_trace(**fields)
    if again["trace_digest"] != built["trace_digest"]:
        _fail("build_release_trace is not deterministic for identical inputs")
    drifted = _build_release_trace(**_trace_fields(candidate_sha="deadbeef"))
    if drifted["trace_digest"] == built["trace_digest"]:
        _fail(
            "trace_digest did not change after candidate_sha drift "
            "(external tag/release cross-check would miss evidence drift)"
        )


# interfaces §1i closed-set discipline: open-set and partial inputs fail.
# AC-FR0276-02@v0.8 TRACKS-TRACE closed-set fail-closed discipline on trace inputs
def test_build_release_trace_rejects_open_and_partial_inputs():
    # Presence first: without this, the probes below would swallow the
    # contract-missing assertion as just another "rejected" exception.
    _build_release_trace(**_trace_fields())

    def _reject(call):
        try:
            call()
        except Exception:
            return
        _fail(
            "build_release_trace accepted an input outside the §1i closed "
            "field set without failing closed"
        )

    _reject(lambda: _build_release_trace(**_trace_fields(foreign_field=1)))
    partial = _trace_fields()
    del partial["release_tag"]
    _reject(lambda: _build_release_trace(**partial))


# FR-0284-02 / FR-0284-01: report shows the Issue map, the trace-comment
# close tokens and the FAKE counterexample rejection.
# AC-FR0284-02@v0.8 TRACKS-TRACE report Issue map FAKE rejection close tokens
def test_report_renders_issue_map_and_fake_rejection(host_repo, tmp_path):
    repo = host_repo
    _seed_run(
        repo,
        [
            (
                "issue.mapped",
                {
                    "repo": "acme/host",
                    "issue_number": 12,
                    "url": "https://github.com/acme/host/issues/12",
                    "baseline_digest": "sha256:" + "1" * 64,
                    "api_verified": True,
                },
            ),
            (
                "issue.fake_rejected",
                {
                    "issue": "FAKE-97",
                    "project": "fake-project",
                    "reason": "fake_rejected",
                },
            ),
            (
                "issue.closed",
                {
                    "issue_number": 12,
                    "comment": (
                        f"release trace: candidate SHA {CAND} "
                        f"preview_digest {PVIEW} release_tag {TAG}"
                    ),
                },
            ),
        ],
    )
    markdown = _render(repo, tmp_path)
    section = _section(markdown, "Issue map")
    for token in ("#12", "acme/host", "api_verified", "FAKE-97", "fake_rejected"):
        assert token in section, f"Issue map section misses {token}"
    for trace_token in (CAND, PVIEW, TAG):
        assert trace_token in section, (
            f"close comment trace token {trace_token} not shown in Issue map"
        )


# FR-0273-01 / FR-0273-02: report audits the preview binding digests and the
# stale judgement; release.decided shows its preview_digest binding.
# AC-FR0273-01@v0.8 TRACKS-TRACE report preview binding digests auditable
# AC-FR0273-02@v0.8 TRACKS-TRACE report stale preview judgement stale_reason
def test_report_renders_release_pipeline_preview_and_decision(
    host_repo, tmp_path
):
    repo = host_repo
    _seed_run(
        repo,
        [
            (
                "release.previewed",
                {
                    "candidate_sha": CAND,
                    "preview_digest": PVIEW,
                    "artifact_digest": ART,
                    "evidence_digests": {"full_f": "sha256:" + "e" * 64},
                    "operation_plan": "sha256:" + "f" * 64,
                    "status": "awaiting_release",
                    "stale_reason": "none",
                },
            ),
            (
                "release.decided",
                {
                    "kind": "release",
                    "candidate_sha": CAND,
                    "preview_digest": PVIEW,
                },
            ),
            (
                "release.previewed",
                {
                    "candidate_sha": CAND,
                    "preview_digest": PVIEW2,
                    "artifact_digest": ART,
                    "evidence_digests": {"full_f": "sha256:" + "e" * 64},
                    "operation_plan": "sha256:" + "f" * 64,
                    "status": "awaiting_release",
                    "stale_reason": "none",
                },
            ),
        ],
    )
    markdown = _render(repo, tmp_path)
    section = _section(markdown, "Release pipeline")
    for token in (CAND, PVIEW, PVIEW2, ART, "awaiting_release", "stale_reason"):
        assert token in section, f"Release pipeline section misses {token}"
    assert "stale" in section and "none" in section, (
        "Release pipeline must show awaiting_release vs stale judgement "
        "with stale_reason (FR-0273-02)"
    )
    assert "decided" in section and PVIEW in section, (
        "release.decided must be shown bound to its preview_digest"
    )


# FR-0276-01 / FR-0276-02: report exports the §1i release trace payload and
# shows the closure completion (milestone.sealed, refs.cleaned).
# AC-FR0276-01@v0.8 TRACKS-TRACE report Release trace section §1i export
# AC-FR0276-02@v0.8 TRACKS-TRACE report closure completion sealed refs.cleaned
def test_report_renders_release_trace_section(host_repo, tmp_path):
    repo = host_repo
    trace_payload = _build_release_trace(**_trace_fields())
    _seed_run(
        repo,
        [
            ("milestone.trace_closed", trace_payload),
            ("milestone.sealed", {"archive": "readonly-sealed"}),
            ("refs.cleaned", {"refs": [f"refs/trac/tmp/{RUN_ID}/x"]}),
        ],
    )
    markdown = _render(repo, tmp_path)
    section = _section(markdown, "Release trace")
    for token in (CAND, PVIEW, TAG, trace_payload["trace_digest"]):
        assert token in section, f"Release trace section misses {token}"
    assert "sealed" in section, "Release trace section misses milestone.sealed"
    assert "refs.cleaned" in section, (
        "Release trace section misses refs.cleaned closure completion"
    )
