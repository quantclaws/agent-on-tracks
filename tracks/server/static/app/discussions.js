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
  let hidden = true;
  let index = -1;
  let items = Array.isArray(threads) ? threads.slice() : [];

  const list = element("ul", { class: "discussion-list", "data-testid": "discussion-list" });
  const toggleButton = element("button", {
    type: "button",
    class: "discussion-toggle",
    "data-testid": "discussions-toggle",
    text: "Discussions",
  });
  const nextButton = element("button", {
    type: "button",
    class: "discussion-next",
    "data-testid": "discussions-next",
    text: "Next",
  });
  const unresolvedButton = element("button", {
    type: "button",
    class: "discussion-unresolved",
    "data-testid": "discussions-unresolved",
    text: "Unresolved only",
  });
  const root = element("aside", { class: "discussions", "data-testid": "discussions", hidden: true }, [
    element("div", { class: "discussion-controls" }, [
      toggleButton,
      nextButton,
      unresolvedButton,
    ]),
    list,
  ]);

  toggleButton.addEventListener("click", () => toggle());
  nextButton.addEventListener("click", () => nextThread());
  unresolvedButton.addEventListener("click", () => renderList(unresolvedOnly()));

  function toggle() {
    hidden = !hidden;
    root.hidden = hidden;
    return hidden;
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
        element(
          "li",
          {
            class: "discussion-item",
            "data-testid": `discussion-${id}`,
            "data-entry-line": thread.entry_line === undefined ? "" : String(thread.entry_line),
            "aria-current": thread.thread_id === activeId ? "true" : "false",
          },
          [
            element("span", {
              class: "discussion-label",
              text: `${id} - ${thread.status || "unknown"}`,
            }),
          ]
        )
      );
    }
  }

  renderList(items);
  return { root, toggle, nextThread, unresolvedOnly, setThreads };
}