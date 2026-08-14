"""Q-04 audit stand-in: Devon fake-opencode behaviors (single source).

The base ``opencode`` stand-in script (generic + Shield behaviors) lives in
``test_opencode_backend.STANDIN``.  The write-audit tests additionally drive
Devon commentable-doc fake behaviors (canonical discussion replies, doc
deletion, symlink/type swaps, body edits, atomic whole-run rollback cases).

They are kept here so the shared base stand-in in ``test_opencode_backend.py``
stays under the 1200-line pylint C0302 ceiling.  The audit fixture composes
base + Devon, so neither block is duplicated across modules (no pylint R0801
duplicate-code).
"""

from tests.integration.test_opencode_backend import STANDIN as _BASE_STANDIN

# Devon fake-opencode behaviors, inserted into the base script right before its
# ``quota_error`` block.  Lines are verbatim from the pre-refactor shared
# STANDIN, so the composed script below is byte-identical to it.
DEVON_BEHAVIORS = """if behavior == "devon_reply" and docs:
    # Canonical discussion reply on a commentable doc + a legit allowed write.
    for path in docs.split(","):
        open(path, "a").write("\\n> **Devon:** implementation note\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# impl\\n")
if behavior == "devon_pre_dirty_reply" and docs:
    # Canonical discussion reply appended to pre-dirty commentable content.
    for path in docs.split(","):
        open(path, "a").write("\\n> **Devon:** reply on pre-dirty doc\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# impl\\n")
if behavior == "devon_delete_doc" and docs:
    # Agent deletes a commentable doc outright.
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
if behavior == "devon_doc_to_dangling_symlink" and docs:
    # Agent replaces a commentable doc with a dangling symlink.
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.symlink("/nonexistent/devon/evil", path)
if behavior == "devon_doc_to_dir_symlink" and docs:
    target = os.environ.get("FAKE_OPENCODE_SYMLINK_TARGET", "/tmp")
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.symlink(target, path)
if behavior == "devon_body_edit" and docs:
    # Non-discussion body edit to a commentable doc + a legit allowed write.
    for path in docs.split(","):
        open(path, "a").write("\\nagent body edit\\n")
    if extra:
        os.makedirs(os.path.dirname(extra), exist_ok=True)
        open(extra, "w").write("# impl\\n")
if behavior == "devon_overreach_plus_doc_edit" and docs:
    # Allowed write (target) + out-of-scope write (extra) + doc body edit.
    if target:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").write("# allowed impl\\n")
    if extra:
        open(extra, "w").write("# over-reach\\n")
    for path in docs.split(","):
        open(path, "a").write("\\nagent body edit\\n")
if behavior == "devon_edit_predirty_plus_doc_edit" and docs:
    # Agent modifies a pre-dirty tracked file (extra) AND body-edits a doc.
    if extra:
        with open(extra, "a") as f:
            f.write("\\nagent edit on predirty\\n")
    for path in docs.split(","):
        open(path, "a").write("\\nagent body edit\\n")
if behavior == "devon_doc_to_nonempty_dir" and docs:
    # Agent replaces a commentable doc with a plain NON-EMPTY directory tree
    # (payload + deep child) and, in the same run, writes an allowed file
    # (target) and an over-reach file (extra).  Rollback must unwound the
    # tree leaves-first, then restore the parent regular file (a shallowest-
    # first restore-then-walk raised NotADirectoryError -> filesystem).
    for path in docs.split(","):
        if os.path.exists(path):
            os.unlink(path)
        os.makedirs(path)
        with open(os.path.join(path, "payload.md"), "w") as f:
            f.write("payload\\n")
        deep = os.path.join(path, "deep")
        os.makedirs(deep)
        with open(os.path.join(deep, "child.md"), "w") as f:
            f.write("child\\n")
    if target:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        open(target, "w").write("# allowed impl\\n")
    if extra:
        open(extra, "w").write("# over-reach\\n")
"""

# Full stand-in consumed by the audit fake_opencode fixture: base + Devon.
AUDIT_STANDIN = _BASE_STANDIN.replace(
    'if behavior == "quota_error":',
    DEVON_BEHAVIORS + 'if behavior == "quota_error":',
    1,
)
