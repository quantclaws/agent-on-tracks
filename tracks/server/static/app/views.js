/**
 * Functional-area views (interfaces §1l.3, §1l.4): the tab bar's seven items
 * map onto these surfaces. Every view consumes the live query projections
 * (interfaces §1l.6 — no mock data) and renders an observable failure marker
 * (`api-failure`) when a request fails and a readable degradation marker
 * (`degrade-NotFound`) when a value is unknown. The main area title shows the
 * content title, never the tab-bar category name.
 */

import { requestJson } from "./api.js";
import { degradedMessage, element, loadInto } from "./dom.js";
import { renderSidebar } from "./sidebar.js";
import { renderDocsView } from "./docs.js";
import { renderTimelineView } from "./timeline.js";
import { openTab } from "./tabs.js";

const VIEW_RENDERERS = {
  projects: renderProjects,
  runs: renderRuns,
  docs: renderDocsView,
  review: renderReview,
  todos: renderTodos,
  settings: renderSettings,
  account: renderAccount,
  run_detail: renderTimelineView,
};

// #227: registry rows are compact index entries — the full title stays on the
// main area's face, so a row label keeps at most 20 characters and a run
// without a story title (the projection falls back to the raw ULID run id,
// e.g. hotfix runs) shows the bare version instead of id noise.
const RUN_ROW_MAX_CHARS = 20;

function runRowLabel(run) {
  const version = run.version || "unknown";
  if (!run.title || run.title === run.run_id) return version;
  const chars = Array.from(`${version} · ${run.title}`);
  if (chars.length <= RUN_ROW_MAX_CHARS) return chars.join("");
  return `${chars.slice(0, RUN_ROW_MAX_CHARS).join("")}...`;
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

/** The registered projects + their live runs (overview projection, §1e). */
async function collectOverviewRows(body, { withRuns = true } = {}) {
  const projects = await requestJson("/api/projects");
  const rows = Array.isArray(projects) ? projects : projects.projects || [];
  const list = element("ul", { class: "project-list", "data-testid": "overview-list" });
  for (const project of rows) {
    const id = project.project_id || "unknown";
    list.append(
      element("li", {
        class: "project-row",
        "data-testid": `overview-project-${id}`,
        text: `${id} · ${project.version || "unknown"}`,
      })
    );
  }
  body.append(list);
  if (!rows.length) {
    // §1l.5: an empty registry degrades readably — never a blank surface.
    body.append(degradedMessage("No registered projects yet"));
  }
  if (!withRuns) return rows;
  let failed = false;
  for (const project of rows) {
    const id = project.project_id || "unknown";
    try {
      const overview = await requestJson(
        `/api/projects/${encodeURIComponent(id)}/overview`
      );
      const runList = element("ul", { class: "run-list", "data-testid": "overview-runs" });
      for (const run of overview.runs || []) {
        runList.append(
          element("li", {
            class: "run-row",
            "data-testid": `overview-run-${run.run_id || "unknown"}`,
            text: runRowLabel(run),
          })
        );
      }
      body.append(runList);
    } catch (error) {
      failed = true;
    }
  }
  if (failed) {
    body.append(
      element("p", {
        class: "request-error",
        "data-testid": "api-failure",
        role: "alert",
        text: "The live run projection is unavailable right now.",
      })
    );
  }
  return rows;
}

function renderProjects(body) {
  return loadInto(body, async () => {
    await collectOverviewRows(body);
    const sidebarNodes = [];
    for (const row of body.querySelectorAll(".project-row")) {
      const id = (row.getAttribute("data-testid") || "").replace(
        "overview-project-",
        ""
      );
      sidebarNodes.push({ label: id, testid: `sidebar-project-${id}` });
    }
    if (sidebarNodes.length) {
      renderSidebar({ title: "projects", nodes: sidebarNodes });
    }
  });
}

function renderRuns(body, context = {}) {
  return loadInto(body, async () => {
    const projects = await requestJson("/api/projects");
    const rows = Array.isArray(projects) ? projects : projects.projects || [];
    const runs = [];
    for (const project of rows) {
      const id = project.project_id || "unknown";
      try {
        const overview = await requestJson(
          `/api/projects/${encodeURIComponent(id)}/overview`
        );
        for (const run of overview.runs || []) runs.push({ ...run, projectId: id });
      } catch (error) {
        body.append(
          element("p", {
            class: "request-error",
            "data-testid": "api-failure",
            role: "alert",
            text: `The live run projection for ${id} is unavailable right now.`,
          })
        );
      }
    }
    // The overview face keeps the registered projects visible (the landing
    // route merges Projects/Runs overview semantics — §1l.2); the run
    // registry itself lives in the sidebar below.
    const projectList = element("ul", {
      class: "project-list",
      "data-testid": "overview-list",
    });
    for (const project of rows) {
      const id = project.project_id || "unknown";
      projectList.append(
        element("li", {
          class: "project-row",
          "data-testid": `overview-project-${id}`,
          text: `${id} · ${project.version || "unknown"}`,
        })
      );
    }
    body.append(projectList);

    // Newest first: the registry reads top-down as a history, and the main
    // area opens on the newest run's stage track.
    const newestFirst = [...runs].sort((a, b) =>
      String(b.updated_ts || "").localeCompare(String(a.updated_ts || ""))
    );

    // The Runs area's subtree is the run registry itself (§1l.3: the sidebar
    // shows the active item's children); a node opens that run's tab.
    const sidebar = renderSidebar({
      title: "runs",
      nodes: newestFirst.map((run) => ({
        label: runRowLabel(run),
        testid: `sidebar-run-${run.run_id || "unknown"}`,
      })),
    });
    if (sidebar) {
      for (const anchor of sidebar.querySelectorAll('[data-testid^="sidebar-run-"]')) {
        anchor.addEventListener("click", (event) => {
          event.preventDefault();
          const runId = (anchor.getAttribute("data-testid") || "").replace(
            "sidebar-run-",
            ""
          );
          if (runId) {
            openTab("runs", {
              feature: "runs",
              route: "runs",
              runId,
              title: runId,
            });
          }
        });
      }
    }

    if (!newestFirst.length) {
      body.append(degradedMessage("No runs yet"));
      return;
    }

    // The main face opens on the focused run's stage track — the sidebar
    // selection or the deep link decides which, newest by default. The run's
    // identity rides the timeline face itself (the main header strip is
    // gone, 2026-10-09 operator directive), under the registry label rule
    // (#224/#227): version · title, bare version when the title is the raw
    // run id.
    const focus =
      newestFirst.find((run) => run.run_id === context.runId) || newestFirst[0];
    const focusVersion = focus.version || "unknown";
    await renderTimelineView(body, {
      runId: focus.run_id,
      runTitle:
        focus.title && focus.title !== focus.run_id
          ? `${focusVersion} · ${focus.title}`
          : focusVersion,
    });
  });
}

function renderReview(body) {
  body.append(
    element("p", {
      class: "review-hint",
      "data-testid": "review-hint",
      text: "Material review lives in the docs centre.",
    })
  );
  return null;
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
