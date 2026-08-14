"""Parser performance benchmark (NFR-0020 / AC-NFR0020-01).

Opt-in: marked ``performance`` and excluded from the default suite
(``addopts = -m 'not performance'``). Run on demand after v0.2 and any later
change to the discuss parser::

    .venv/bin/python -m pytest -m performance

AC-NFR0020-01: a 1MB synthetic document containing 5000 discussion threads
parses in < 1 second. The discuss parser is an O(n) single-pass line scan
(architecture.md: discuss has no persistent ID, full scan rebuilds threads), so
this guards the linear bound at the NFR-0020 size ceiling against regressing to
quadratic behaviour.
"""

import time

import pytest

from tracks.discuss.parser import parse_threads

THREADS = 5000
ONE_MB = 1_000_000
BUDGET_SECONDS = 1.0

# filler sized so 5000 threads (root + reply each, plus a section header) land
# just above the 1MB NFR-0020 ceiling — verified by the size assertion below.
_FILLER = "lorem ipsum dolor sit amet " * 3


def _synthetic_document() -> str:
    """~1MB of markdown prose interleaved with THREADS discussion threads."""
    lines = ["# Synthetic performance document", ""]
    for i in range(THREADS):
        lines.append(f"## Section {i}")
        lines.append("")
        lines.append(f"> **Aaron:** thread {i} raises a question. {_FILLER}")
        lines.append(f">> **Sage:** reply {i} answers it. {_FILLER}")
        lines.append("")
    return "\n".join(lines)


def _time_parse(text: str) -> float:
    start = time.perf_counter()
    threads = parse_threads(text)
    elapsed = time.perf_counter() - start
    # parsed every thread — not a silent short-circuit on a malformed fixture
    assert len(threads) == THREADS
    return elapsed


@pytest.mark.performance
def test_parse_1mb_5000_threads_under_1s():
    text = _synthetic_document()
    size = len(text.encode("utf-8"))
    # sanity: the fixture actually exercises the NFR-0020 1MB ceiling
    assert size >= ONE_MB, f"fixture too small to be a fair test: {size} bytes"

    # best-of-3 absorbs scheduler / cold-start noise; the margin to the budget is
    # orders of magnitude, so this is stability, not a tight micro-benchmark.
    elapsed = min(_time_parse(text) for _ in range(3))
    assert elapsed < BUDGET_SECONDS, (
        f"parse took {elapsed:.3f}s for {size / ONE_MB:.2f}MB / {THREADS} threads "
        f"(budget {BUDGET_SECONDS}s, NFR-0020)"
    )
