/**
 * Read-only discussion overlay (IF-DISCUSS-001; interfaces §1p).
 *
 * The server parses the tracks discussion protocol into a thread read model
 * (``entry_line`` is the navigation anchor); the client only offers the three
 * navigation capabilities — show/hide toggle, next discussion, unresolved-only
 * filter. There is no resolve/reply write entry: write-back stays with the CLI
 * and no discussion mutation request is ever issued from the browser.
 */

import { element } from "./dom.js";

export function createDiscussionNav({ threads = [], onNavigate } = {}) {
  let open = false;
  let index = -1;
  let items = Array.isArray(threads) ? threads.slice() : [];
  let overlay = null;

  const list = element("ul", {
    class: "discussion-list",
    "data-testid": "discussion-list",
  });
  const toggleButton = element("button", {
    type: "button",
    class: "discussion-toggle",
    "data-testid": "discussion-toggle",
    text: "Discussions",
  });
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
    toggleButton,
    nextButton,
    unresolvedButton,
  ]);

  toggleButton.addEventListener("click", () => toggle());
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
  return { controls, toggle, nextThread, unresolvedOnly, setThreads };
}
