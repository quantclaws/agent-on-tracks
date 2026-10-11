/**
 * Functional-area views (interfaces §1l.3, §1l.4; §1e.1 five-tab world):
 * the tab bar's five items map onto these surfaces. Every view consumes
 * the live query projections (interfaces §1l.6 — no mock data) and
 * renders an observable failure marker (`api-failure`) when a request
 * fails and a readable degradation marker (`degrade-NotFound`) when a
 * value is unknown. The main area title shows the content title, never
 * the tab-bar category name.
 *
 * v0.11 (FR-0334): the functional world is releases/docs/todos/settings/
 * account — the projects/runs registries merge into the Releases window
 * (rendered by releases.js, loaded on demand) and the Review placeholder
 * is gone: stage reviews are carried by the awaiting action panel on the
 * timeline's fixed detail pane. Run-scoped routes render the timeline
 * view directly.
 */

import { requestJson } from "./api.js";
import { degradedMessage, element, loadInto } from "./dom.js";
import { renderDocsView } from "./docs.js";
import { renderTimelineView } from "./timeline.js";

const VIEW_RENDERERS = {
  releases: renderReleasesWindow,
  docs: renderDocsView,
  todos: renderTodos,
  settings: renderSettings,
  account: renderAccount,
  run_detail: renderTimelineView,
  release: renderTimelineView,
};

// #227 (v0.10 ruling, NFR-0158-02): registry rows stay compact index
// entries — the full title stays on the main area's face, so a row label
// keeps at most 20 characters and a run without a story title (the
// projection falls back to the raw ULID run id, e.g. hotfix runs) shows
// the bare version instead of id noise.
const RUN_ROW_MAX_CHARS = 20;

export function runRowLabel(run) {
  const version = run.version || "unknown";
  if (!run.title || run.title === run.run_id) return version;
  const chars = Array.from(`${version} · ${run.title}`);
  if (chars.length <= RUN_ROW_MAX_CHARS) return chars.join("");
  return `${chars.slice(0, RUN_ROW_MAX_CHARS).join("")}...`;
}

/** The Releases registry's two compact faces (#227): the sidebar nodes of
 * the version-grouped registry and the main-area row elements. releases.js
 * renders the window and consumes these through this seam — the releases
 * view loads on demand below, so the import stays one-directional. */
export function runRegistryFaces(runs) {
  const nodes = runs.map((run) => ({
    label: runRowLabel(run),
    testid: `sidebar-run-${run.run_id || "unknown"}`,
    href: "#",
    attrs: {
      "data-run-id": run.run_id || "",
      "data-version": run.version || "",
    },
  }));
  const rows = runs.map((run) =>
    element("li", {
      class: "run-row",
      "data-testid": `registry-run-${run.run_id || "unknown"}`,
      "data-run-id": run.run_id || "",
      text: runRowLabel(run),
    })
  );
  return { nodes, rows };
}

async function renderReleasesWindow(body, context = {}) {
  const { renderReleasesView } = await import("./releases.js");
  return renderReleasesView(body, context);
}

export function renderView(route, mainArea, context = {}) {
  if (!mainArea) return null;
  const view = context.view || route;
  const host =
    mainArea.querySelector('[data-testid="main-body"]') ||
    mainArea.appendChild(
      element("section", { class: "main-body", "data-testid": "main-body" })
    );
  // A fresh body element per activation: a slower previous loader appends to
  // its detached body, never into the newly mounted view.
  for (const previous of Array.from(host.children)) previous.remove();
  const body = element("section", {
    class: "view",
    "data-testid": `view-${view || "unknown"}`,
  });
  host.append(body);
  const renderer = VIEW_RENDERERS[view];
  let done = Promise.resolve();
  if (!renderer) {
    body.append(degradedMessage(`Unknown view: ${route || "no route"}`));
  } else {
    done = Promise.resolve()
      .then(() => renderer(body, context))
      .then(() => undefined)
      .catch((error) => {
        body.append(
          degradedMessage(
            `View failed: ${error && error.message ? error.message : error}`
          )
        );
      });
  }
  return { body, done };
}

function renderTodos(body) {
  return loadInto(body, async () => {
    const todos = await requestJson("/api/todos");
    const list = element("ul", { class: "todo-list", "data-testid": "todo-list" });
    for (const todo of Array.isArray(todos) ? todos : []) {
      list.append(
        element("li", {
          class: "todo-row",
          "data-testid": `todo-${todo.run_id || "unknown"}`,
          text: `${todo.run_id || "unknown"}: ${todo.reason || todo.kind || "unknown"}`,
        })
      );
    }
    if (!list.children.length) list.append(element("li", { text: "No human todos." }));
    body.append(list);
  });
}

function renderSettings(body) {
  body.append(
    element("p", {
      class: "settings-hint",
      "data-testid": "settings-surface",
      text: "Settings (no runtime controls this version).",
    })
  );
  return null;
}

function renderAccount(body) {
  return loadInto(body, async () => {
    const profile = await requestJson("/api/auth/profile");
    const menu = element("div", {
      class: "account-menu",
      "data-testid": "account-menu",
    });
    menu.append(
      element("p", {
        class: "account-name",
        "data-testid": "account-name",
        text: profile.actor || "unknown",
      })
    );
    const logout = element("button", {
      type: "button",
      class: "account-logout",
      "data-testid": "account-logout",
      text: "Log out",
    });
    logout.addEventListener("click", async () => {
      try {
        await requestJson("/api/auth/logout", {
          method: "POST",
          body: JSON.stringify({}),
        });
      } finally {
        globalThis.location.assign("/login");
      }
    });
    menu.append(logout);
    body.append(menu);
  });
}
