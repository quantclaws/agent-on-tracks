"""``trac serve`` — service lifecycle entry (IF-SERVE-001).

Single-command manual start/stop of the v0.9 web service (no daemon /
autostart / log rotation this version). Parses the grammar of interfaces
§2a, provisions first-start password material, builds the composition root
(``tracks/server/app.py:create_app``), runs startup recovery and the
supervisor, and serves under uvicorn until SIGINT/SIGTERM.

待实现 Devon foundation task：本模块行为体与 ``cli/main.py`` 的
``_COMMANDS``/USAGE 注册同步交付（同 v0.8 cmd_release 先例）。
"""

from __future__ import annotations

from pathlib import Path


def cmd_serve(repo: Path, *args: str) -> int:
    """Parse serve flags and run the service until stopped (interfaces §2a)."""
    raise NotImplementedError("IF-SERVE-001")
