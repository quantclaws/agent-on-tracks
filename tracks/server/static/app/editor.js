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

  const surface = element("div", { class: "doc-surface", "data-testid": "doc-surface" });
  const status = element("p", { class: "doc-status", "data-testid": "doc-status", text: "Saved" });
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

  async function submit() {
    try {
      const result = await postJson(url, {
        base_revision: draft.baseRevision,
        content: draft.content,
      });
      draft.saved = draft.content;
      draft.baseRevision = result && result.new_revision ? result.new_revision : draft.baseRevision;
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
    return payload && payload.current_revision ? payload.current_revision : draft.baseRevision;
  }

  async function reloadDiscard() {
    const currentRevision = conflict.revision() || draft.baseRevision;
    const server = reload ? await reload(currentRevision) : null;
    const next = server && typeof server.content === "string" ? server.content : draft.saved;
    draft.saved = next;
    draft.content = next;
    draft.baseRevision =
      server && server.revision ? server.revision : currentRevision;
    draft.dirty = false;
    saveButton.disabled = true;
    conflict.hide();
    errorLine.hidden = true;
    status.textContent = "Reloaded";
    return next;
  }

  function forceOverwrite() {
    draft.baseRevision = conflict.revision() || draft.baseRevision;
    conflict.hide();
    return submit();
  }

  function conflictPanel() {
    let liveRevision = "";
    const revisionLabel = element("span", {
      class: "conflict-revision",
      "data-testid": "doc-conflict-revision",
    });
    const reloadButton = element("button", {
      type: "button",
      class: "conflict-reload",
      "data-testid": "doc-reload-discard",
      text: "Reload and discard",
    });
    const overwriteButton = element("button", {
      type: "button",
      class: "conflict-overwrite",
      "data-testid": "doc-force-overwrite",
      text: "Force overwrite",
    });
    reloadButton.addEventListener("click", () => reloadDiscard());
    overwriteButton.addEventListener("click", () => forceOverwrite());
    const panel = element(
      "div",
      { class: "doc-conflict", "data-testid": "doc-conflict", hidden: true },
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
        revisionLabel.textContent = liveRevision;
        panel.hidden = false;
      },
      hide() {
        panel.hidden = true;
      },
    };
  }

  return { root, surface, setContent, submit, reloadDiscard, forceOverwrite, draft };
}