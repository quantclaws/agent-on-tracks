"""T-010 RED: page shells and the Vditor host (IF-DOCREV-001).

Devon-owned unit RED for the T-010 delivery slice (``tracks/server/pages.py``):

- ``render_page`` renders every name of the closed ``PAGES`` set (E-01..E-08)
  as a complete HTML document and rejects names outside the set
  (interfaces §2b page list);
- the material review page (E-06) hosts the vendored Vditor build from the
  same origin (``/static/vendor/vditor/``, never a CDN), shows the revision
  identity it was handed and carries the revision-compare area
  (interfaces §2b #15-17, §4a #4; FR-0294);
- ``vditor_asset_tags`` references only vendored same-origin assets and never
  the optional render engines that are not vendored (interfaces §2b, §3.4).

The scaffold bodies still raise their IF-DOCREV-001 stub token; every failing
node guards that stub state into a real ``AssertionError`` so the records
classify as assertion_failure (no stub_token, no assembly errors).

AC: FR-0294 — TRACKS-TRACE IF-DOCREV-001.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tracks.server import pages

# Content-addressed revision ids (baseline.revision_digest semantics); the
# review page is handed them by its caller and must not invent its own.
_REVISION_OLD = "1" * 64
_REVISION_NEW = "2" * 64
_DIFF_LINE = "+revision-two material line"
_CDN_MARKERS = ("http://", "https://", "//cdn", ".cdn.")


def _calling(call: Callable[[], Any], label: str) -> Any:
    """Call a scaffold seam; a NotImplementedError stub becomes an assertion."""
    try:
        return call()
    except NotImplementedError as exc:
        raise AssertionError(f"{label} is still a stub: {exc}") from None


def _doc_context() -> dict:
    """Material-review context: current revision, history and revision diff."""
    return {
        "run_id": "R-T010",
        "project_id": "P-T010",
        "doc": "spec",
        "revision": _REVISION_NEW,
        "content": "# SPEC-009\n\nreviewed material body\n",
        "history": [
            {"revision": _REVISION_OLD, "ts": "2026-09-20T10:00:00+00:00", "actor": "Human"},
            {"revision": _REVISION_NEW, "ts": "2026-09-20T10:05:00+00:00", "actor": "Human"},
        ],
        "diff": {
            "from": _REVISION_OLD,
            "to": _REVISION_NEW,
            "unified_diff": (
                "--- a/spec.md\n+++ b/spec.md\n@@ -1 +1 @@\n"
                "-first material line\n" + _DIFF_LINE + "\n"
            ),
        },
    }


def _render(name: str) -> str:
    html = _calling(lambda: pages.render_page(name, _doc_context()), f"render_page({name!r})")
    assert isinstance(html, str) and html.strip(), f"render_page({name!r}) must render HTML"
    return html


def _asset_tags() -> list[str]:
    tags = _calling(pages.vditor_asset_tags, "vditor_asset_tags")
    assert isinstance(tags, list) and tags, "vditor_asset_tags must return the vendored tags"
    assert all(isinstance(tag, str) and tag.strip() for tag in tags), "asset tags must be strings"
    return tags


# -- page shell: the closed PAGES set renders complete HTML documents --------


# AC-FR0294-01@v0.9 TRACKS-TRACE IF-DOCREV-001 closed-set pages render HTML shells
def test_pages_closed_set_renders_html_shells():
    assert len(pages.PAGES) == 8, "PAGES is the E-01..E-08 closed set"
    assert set(pages.PAGES) == {
        "login",
        "overview",
        "projects",
        "run_new",
        "run_detail",
        "review",
        "todos",
        "release",
    }, "PAGES must cover the interfaces §2b page list"
    for name in pages.PAGES:
        html = _render(name)
        lowered = html.lower()
        assert "<!doctype html" in lowered, f"{name} page must be a complete HTML document"
        assert "<html" in lowered and "</html>" in lowered, f"{name} page must close <html>"
        assert "<body" in lowered and "</body>" in lowered, f"{name} page must have a body"


# AC-FR0294-01@v0.9 TRACKS-TRACE IF-DOCREV-001 out-of-set page names rejected
def test_pages_reject_unknown_page_name():
    try:
        pages.render_page("not_a_page", _doc_context())
    except NotImplementedError as exc:
        raise AssertionError(f"render_page is still a stub: {exc}") from None
    except ValueError:
        return
    raise AssertionError("render_page must reject names outside the PAGES closed set")


# -- review page (E-06): Vditor host, revision identity, compare area --------


# AC-FR0294-02@v0.9 TRACKS-TRACE IF-DOCREV-001 review page hosts same-origin Vditor
def test_review_page_hosts_vditor_from_same_origin():
    html = _render("review")
    assert "/static/vendor/vditor/index.min.js" in html, "editor core must load from the origin site"
    assert "/static/vendor/vditor/index.css" in html, "editor styles must load from the origin site"
    lowered = html.lower()
    for marker in _CDN_MARKERS:
        assert marker not in lowered, f"Vditor assets must not reference {marker!r}"


# AC-FR0294-01@v0.9 TRACKS-TRACE IF-DOCREV-001 review page shows revision identity
def test_review_page_shows_revision_identity():
    html = _render("review")
    assert _REVISION_NEW in html, "the review page must show the revision being reviewed"


# AC-FR0294-02@v0.9 TRACKS-TRACE IF-DOCREV-001 review page carries revision compare
def test_review_page_carries_revision_compare_area():
    html = _render("review")
    assert _REVISION_OLD in html, "the compare area must surface the compared revision"
    assert _DIFF_LINE in html, "the compare area must surface the revision difference"


# -- vditor_asset_tags: vendored, same-origin, no optional engines -----------


# AC-FR0294-02@v0.9 TRACKS-TRACE IF-DOCREV-001 tagged assets stay vendored/same-origin
def test_vditor_asset_tags_are_same_origin_vendored_assets():
    tags = _asset_tags()
    for tag in tags:
        lowered = tag.lower()
        assert lowered.lstrip().startswith(("<script", "<link")), f"not an asset tag: {tag!r}"
        assert "/static/vendor/vditor/" in tag, f"asset must be served from the origin site: {tag!r}"
        for marker in _CDN_MARKERS:
            assert marker not in lowered, f"asset tag must not reference {marker!r}: {tag!r}"
    joined = "\n".join(tags)
    assert "index.min.js" in joined, "the vendored editor core must be tagged"
    assert "index.css" in joined, "the vendored editor styles must be tagged"


# AC-FR0294-02@v0.9 TRACKS-TRACE IF-DOCREV-001 un-vendored engines stay untagged
def test_vditor_asset_tags_exclude_optional_engines():
    joined = "\n".join(_asset_tags()).lower()
    for engine in ("mermaid", "echarts", "katex", "mathjax", "emoji"):
        assert engine not in joined, f"{engine} is not vendored and must stay disabled"
