/**
 * Releases window view (IF-RELEASES-001 / IF-INTAKE-001 / IF-HOTFIXUI-001;
 * interfaces §1e, §1f, §1i; E-01/E-03).
 *
 * The window's single data source is the #36 aggregation projection
 * (``GET /api/projects/{pid}/releases``) — version-grouped run registry,
 * the event-derived backlog folded group, the service-plane archive query
 * group, and the registered identity as a summary row. No mock data: an
 * unavailable projection surfaces an observable failure marker; an empty
 * registry degrades readably. The sidebar's three groups ride the shared
 * ``renderSidebar`` model; the registry's compact row faces come from the
 * views layer (#227 rule). A focused run renders the full timeline view.
 */

import { requestJson } from "./api.js";
import { element } from "./dom.js";
import { renderSidebar } from "./sidebar.js";
import { renderTimelineView } from "./timeline.js";
import { runRegistryFaces } from "./views.js";

const HOTFIX_SCENARIOS = ["post-release", "dev"];

// The window's focused project (the registry/intake/hotfix entries all
// address it; set on each render).
let focusedProjectId = "";

/** The registry runs flattened with their group version attached (#227). */
function registryRuns(payload) {
  const runs = [];
  for (const entry of payload.versions || []) {
    for (const run of entry.runs || []) {
      runs.push({ ...run, version: entry.version });
    }
  }
  return runs;
}

/** The summary row: registered project/version identity only (E-01). */
function summaryRow(summary) {
  return element("p", {
    class: "releases-summary",
    "data-testid": "releases-summary",
    text:
      `${summary.project_id || "unknown"} · ` +
      `${summary.registered_version || "unregistered"} · ` +
      `${summary.repo_name || "repo"}`,
  });
}

/** The sidebar's three visually distinct groups: the version-grouped run
 * registry, the backlog folded group (undispositioned entries only —
 * dispositioned ones left the fold, whereabouts stay queryable), and the
 * archive query group (SM-01.2 vs SM-01.3 storage distinction). */
function sidebarGroups(payload, registryNodes) {
  const groups = [];
  for (const entry of payload.versions || []) {
    groups.push({
      label: `${entry.version} (${(entry.runs || []).length})`,
      testid: `releases-registry-${entry.version}`,
      children: registryNodes.filter(
        (node) => node.attrs && node.attrs["data-version"] === entry.version
      ),
    });
  }
  const backlog = payload.backlog || { count: 0, entries: [] };
  groups.push({
    label: `backlog (${backlog.count || 0})`,
    testid: "releases-backlog-group",
    open: false,
    children: (backlog.entries || [])
      .filter((entry) => !entry.disposition)
      .map((entry) => ({
        label: entry.content || entry.entry_id || "entry",
        testid: `sidebar-backlog-${entry.entry_id || "unknown"}`,
        attrs: { "data-entry-id": entry.entry_id || "" },
      })),
  });
  const archive = payload.archive || [];
  groups.push({
    label: `archive (${archive.length})`,
    testid: "releases-archive-group",
    open: false,
    children: archive.map((row) => ({
      label: row.content || row.intake_id || "archived",
      testid: `sidebar-archive-${row.intake_id || "unknown"}`,
    })),
  });
  return groups;
}

/** Selecting a backlog entry opens the promote entry: the unified intake
 * channel with source=backlog (SM-01.6) — never a second write path. */
function promoteBacklogEntry(entryId) {
  return requestJson(
    `/api/projects/${encodeURIComponent(focusedProjectId)}/intakes`,
    {
      method: "POST",
      body: JSON.stringify({ source: "backlog", origin_entry: entryId }),
    }
  );
}

function wireSidebarSelections(sidebar) {
  if (!sidebar) return;
  for (const anchor of sidebar.querySelectorAll('[data-testid^="sidebar-run-"]')) {
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      const runId = anchor.getAttribute("data-run-id");
      if (runId) globalThis.location.assign(`/runs/${encodeURIComponent(runId)}`);
    });
  }
  for (const anchor of sidebar.querySelectorAll('[data-testid^="sidebar-backlog-"]')) {
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      const entryId = anchor.getAttribute("data-entry-id");
      if (entryId) promoteBacklogEntry(entryId).catch(() => undefined);
    });
  }
}

/** The new-release entry (§1f.3): one intake submitted (source=text); the
 * suggestion panel face polls the intake read model (#37). */
function newReleaseEntry(pid) {
  const input = element("input", {
    class: "intake-input",
    "data-testid": "intake-input",
    type: "text",
    placeholder: "new release: a story seed",
  });
  const panel = element("div", {
    class: "intake-panel",
    "data-testid": "scribe-panel",
    text: " ",
  });
  const submit = element("button", {
    type: "button",
    class: "intake-submit",
    "data-testid": "intake-submit",
    text: "new release",
  });
  submit.addEventListener("click", async () => {
    const content = input.value.trim();
    if (!content) return;
    try {
      await requestJson(`/api/projects/${encodeURIComponent(pid)}/intakes`, {
        method: "POST",
        body: JSON.stringify({ source: "text", content }),
      });
      input.value = "";
      panel.textContent = "intake submitted: the Scribe suggestion panel loads on evaluation";
    } catch (error) {
      panel.textContent = `intake submit failed: ${
        error && error.message ? error.message : error
      }`;
    }
  });
  return element("div", { class: "intake-form" }, [input, submit, panel]);
}

