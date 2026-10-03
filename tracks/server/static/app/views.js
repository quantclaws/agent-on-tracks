/**
 * Functional-area views (interfaces §1l.3, §1l.4): the tab bar's seven items
 * map onto these surfaces. Every view consumes the live query projections
 * (interfaces §1l.6 — no mock data) and renders an observable failure marker
 * when a request fails. The main area title shows the content title, never
 * the tab-bar category name.
 */

import { postJson, requestJson } from "./api.js";
import { clear, degradedMessage, element, loadInto } from "./dom.js";
import { renderDocsView } from "./docs.js";
import { renderTimelineView } from "./timeline.js";

const VIEW_RENDERERS = {
  projects: renderProjects,
  runs: renderRuns,
  docs: renderDocsView,
  review: renderReview,
  todos: renderTodos,
  settings: renderSettings,
  account: renderAccount,
};

export function renderView(route, mainArea, context = {}) {
  if (!mainArea) return null;
  clear(mainArea);
  mainArea.append(
    element("header", { class: "main-header" }, [
      element("h1", {
        class: "main-title",
        "data-testid": "main-title",
        text: context.title || "Unknown",
      }),
    ])
  );
  const body = element("section", {
    class: "view",
    "data-testid": `view-${route || "unknown"}`,
  });
  mainArea.append(body);
  const renderer = VIEW_RENDERERS[route];
  if (!renderer) {
    body.append(degradedMessage("Unknown view"));
    return null;
  }
  renderer(body, context);
  return body;
}

function renderProjects(body) {
  return loadInto(body, async () => {
    const payload = await requestJson("/api/projects");
    const list = element("ul", { class: "project-list", "data-testid": "project-list" });
    for (const project of payload.projects || payload || []) {
      const id = project.project_id || project.pid || "unknown";
      list.append(
        element("li", { class: "project-row", "data-testid": `project-row-${id}` }, [
          element("a", {
            class: "project-name",
            href: `/projects?project_id=${encodeURIComponent(id)}`,
            text: project.name || id,
          }),
        ])
      );
    }
    body.append(list);
  });
}

function renderRuns(body, context) {
  if (context.route === "run_detail" && context.runId) {
    return renderTimelineView(body, context);
  }
  return loadInto(body, async () => {
    const payload = await requestJson("/api/projects");
    const list = element("ul", { class: "run-list", "data-testid": "run-list" });
    for (const project of payload.projects || payload || []) {
      const id = project.project_id || project.pid || "unknown";
      list.append(
        element("li", { class: "run-row", "data-testid": `run-row-${id}` }, [
          element("a", {
            class: "run-link",
            href: `/projects/${encodeURIComponent(id)}/runs/new`,
            text: project.name || id,
          }),
        ])
      );
    }
    body.append(list);
  });
}

function renderReview(body) {
  body.append(
    element("p", {
      class: "review-hint",
      "data-testid": "review-hint",
      text: "Material review lives in the Docs centre.",
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
        element("li", { class: "todo-row", "data-testid": `todo-${todo.run_id || "unknown"}` }, [
          element("span", { class: "todo-label", text: todo.reason || todo.kind || "Unknown" }),
        ])
      );
    }
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
    body.append(
      element("p", {
        class: "account-name",
        "data-testid": "account-name",
        text: profile.actor || "Unknown",
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
        await postJson("/api/auth/logout", {});
      } finally {
        globalThis.location.assign("/login");
      }
    });
    body.append(logout);
  });
}