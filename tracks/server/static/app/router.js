/**
 * Shell router: restores the server ``data-route`` deep link (interfaces
 * §1l.1). The server renders one shared workbench shell for the seven entries
 * and carries the route plus run_id/project_id on the ``#workbench`` dataset,
 * so the client restores the initial tab/sidebar without a second source.
 *
 * Unknown routes degrade explicitly (interfaces §1l.5) instead of rendering a
 * blank surface.
 */

export const FEATURE_ROUTES = [
  "projects",
  "runs",
  "docs",
  "review",
  "todos",
  "settings",
  "account",
];

const ROUTE_FEATURE = {
  overview: "runs",
  projects: "projects",
  run_new: "runs",
  run_detail: "runs",
  release: "runs",
  docs: "docs",
  review: "review",
  todos: "todos",
  settings: "settings",
  account: "account",
};

export function readRoute(root = document) {
  const shell = root.querySelector("[data-route]");
  const route = shell ? shell.dataset.route || "" : "";
  return {
    route,
    known: Object.prototype.hasOwnProperty.call(ROUTE_FEATURE, route),
    runId: shell ? shell.dataset.runId || null : null,
    projectId: shell ? shell.dataset.projectId || null : null,
  };
}

export function featureForRoute(route) {
  return ROUTE_FEATURE[route] || null;
}

export function routeQuery() {
  return new URLSearchParams(globalThis.location ? globalThis.location.search : "");
}