/**
 * Auto-save + conflict recovery (IF-DOCSAVE-001 client face; §1o.2; 2026-10-09
 * operator directive: icon-only save control living in the pane bar, autosave
 * after edits, hidden revision face, front-matter hidden from the editor).
 *
 * The save control is an icon button that is disabled while the draft matches
 * the server revision (saved) and enabled while it differs; a user edit
 * schedules an automatic save, and the completed save disables the control
 * again — the button state IS the saved indicator. A 409 carries the live
 * token as the response body's top-level ``current_revision``; the editor
 * offers exactly two options — reload and discard (server content +
 * ``current_revision`` become the baseline) or force overwrite (the local
 * draft is resubmitted with ``current_revision`` as the new
 * ``base_revision``). It never overwrites silently, and any other write
 * failure keeps the local draft retryable.
 *
 * The sealed envelope front-matter never reaches the editable face: the
 * document is split into (head, body) on load, the surfaces edit the body,
 * and a save submits head + body — a no-change round trip reproduces the
 * file byte for byte, so hiding the head can never rewrite it.
 *
 * Content surfaces: the authoritative draft is the markdown source area
 * (``doc-editor-content``); the Vditor ir host renders it live (§1n.3) and a
 * load failure swaps in the ``doc-editor-fallback`` textarea — the document
 * stays viewable and editable in every path.
 */

import { postJson } from "./api.js";
import { element } from "./dom.js";

// Operator directive (2026-10-09): edits save themselves — no submit gesture
// required. The debounce keeps a burst of typing on one request.
const AUTOSAVE_DELAY_MS = 800;

/** (head incl. delimiters, body); a headless document round-trips as-is. */
function splitFrontmatter(text) {
  if (typeof text !== "string" || !text.startsWith("---")) {
    return { head: "", body: text };
  }
  const match = /^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/.exec(text);
  return match
    ? { head: match[0], body: text.slice(match[0].length) }
    : { head: "", body: text };
}

