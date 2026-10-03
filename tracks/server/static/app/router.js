/**
 * Shell router: restores the server ``data-route`` deep link (interfaces
 * §1l.1). The server renders one shared workbench shell for the seven entries
 * and carries the route on the ``#workbench`` dataset, so the client restores
 * the initial tab/sidebar without a second source. Run/project scoped deep
 * links (``/runs/<id>``, ``/projects/<pid>/runs/new``) additionally parse
 * their ids from the location: the page shell renders one shared document,
 * so the URL is the deep-link parameter carrier.
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
  const path = globalThis.location ? globalThis.location.pathname : "";
  const segments = String(path)
    .split("/")
    .filter(Boolean)
    .map((part) => {
      try {
        return decodeURIComponent(part);
      } catch (error) {
        return part;
      }
    });
  return {
    route,
    known: Object.prototype.hasOwnProperty.call(ROUTE_FEATURE, route),
    runId: deepLinkRunId(shell, segments),
    projectId: deepLinkProjectId(shell, segments),
  };
}

export function featureForRoute(route) {
  return ROUTE_FEATURE[route] || null;
}

export function routeQuery() {
  return new URLSearchParams(globalThis.location ? globalThis.location.search : "");
}

function deepLinkRunId(shell, segments) {
  const fromDataset = shell ? shell.dataset.runId || null : null;
  if (fromDataset) return fromDataset;
  // /runs/<run_id> and /runs/<run_id>/{review,release}
  if (segments[0] === "runs" && segments[1]) return segments[1];
  return null;
}

function deepLinkProjectId(shell, segments) {
  const fromDataset = shell ? shell.dataset.projectId || null : null;
  if (fromDataset) return fromDataset;
  // /projects/<pid>/runs/new
  if (segments[0] === "projects" && segments[1] && segments[2] === "runs") {
    return segments[1];
  }
  return null;
}
