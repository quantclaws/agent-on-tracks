"""Auto-start coverage in the `trac` CLI subprocesses spawned by E2E tests.

Python imports `sitecustomize` at interpreter startup when it is on sys.path.
conftest.py puts this directory on a subprocess's PYTHONPATH ONLY when the test
session itself runs under coverage, and sets COVERAGE_PROCESS_START so
`process_startup()` knows the config. With parallel mode each subprocess writes
its own `.coverage.*` data file (to the absolute COVERAGE_FILE base), later
merged by `coverage combine`. This is a no-op unless COVERAGE_PROCESS_START is
set, so plain `pytest` runs are unaffected.
"""
import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except Exception:  # never break the CLI because of coverage bookkeeping
        pass