export function createEditor({ url, revision, content, reload } = {}) {
  const initial = splitFrontmatter(content || "");
  const draft = {
    head: initial.head,
    content: initial.body,
    saved: initial.body,
    baseRevision: revision || "",
    dirty: false,
  };

  // The Vditor ir mount host (§1n.3): assets load on demand, same-origin.
  const irHost = element("div", {
    class: "vditor-host",
    "data-testid": "vditor-ir-host",
  });
  // The markdown source area — the authoritative editable draft (the body;
  // the sealed front-matter head never appears on any editable surface).
  const source = element("textarea", {
    class: "doc-source",
    "data-testid": "doc-editor-content",
    rows: 10,
    spellcheck: "false",
  });
  source.value = draft.content;
  source.addEventListener("input", () => {
    setContent(source.value);
  });

  const surface = element("div", { class: "doc-surface", "data-testid": "doc-surface" }, [
    irHost,
    source,
  ]);
  // The revision is the optimistic-concurrency baseline (base_revision) —
  // internal bookkeeping, not a user-facing fact (operator directive
  // 2026-10-09). The element stays in the tree (hidden) with the full sha in
  // its tooltip so support and the ui contract can still read it.
  const revisionLine = element("span", {
    class: "doc-revision",
    "data-testid": "doc-revision",
    title: draft.baseRevision || "",
    text: shortRevision(draft.baseRevision),
    hidden: true,
  });
  const errorLine = element("p", {
    class: "doc-error",
    "data-testid": "doc-error",
    role: "alert",
    hidden: true,
  });
  // The save control is hosted by the pane bar (merge directive): an icon
  // button whose enabled/disabled state is the saved indicator. It starts
  // disabled — a freshly opened document is saved by definition.
  const saveButton = element("button", {
    type: "button",
    class: "doc-save",
    "data-testid": "doc-save",
    "aria-label": "保存",
    title: "已保存",
    text: "⤓",
  });
  saveButton.disabled = true;
  saveButton.addEventListener("click", () => submit());

  const conflict = conflictPanel();
  const root = element("div", { class: "doc-editor", "data-testid": "doc-editor" });
  root.append(surface, revisionLine, errorLine, conflict.root);

  let autosaveTimer = null;
  // The in-flight save (null when idle) — single-writer channel: a second
  // submit while one is airborne requeues instead of stacking.
  let flight = null;
  // Set when the editor leaves its pane: a torn-down editor never re-arms
  // autosave (a stray POST landing on a closed surface could open its
  // conflict/error face on detached DOM — Prism round-2 R2-A1).
  let destroyed = false;

  function shortRevision(revision) {
    return revision ? revision.slice(0, 12) : "unknown revision";
  }

  function refreshSaveFace() {
    saveButton.disabled = !draft.dirty;
    saveButton.setAttribute("title", draft.dirty ? "保存更改" : "已保存");
    saveButton.setAttribute("aria-label", draft.dirty ? "保存更改" : "已保存");
  }

  function conflictOpen() {
    return !conflict.root.hidden;
  }

  function scheduleAutosave() {
    if (destroyed) return; // the surface is gone — nothing may re-arm here
    if (autosaveTimer) globalThis.clearTimeout(autosaveTimer);
    autosaveTimer = globalThis.setTimeout(() => {
      autosaveTimer = null;
      // never behind the user's back: a visible conflict waits for a
      // decision, and a clean draft has nothing to send.
      if (!draft.dirty || conflictOpen()) return;
      if (flight) {
        scheduleAutosave(); // the airborne save owns the channel — retry after it lands
        return;
      }
      submit();
    }, AUTOSAVE_DELAY_MS);
  }

  function applyDraft(value) {
    draft.content = typeof value === "string" ? value : "";
    source.value = draft.content;
    draft.dirty = draft.content !== draft.saved;
    refreshSaveFace();
    return draft.dirty;
  }

  /** A user edit in the source area — dirty-gates and arms the autosave. */
  function setContent(value) {
    const dirty = applyDraft(value);
    if (dirty) scheduleAutosave();
    return dirty;
  }

  /**
   * A live user edit in the ir host — its markdown serialization becomes
   * the draft (the host is the editable face, so host edits autosave too).
   */
  function hostDraft(value) {
    const dirty = applyDraft(value);
    if (dirty) scheduleAutosave();
    return dirty;
  }

  function setRevision(revision_) {
    draft.baseRevision = revision_ || "";
    revisionLine.textContent = shortRevision(draft.baseRevision);
    revisionLine.setAttribute("title", draft.baseRevision || "");
    return draft.baseRevision;
  }

  function replaceContent(next, revision_) {
    const split = splitFrontmatter(typeof next === "string" ? next : "");
    draft.head = split.head;
    applyDraft(split.body);
    draft.saved = draft.content;
    draft.dirty = false;
    refreshSaveFace();
    setRevision(revision_);
    return draft.content;
  }

  async function submit() {
    if (autosaveTimer) {
      globalThis.clearTimeout(autosaveTimer);
      autosaveTimer = null;
    }
    if (flight) {
      scheduleAutosave(); // the airborne save owns the channel; the draft requeues
      return null;
    }
    const run = (async () => {
      // Snapshot at send time: the response can only vouch for what was
      // actually posted — typing that raced the flight must survive as
      // dirty, never be marked saved by the landing response.
      const sentBody = draft.content;
      try {
        const result = await postJson(url, {
          base_revision: draft.baseRevision,
          // the sealed head rides along verbatim — the editor only ever
          // edited the body.
          content: draft.head + sentBody,
        });
        draft.baseRevision =
          result && result.new_revision ? result.new_revision : draft.baseRevision;
        setRevision(draft.baseRevision);
        draft.saved = sentBody;
        draft.dirty = draft.content !== draft.saved;
        refreshSaveFace();
        errorLine.hidden = true;
        conflict.hide();
        if (draft.dirty) scheduleAutosave(); // typing raced the flight
        return result;
      } catch (error) {
        if (error && error.status === 409) {
          conflict.show(conflictRevision(error));
          return null;
        }
        // Write failure: the local draft stays in place and is retryable.
        errorLine.textContent = `Save failed: ${error && error.message ? error.message : error}`;
        errorLine.hidden = false;
        return null;
      }
    })();
    flight = run.finally(() => {
      flight = null;
    });
    return flight;
  }

  /**
   * The document is leaving its pane (another document loads, the pane
   * closes). An unresolved conflict blocks the caller's switch (the panel
   * is alive on the still-mounted editor); otherwise any airborne save is
   * waited out and a still-dirty draft flushes now. Resolves null when the
   * outgoing edit is NOT safely on the server — the caller must keep this
   * editor mounted so the conflict/error faces stay visible.
   */
  async function destroy() {
    if (destroyed) return { flushed: false };
    destroyed = true;
    if (conflictOpen()) return null;
    if (autosaveTimer) {
      globalThis.clearTimeout(autosaveTimer);
      autosaveTimer = null;
    }
    while (flight) await flight.catch(() => null);
    if (conflictOpen()) return null; // the waited flight hit a 409
    if (!draft.dirty) return { flushed: false };
    return submit();
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
    const next =
      server && typeof server.content === "string" ? server.content : draft.head + draft.saved;
    replaceContent(next, server && server.revision ? server.revision : currentRevision);
    conflict.hide();
    errorLine.hidden = true;
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
      text: "重载服务端版本（丢弃本地修改）",
    });
    const overwriteButton = element("button", {
      type: "button",
      class: "conflict-overwrite",
      "data-testid": "conflict-overwrite",
      text: "用本地修改覆盖服务端",
    });
    reloadButton.addEventListener("click", () => reloadDiscard());
    overwriteButton.addEventListener("click", () => forceOverwrite());
    const panel = element(
      "div",
      { class: "doc-conflict", "data-testid": "conflict-dialog", hidden: true },
      [
        element("p", {
          class: "conflict-message",
          text: "文档已在服务器上更新。请选择继续方式：",
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
        panel.classList.add("conflict-attention");
        if (typeof panel.scrollIntoView === "function") {
          try {
            panel.scrollIntoView({ block: "nearest" });
          } catch (error) {
            // scroll hint only — the panel is already unhidden
          }
        }
      },
      hide() {
        panel.hidden = true;
        panel.classList.remove("conflict-attention");
      },
    };
  }

  /**
   * The caret line's text (§1n.3 anchor form): the textarea answers from
   * its selectionStart; a selection inside the ir host answers the
   * anchor's own block text. Null when nothing resolvable is focused —
   * the composer then refuses to publish rather than anchor blindly.
   */
  function selectionLineText() {
    if (document.activeElement === source && typeof source.selectionStart === "number") {
      const upto = source.value.slice(0, source.selectionStart);
      const lines = source.value.split("\n");
      const lineNo = upto.split("\n").length - 1;
      const line = lines[lineNo];
      return typeof line === "string" && line.trim() ? line : null;
    }
    const selection = globalThis.getSelection ? globalThis.getSelection() : null;
    const node = selection && selection.anchorNode ? selection.anchorNode : null;
    if (!node) return null;
    const block = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    const host = block && block.closest ? block.closest('[data-testid="vditor-ir-host"]') : null;
    if (!host) return null;
    const text = node.textContent && node.textContent.trim();
    return text || null;
  }

  return {
    root,
    surface,
    irHost,
    source,
    saveControl: saveButton,
    setContent,
    hostDraft,
    setRevision,
    replaceContent,
    submit,
    destroy,
    conflictOpen,
    reloadDiscard,
    forceOverwrite,
    selectionLineText,
    draft,
  };
}
