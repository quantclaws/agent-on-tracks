"""Worker subprocess entry point (interfaces §1i, §1d).

Runs one claimed command in isolation: for ``drive_run`` it loops
``executor.drive.drive_once`` until a boundary (await_human /
await_external / terminal / failed) and returns the structured outcome to
the supervisor; for gate commands it executes the shared gate semantics.
The worker writes run-plane events through the existing writer_lock
discipline; it never talks to HTTP clients.

Contract token: IF-DRIVE-001.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Entry: python -m tracks.supervisor.worker_main --command-id <id>."""
    raise NotImplementedError("IF-DRIVE-001")


if __name__ == "__main__":  # pragma: no cover - process entry
    raise SystemExit(main())
