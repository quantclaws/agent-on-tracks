"""tracks/checks - read-only reporting tools (FR-0080/FR-0090).

Sixth growth axis: orthogonal to the event-sourced kernel/effects axes and the
discuss/ bypass. checks/ reads the filesystem (version dirs + tests/ +
pyproject.toml) as its reporting remit; it does not import kernel/effects and is
not referenced by project/decide. Consumed by cli (trac check subcommands) and
executor (M-TEST EXIT gate calls trace).
"""
