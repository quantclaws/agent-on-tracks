"""Resolve version-qualified hotfix test-plan ACs against their own sources."""

from pathlib import Path

from tracks.executor.validation_shared import _acc_scan


def resolve_ac_sources(projects: Path, refs: list[str]) -> tuple[str, list[str]]:
    """Return source acceptance text and errors for version-qualified row refs.

    Callers validate the grammar before passing refs here. Each reference is
    checked against its own version, not against a union of unrelated versions.
    """
    sources: dict[str, str] = {}
    issues: list[str] = []
    for ref in sorted(set(refs)):
        ac_id, version = ref.split("@", 1)
        if version not in sources:
            path = projects / version / "acceptance.md"
            sources[version] = path.read_text(encoding="utf-8") if path.is_file() else ""
        _, acs = _acc_scan(sources[version])
        if ac_id not in {ac for ac, _fr, _line, _section in acs}:
            issues.append(f"unknown AC {ref} in {version}/acceptance.md")
    return "\n".join(sources.values()), issues
