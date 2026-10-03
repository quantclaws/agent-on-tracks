/**
 * Explicit save + conflict recovery (IF-DOCSAVE-001 client face; §1o.2).
 *
 * The save control is enabled only while the draft differs from the server
 * revision. A 409 carries the live token as the response body's top-level
 * ``current_revision``; the editor offers exactly two options — reload and
 * discard (server content + ``current_revision`` become the baseline) or
 * force overwrite (the local draft is resubmitted with ``current_revision``
 * as the new ``base_revision``). It never overwrites silently, and any other
 * write failure keeps the local draft retryable.
 *
 * Content surfaces: the authoritative draft is the markdown source area
 * (``doc-editor-content``); the Vditor ir host renders it live (§1n.3) and a
 * load failure swaps in the ``doc-editor-fallback`` textarea — the document
 * stays viewable and editable in every path.
 */

import { postJson } from "./api.js";
import { element } from "./dom.js";

export function createEditor({ url, revision, content, reload } = {}) {
  const draft = {
    content: content || "",
    saved: content || "",
    baseRevision: revision || "",
    dirty: false,
  };

  // The Vditor ir mount host (§1n.3): assets load on demand, same-origin.
  const irHost = element("div", {
    class: "vditor-host",
    "data-testid": "vditor-ir-host",
  });
  // The markdown source area — the authoritative editable draft.
  const source = element("textarea", {
    class: "doc-source",
    "data-testid": "doc-editor-content",
    rows: 10,
    spellcheck: "false",
  });
  source.value = draft.content;
  let touched = false; // any local edit since mount (host adoption never clobbers)
  source.addEventListener("input", () => {
    touched = true;
    setContent(source.value);
  });

  const surface = element("div", { class: "doc-surface", "data-testid": "doc-surface" }, [
    irHost,
    source,
  ]);
  const status = element("p", {
    class: "doc-status",
    "data-testid": "doc-status",
    text: "Saved",
  });
  const revisionLine = element("span", {
    class: "doc-revision",
    "data-testid": "doc-revision",
    text: draft.baseRevision || "unknown revision",
  });
  const errorLine = element("p", {
    class: "doc-error",
    "data-testid": "doc-error",
    role: "alert",
    hidden: true,
  });
  const saveButton = element("button", {
    type: "button",
    class: "doc-save",
    "data-testid": "doc-save",
    text: "Save",
  });
  saveButton.disabled = true;
  saveButton.addEventListener("click", () => submit());

  const conflict = conflictPanel();
  const root = element("div", { class: "doc-editor", "data-testid": "doc-editor" });
  root.append(
    element("div", { class: "doc-toolbar", "data-testid": "doc-toolbar" }, [
      saveButton,
      revisionLine,
      status,
    ]),
    surface,
    errorLine,
    conflict.root
  );

  function setContent(value) {
    draft.content = typeof value === "string" ? value : "";
    draft.dirty = draft.content !== draft.saved;
    saveButton.disabled = !draft.dirty;
    return draft.dirty;
  }

  /**
   * The ir host's live document becomes the draft (its markdown
   * serialization is the editable document; a normalised mount differs from
   * the raw file baseline, which is exactly what the save gate compares).
   */
  function hostDraft(value) {
    touched = true;
    draft.content = typeof value === "string" ? value : "";
    source.value = draft.content;
    draft.dirty = draft.content !== draft.saved;
    saveButton.disabled = !draft.dirty;
    return draft.dirty;
  }

  /** Adopt the host's mount-time serialization unless the user typed first. */
  function adoptHostValue(value) {
    if (touched) return false;
    return hostDraft(value);
  }

  function setRevision(revision_) {
    draft.baseRevision = revision_ || "";
    revisionLine.textContent = draft.baseRevision || "unknown revision";
    return draft.baseRevision;
  }

  function replaceContent(next, revision_) {
    draft.saved = typeof next === "string" ? next : "";
    draft.content = draft.saved;
    source.value = draft.content;
    setRevision(revision_);
    draft.dirty = false;
    saveButton.disabled = true;
    return draft.content;
  }

  async function submit() {
    try {
      const result = await postJson(url, {
        base_revision: draft.baseRevision,
        content: draft.content,
      });
      draft.saved = draft.content;
      draft.baseRevision =
        result && result.new_revision ? result.new_revision : draft.baseRevision;
      setRevision(draft.baseRevision);
      draft.dirty = false;
      saveButton.disabled = true;
      status.textContent = "Saved";
      errorLine.hidden = true;
      conflict.hide();
      return result;
    } catch (error) {
      if (error && error.status === 409) {
        conflict.show(conflictRevision(error));
        return null;
      }
      // Write failure: the local draft stays in place and is retryable.
      status.textContent = "Save failed";
      errorLine.textContent = `Save failed: ${error && error.message ? error.message : error}`;
      errorLine.hidden = false;
      return null;
    }
  }

  function conflictRevision(error) {
    const payload = error ? error.payload : null;
    if (!payload) return draft.baseRevision;
    // The top-level ``current_revision`` is the authoritative shape (§1o.2);
    // the nested ``error.current_revision`` spelling is tolerated so a
    // gateway that rewraps the 409 body still feeds the recovery options.
    return (
      payload.current_revision ||
      (payload.error && payload.error.current_revision) ||
      draft.baseRevision
    );
  }

  async function reloadDiscard() {
    const currentRevision = conflict.revision() || draft.baseRevision;
    const server = reload ? await reload(currentRevision).catch(() => null) : null;
    const next = server && typeof server.content === "string" ? server.content : draft.saved;
    draft.saved = next;
    draft.content = next;
    source.value = next;
    draft.baseRevision =
      server && server.revision ? server.revision : currentRevision;
    setRevision(draft.baseRevision);
    draft.dirty = false;
    saveButton.disabled = true;
    conflict.hide();
    errorLine.hidden = true;
    status.textContent = "Reloaded";
    return next;
  }

  function forceOverwrite() {
    draft.baseRevision = conflict.revision() || draft.baseRevision;
    setRevision(draft.baseRevision);
    conflict.hide();
    return submit();
  }

  function conflictPanel() {
    let liveRevision = "";
    const revisionLabel = element("span", {
      class: "conflict-revision",
      "data-testid": "conflict-revision",
    });
    const reloadButton = element("button", {
      type: "button",
      class: "conflict-reload",
      "data-testid": "conflict-reload",
      text: "Reload and discard",
    });
    const overwriteButton = element("button", {
      type: "button",
      class: "conflict-overwrite",
      "data-testid": "conflict-overwrite",
      text: "Force overwrite",
    });
    reloadButton.addEventListener("click", () => reloadDiscard());
    overwriteButton.addEventListener("click", () => forceOverwrite());
    const panel = element(
      "div",
      { class: "doc-conflict", "data-testid": "conflict-dialog", hidden: true },
      [
        element("p", {
          class: "conflict-message",
          text: "This document changed on the server. Choose how to continue.",
        }),
        revisionLabel,
        reloadButton,
        overwriteButton,
      ]
    );
    return {
      root: panel,
      revision: () => liveRevision,
      show(revision) {
        liveRevision = revision || "";
        revisionLabel.textContent = `server revision: ${liveRevision}`;
        panel.hidden = false;
      },
      hide() {
        panel.hidden = true;
      },
    };
  }

  return {
    root,
    surface,
    irHost,
    source,
    setContent,
    hostDraft,
    adoptHostValue,
    setRevision,
    replaceContent,
    submit,
    reloadDiscard,
    forceOverwrite,
    draft,
  };
}
