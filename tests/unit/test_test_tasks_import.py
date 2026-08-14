"""Regression: ``import tracks.executor.test_tasks`` must succeed on a fresh
interpreter without ``validate`` having been imported first.

Before the ``validation_shared.py`` extraction this was a fragile circular
import: ``test_tasks`` imported scanner primitives (``_acc_scan``,
``_strip_comments``, ``_HEADING``) from ``validate``, which mid-init imported
``test_tasks`` back. It only worked because ``validate`` placed the reverse
import after those primitives were defined. A cold ``import
tracks.executor.test_tasks`` that bypassed that ordering would fail. The
shared module breaks the cycle so any import order is safe.
"""

import subprocess
import sys


def test_test_tasks_imports_in_fresh_subprocess():
    """A fresh subprocess importing only test_tasks must not fail.

    The subprocess inherits no module cache, so this proves the import works
    without ``tracks.executor.validate`` having been loaded first.
    """
    proc = subprocess.run(
        [sys.executable, "-c", "import tracks.executor.test_tasks"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"import failed (rc={proc.returncode}):\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_test_tasks_primitives_come_from_shared():
    """The scanner primitives on test_tasks come from validation_shared, not
    validate, so no module-init ordering hazard remains."""
    import tracks.executor.test_tasks as tt
    import tracks.executor.validation_shared as vs

    assert tt._acc_scan is vs._acc_scan
    assert tt._strip_comments is vs._strip_comments
    assert tt._HEADING is vs._HEADING
