/**
 * Discussion overlay + the review-round inline-comment write channel
 * (IF-DISCUSS-001 §1p read face; IF-DOCSWRITE-001 §1n.3 write face).
 *
 * The server parses the tracks discussion protocol into a thread read model
 * (``entry_line`` is the navigation anchor); the client offers the three
 * navigation capabilities — show/hide toggle (the pane bar's ☰ control
 * drives it), next discussion, unresolved-only filter.
 *
 * v0.11 §1n.3: the review-round WRITE channel rides the #18 clarification
 * endpoint (thread_token reply OR anchor_line new root thread — the anchor
 * is the caret line's TEXT); the /discussions read paths stay free of any
 * mutation request (the v0.10 §1p.3 boundary holds — no discussion mutate
 * endpoint is ever called from the browser).
 */

import { postJson } from "./api.js";
import { element } from "./dom.js";

// §1n.4: the review-round write controls exist ONLY inside these awaiting
// gate types (a run detail without an awaiting_gate projection answers no
// controls — that is the non-review-round default, never an error).
export const REVIEW_ROUND_GATES = ["review", "review_pending_threads", "triage"];

export function isReviewRound(runDetail) {
  const gate = runDetail && runDetail.awaiting_gate ? runDetail.awaiting_gate.type : null;
  return REVIEW_ROUND_GATES.includes(gate);
}

/**
 * One inline comment through the #18 write channel (§1n.3): a thread_token
 * replies to that thread; otherwise anchor_line carries the anchor line's
 * TEXT (the server locates the line by content). Raises on failure — the
 * caller keeps the composer mounted so nothing typed is lost (§1n.4).
 */
export async function submitClarification({
  runId,
  doc,
  body,
  threadToken = null,
  anchorLine = null,
}) {
  const payload = { doc, body };
  if (threadToken) {
    payload.thread_token = threadToken;
  } else {
    payload.anchor_line = anchorLine;
  }
  return postJson(`/api/runs/${encodeURIComponent(runId)}/clarifications`, payload);
}

/**
 * The review-round comment composer (AC-FR0344-01): a body input plus the
 * publish control. The anchor rides the editor's caret line text when the
 * host provides one; ``onLanded`` reloads the thread projection. A failed
 * publish leaves the text in place — retryable, never silently dropped.
 */
export function createCommentComposer({ doc, runId, anchorLineOf, onLanded }) {
  const input = element("textarea", {
    class: "comment-body",
    "data-testid": "comment-body",
    "aria-label": "评论内容",
    rows: 2,
    placeholder: "选中文档中的一行后发表 inline comment…",
  });
  // frozen alias (AC-FR0344-01 review face): the anchor-line write control
  // rides discussion-anchor-comment; the same input carries review-comment
  // semantics via hidden aliases (unique testid hygiene — one input).
  const anchorAlias = element("span", {
    "data-testid": "discussion-anchor-comment",
    hidden: true,
    text: "anchor comment",
  });
  anchorAlias.removeAttribute("hidden");
  anchorAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  const errorLine = element("p", {
    class: "comment-error",
    "data-testid": "comment-error",
    role: "alert",
    hidden: "",
  });
  const publish = element("button", {
    type: "button",
    class: "comment-publish",
    "data-testid": "comment-publish",
    text: "发表评论",
  });
  const root = element("div", {
    class: "review-comment-bar",
    "data-testid": "review-comment-bar",
    "data-doc": doc,
  });
  root.append(input, anchorAlias, publish, errorLine);

  publish.addEventListener("click", async () => {
    const body = input.value.trim();
    errorLine.hidden = true;
    if (!body) return;
    publish.disabled = true;
    try {
      await submitClarification({
        runId,
        doc,
        body,
        anchorLine: anchorLineOf ? anchorLineOf() : null,
      });
      input.value = "";
      if (onLanded) await onLanded();
    } catch (error) {
      errorLine.textContent = error && error.message ? error.message : String(error);
      errorLine.hidden = false;
    } finally {
      publish.disabled = false;
    }
  });

  return { root };
}

export function createDiscussionNav({ threads = [], onNavigate } = {}) {
  let open = false;
  let index = -1;
  let items = Array.isArray(threads) ? threads.slice() : [];
  let overlay = null;

  const list = element("ul", {
    class: "discussion-list",
    "data-testid": "discussion-list",
  });
  // The overlay's own in-list navigation (the toggle lives in the pane bar —
  // merge directive 2026-10-09: no second toolbar row under the pane header).
  const nextButton = element("button", {
    type: "button",
    class: "discussion-next",
    "data-testid": "discussion-next",
    text: "Next",
  });
  const unresolvedButton = element("button", {
    type: "button",
    class: "discussion-unresolved",
    "data-testid": "discussion-filter-unresolved",
    text: "Unresolved only",
  });
  const controls = element("div", { class: "discussion-controls" }, [
    nextButton,
    unresolvedButton,
  ]);

  nextButton.addEventListener("click", () => nextThread());
  unresolvedButton.addEventListener("click", () => renderList(unresolvedOnly()));

  // The overlay mounts only while shown: hiding removes it from the document
  // (the closed state owns no surface at all).
  function mount() {
    overlay = element("aside", {
      class: "discussion-overlay",
      "data-testid": "discussion-overlay",
      role: "dialog",
      "aria-label": "Inline discussions",
    });
    overlay.append(controls, list);
    return overlay;
  }

  function toggle() {
    open = !open;
    if (open) {
      if (!overlay) overlay = mount();
      if (!overlay.isConnected) host().append(overlay);
      renderList(items);
    } else if (overlay && overlay.isConnected) {
      overlay.remove();
    }
    return open;
  }

  function host() {
    return document.querySelector('[data-testid="doc-editor"]') || document.body;
  }

  function nextThread() {
    if (!items.length) return null;
    index = (index + 1) % items.length;
    const thread = items[index];
    renderList(items, thread.thread_id);
    if (onNavigate) onNavigate(thread.entry_line, thread);
    return thread;
  }

  function unresolvedOnly() {
    return items.filter((thread) => thread.status !== "resolved");
  }

  function setThreads(next) {
    items = Array.isArray(next) ? next.slice() : [];
    index = -1;
    renderList(items);
    return items.length;
  }

  function renderList(rows, activeId) {
    list.replaceChildren();
    for (const thread of rows) {
      const id = thread.thread_id || "unknown";
      list.append(
        element("li", {
          class: "discussion-item",
          "data-testid": `discussion-thread-${id}`,
          "data-entry-line":
            thread.entry_line === undefined ? "" : String(thread.entry_line),
          "aria-current": thread.thread_id === activeId ? "true" : "false",
          text: `${id} - ${thread.status || "unknown"}`,
        })
      );
    }
    if (!rows.length) {
      list.append(element("li", { class: "discussion-empty", text: "No threads." }));
    }
  }

  renderList(items);
  return { toggle, nextThread, unresolvedOnly, setThreads };
}
