"""inline-discussion bypass (FR-050..FR-120, IF-003 §6/§7a).

Doc-level bypass: discussion threads live in a document's markdown blockquotes,
NOT in the events table, and ``Command.kind`` gains no member for them. Identity
is rebuilt by a full scan + 4-level degrade on every access (FR-070); there is
no persistent thread id — ``T-NNN`` is a per-scan display label only.
"""