/** The hotfix entry (§1i): issue + scenario selection, the read-only
 * hotfix-preview (#44), and the explicit unanchored exit guidance. */
function hotfixEntry(pid) {
  const issue = element("input", {
    class: "hotfix-issue",
    "data-testid": "hotfix-issue",
    type: "text",
    placeholder: "issue number",
  });
  const scenario = element("select", {
    class: "hotfix-scenario",
    "data-testid": "hotfix-scenario",
  });
  for (const name of ["", ...HOTFIX_SCENARIOS]) {
    scenario.append(element("option", { value: name, text: name || "scenario…" }));
  }
  const previewButton = element("button", {
    type: "button",
    class: "hotfix-preview-button",
    "data-testid": "hotfix-preview-button",
    text: "preview hotfix",
  });
  const report = element("div", {
    class: "hotfix-report",
    "data-testid": "hotfix-report",
  });
  previewButton.addEventListener("click", async () => {
    const number = issue.value.trim();
    const chosen = scenario.value;
    if (!number || !HOTFIX_SCENARIOS.includes(chosen)) {
      report.textContent = "pick an issue and a scenario (post-release or dev)";
      return;
    }
    try {
      const payload = await requestJson(
        `/api/projects/${encodeURIComponent(pid)}/hotfix-preview` +
          `?issue=${encodeURIComponent(number)}&scenario=${encodeURIComponent(chosen)}`
      );
      renderHotfixReport(report, payload);
    } catch (error) {
      report.textContent = `hotfix preview unavailable: ${
        error && error.message ? error.message : error
      }`;
    }
  });
  return element(
    "div",
    { class: "hotfix-entry" },
    [issue, scenario, previewButton, report]
  );
}

function renderHotfixReport(host, payload) {
  const precheck = payload.precheck || {};
  const anchoring = payload.anchoring || {};
  const issue = payload.issue || {};
  host.replaceChildren();
  host.append(
    element("p", {
      "data-testid": "hotfix-precheck-status",
      text:
        `precheck: ${precheck.status || "unknown"}` +
        (precheck.reason ? ` (${precheck.reason})` : ""),
    }),
    element("p", {
      "data-testid": "hotfix-anchoring",
      text:
        `anchoring: ${anchoring.status || "unknown"}` +
        (anchoring.target_version ? ` → ${anchoring.target_version}` : ""),
    })
  );
  if (anchoring.status !== "anchored" || precheck.status === "rejected") {
    // §1i.1: an unanchored or rejected preview explicitly exits hotfix and
    // points to the backlog entry / a new feature instead.
    host.append(
      element("p", {
        class: "hotfix-exit-guidance",
        "data-testid": "hotfix-exit-guidance",
        text: "hotfix exit: route this input through the backlog entry or start a new feature",
      })
    );
    return;
  }
  const proceed = element("button", {
    type: "button",
    class: "hotfix-proceed",
    "data-testid": "hotfix-proceed",
    text: `create hotfix for #${issue.number ?? "?"}`,
  });
  proceed.addEventListener("click", () => {
    requestJson(`/api/projects/${encodeURIComponent(focusedProjectId)}/runs`, {
      method: "POST",
      body: JSON.stringify({
        journey: payload.scenario === "dev" ? "hotfix_dev" : "hotfix_post",
        issue: issue.number,
      }),
    }).catch(() => undefined);
  });
  host.append(proceed);
}

async function firstProjectId() {
  const projects = await requestJson("/api/projects");
  const rows = Array.isArray(projects) ? projects : projects.projects || [];
  return rows.length ? rows[0].project_id : "";
}

export async function renderReleasesView(body, context = {}) {
  const pid = context.projectId || (await firstProjectId());
  focusedProjectId = pid;
  if (!pid) {
    body.append(element("p", { class: "degraded", text: "No registered projects yet" }));
    renderSidebar({ title: "releases", nodes: [] });
    return null;
  }
  let payload = null;
  let failure = null;
  try {
    payload = await requestJson(`/api/projects/${encodeURIComponent(pid)}/releases`);
  } catch (error) {
    failure = error;
  }
  if (!payload) {
    body.append(
      element("p", {
        class: "request-error",
        "data-testid": "api-failure",
        role: "alert",
        text: `The releases projection is unavailable right now: ${
          failure && failure.message ? failure.message : failure
        }`,
      })
    );
    renderSidebar({ title: "releases", nodes: [] });
    return null;
  }

  body.append(summaryRow(payload.summary || {}));
  const runs = registryRuns(payload);
  const faces = runRegistryFaces(runs);
  const sidebar = renderSidebar({
    title: "releases",
    nodes: [],
    groups: sidebarGroups(payload, faces.nodes),
  });
  wireSidebarSelections(sidebar);

  if (context.runId) {
    await renderTimelineView(body, { runId: context.runId });
    return null;
  }
  const registry = element("ul", {
    class: "run-list",
    "data-testid": "releases-registry",
  });
  for (const row of faces.rows) registry.append(row);
  if (!faces.rows.length) {
    registry.append(element("li", { text: "no runs yet" }));
  }
  for (const row of registry.querySelectorAll("[data-run-id]") || []) {
    row.addEventListener("click", () => {
      const runId = row.getAttribute("data-run-id");
      if (runId) globalThis.location.assign(`/runs/${encodeURIComponent(runId)}`);
    });
  }
  body.append(registry, newReleaseEntry(pid), hotfixEntry(pid));
  return null;
}
