"""Real wheel/dependency setup, with an external CLI that must never be called.

This preflight needs the package index (or an operator-provided wheelhouse),
but no model or GitHub credentials. It proves installation readiness only;
it never produces successful Agent or release-journey evidence.
"""

import os
import shlex

from tests.e2e_live.harness import prepare_live_install


# AC-FR0348-01@v0.12 TRACKS-TRACE live setup imports the installed CLI and Web without dispatching
def test_isolated_live_install_preflight(tmp_path, monkeypatch):
    tools_bin = tmp_path / "external-tools"
    tools_bin.mkdir()
    invoked = tmp_path / "unexpected-model-invocation"
    opencode = tools_bin / "opencode"
    opencode.write_text(
        "#!/bin/sh\n"
        f"touch {shlex.quote(str(invoked))}\n"
        "exit 73\n",
        encoding="utf-8",
    )
    opencode.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools_bin) + os.pathsep + os.environ["PATH"])
    host = tmp_path / "host"
    host.mkdir()
    installed = prepare_live_install(host)
    assert installed.isolated_python.is_file()
    assert installed.probe["tracks_file"].startswith(str(installed.isolated_venv))
    assert not invoked.exists(), "installation preflight must not start a model dispatch"
