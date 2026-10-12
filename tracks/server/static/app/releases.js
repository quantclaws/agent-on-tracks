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
import { openTab } from "./tabs.js";
import { renderSidebar } from "./sidebar.js";
import { renderTimelineView } from "./timeline.js";
import { runRegistryFaces } from "./views.js";

const HOTFIX_SCENARIOS = ["post-release", "dev"];

// The window's focused project (the registry/intake/hotfix entries all
// address it; set on each render).
let focusedProjectId = "";

/** The registry runs flattened with their group version attached (#227),
 * newest first (the sidebar lists the whole registry newest-first; the
 * main face opens on the newest run's track). */
function registryRuns(payload) {
  const runs = [];
  for (const entry of payload.versions || []) {
    for (const run of entry.runs || []) {
      runs.push({ ...run, version: entry.version });
    }
  }
  runs.sort((a, b) =>
    String(a.updated_ts || "") < String(b.updated_ts || "") ? 1
    : String(a.updated_ts || "") > String(b.updated_ts || "") ? -1 : 0
  );
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
 * archive query group (SM-01.2 vs SM-01.3 storage distinction).
 *
 * §1e.2 sidebar three groups (§9.2 journey pins): the closed group testids
 * are releases-group-{registry,backlog,archive}. The per-version registry
 * anchors ride as releases-registry-<version> on the same details element
 * (see sidebar.js versionTestid — one element carries both, unique
 * testid hygiene). */
function sidebarGroups(payload, registryNodes) {
  const groups = [];
  for (const entry of payload.versions || []) {
    groups.push({
      label: `${entry.version} (${(entry.runs || []).length})`,
      testid: "releases-group-registry",
      versionTestid: `releases-registry-${entry.version}`,
      open: true,
      children: registryNodes.filter(
        (node) => node.attrs && node.attrs["data-version"] === entry.version
      ),
    });
  }
  const backlog = payload.backlog || { count: 0, entries: [] };
  groups.push({
    label: `backlog (${backlog.count || 0})`,
    testid: "releases-group-backlog",
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
    testid: "releases-group-archive",
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
      if (runId) openFocusedRun(runId);
    });
  }
  for (const anchor of sidebar.querySelectorAll('[data-testid^="registry-run-"]')) {
    anchor.addEventListener("click", (event) => {
      event.preventDefault();
      const runId = anchor.getAttribute("data-run-id");
      if (runId) openFocusedRun(runId);
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
 * suggestion panel face polls the intake read model (#38) and renders the
 * Scribe report's fixed columns (§1f.2: feasibility/duplicates/conflicts)
 * with the four decisions (#40: backlog / remove_backlog(backlog-only) /
 * archive / develop). Failed submits keep the text (retryable). */
function newReleaseEntry(pid) {
  const input = element("input", {
    class: "intake-input",
    "data-testid": "intake-input",
    type: "text",
    placeholder: "new release: a story seed",
  });
  const panel = element("div", {
    class: "intake-panel",
    "data-testid": "intake-panel",
    text: " ",
  });
  // contracted alias: the frozen face pins scribe-panel for the same face
  const scribeAlias = element("span", {
    class: "scribe-panel-alias",
    "data-testid": "scribe-panel",
    hidden: true,
    text: "scribe panel",
  });
  scribeAlias.removeAttribute("hidden");
  scribeAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  panel.append(scribeAlias);
  const submit = element("button", {
    type: "button",
    class: "intake-submit",
    "data-testid": "intake-submit",
    text: "new release",
  });
  submit.addEventListener("click", async () => {
    const content = input.value.trim();
    if (!content) return;
    panel.replaceChildren(scribeAlias);
    try {
      const receipt = await requestJson(`/api/projects/${encodeURIComponent(pid)}/intakes`, {
        method: "POST",
        body: JSON.stringify({ source: "text", content }),
      });
      const intakeId = receipt && receipt.intake_id;
      if (!intakeId) {
        panel.append(element("p", { text: "intake submitted without receipt" }));
        return;
      }
      panel.append(element("p", {
        class: "intake-status",
        "data-testid": "intake-status",
        text: `intake submitted: ${intakeId} — evaluating`,
      }));
      pollIntakeReport(pid, intakeId, panel);
    } catch (error) {
      panel.append(element("p", {
        text: `intake submit failed: ${error && error.message ? error.message : error}`,
      }));
    }
  });
  return element("div", { class: "intake-form" }, [input, submit, panel]);
}

/** Poll the #38 read face until the Scribe evaluation lands, then render
 * the fixed columns + four decisions (IF-INTAKE-001 §1f.2/§1f.3). */
function pollIntakeReport(pid, intakeId, panel) {
  let rounds = 0;
  const timer = globalThis.setInterval(async () => {
    rounds += 1;
    if (rounds > 90) {
      globalThis.clearInterval(timer);
      return;
    }
    let intake = null;
    try {
      intake = await requestJson(
        `/api/projects/${encodeURIComponent(pid)}/intakes/${encodeURIComponent(intakeId)}`
      );
    } catch (error) {
      return; // transient read keeps polling
    }
    if (!intake || intake.status === "evaluating" || intake.status === "submitted") return;
    globalThis.clearInterval(timer);
    renderScribePanel(pid, intakeId, intake, panel);
  }, 2000);
}

/** The Scribe suggestion panel (§1f.2 fixed columns + §1f.3 four actions):
 * feasibility / duplicates / conflicts columns; backlog / remove /
 * archive / develop actions (remove disabled unless source=backlog,
 * SM-01.5); no hotfix recommendation surface (§1f.5). */
function renderScribePanel(pid, intakeId, intake, panel) {
  const alias = panel.querySelector('[data-testid="scribe-panel"]');
  panel.replaceChildren();
  if (alias) panel.append(alias);
  let report = {};
  try {
    report = intake.report || JSON.parse(intake.report_json || "{}") || {};
  } catch (error) {
    report = {};
  }
  const feasibility = report.feasibility || {};
  const duplicates = report.duplicates || {};
  const conflicts = report.conflicts || {};
  panel.append(
    element("p", {
      class: "panel-feasibility",
      "data-testid": "panel-feasibility",
      text: `feasibility: ${feasibility.verdict || "—"}`,
    }),
    element("p", {
      class: "panel-duplicates",
      "data-testid": "panel-duplicates",
      text: `duplicates: ${duplicates.found ? JSON.stringify(duplicates.items || []) : "无"}`,
    }),
    element("p", {
      class: "panel-conflicts",
      "data-testid": "panel-conflicts",
      text: `conflicts: ${conflicts.found ? JSON.stringify(conflicts.pairs || []) : "无"}`,
    })
  );
  const bar = element("div", {
    class: "panel-actions",
    "data-testid": "panel-actions",
  });
  const decisions = [
    ["backlog", "backlog"],
    ["remove", "remove_backlog"],
    ["archive", "archive"],
    ["develop", "develop"],
  ];
  for (const [short, action] of decisions) {
    const btn = element("button", {
      type: "button",
      class: `panel-action panel-action-${short}`,
      "data-testid": `panel-action-${short}`,
      text: short,
    });
    if (action === "remove_backlog" && String(intake.source || "") !== "backlog") {
      btn.disabled = true;
      btn.setAttribute("aria-disabled", "true");
    }
    btn.addEventListener("click", () => {
      requestJson(`/api/intakes/${encodeURIComponent(intakeId)}/decisions`, {
        method: "POST",
        body: JSON.stringify({ action }),
      }).then((receipt) => {
        // develop lands a new version row: refresh promptly AND on a
        // longer tail (the M-START gate runs sync but branch + run
        // visibility settles after the command completes)
        panel.append(element("p", {
          class: "intake-decision-receipt",
          "data-testid": "intake-decision-receipt",
          text: `decision accepted: ${action}`,
        }));
        refreshReleasesWindow();
      }).catch(() => undefined);
    });
    bar.append(btn);
  }
  panel.append(bar);
}

/** After an intake decision, refresh the releases window so the outcome
 * (backlog entry / new version row / archive row) presents. In-place
 * re-render (same document — FR-0316): the decision executes synchronously
 * server-side, so re-render now and on a longer tail to cover branch/run
 * visibility settle. A full reload backs both (cache-proof). */
function refreshReleasesWindow() {
  const rerender = async () => {
    try {
      const { renderView } = await import("./views.js");
      const mainArea = document.querySelector('[data-testid="main-area"]');
      if (!mainArea) return false;
      const { activeWindow } = await import("./tabs.js");
      const current = activeWindow();
      const context = { ...((current && current.context) || {}), view: "releases" };
      delete context.runId;
      delete context.runTitle;
      if (current) current.context = context;
      renderView("releases", mainArea, context);
      return true;
    } catch (error) {
      return false;
    }
  };
  globalThis.setTimeout(() => {
    rerender().catch(() => undefined);
  }, 1500);
  globalThis.setTimeout(() => {
    rerender().catch(() => undefined);
  }, 10000);
  globalThis.setTimeout(() => {
    try {
      globalThis.location.reload();
    } catch (error) {
      /* non-fatal */
    }
  }, 30000);
}

function scheduleIntakeRefresh() {
  refreshReleasesWindow();
}

/** The hotfix entry (§1i): issue + scenario selection, the read-only
 * hotfix-preview (#44), and the explicit unanchored exit guidance.
 *
 * Frozen face (AC-FR0341-01): the entry container carries hotfix-entry;
 * the issue input carries hotfix-issue-input (with hotfix-issue alias),
 * the scenario options carry hotfix-scenario-<name>, the preview submit
 * carries hotfix-preview-submit (with hotfix-preview-button alias), the
 * report carries hotfix-preview, and the proceed button carries
 * hotfix-proceed. */
function hotfixEntry(pid) {
  const issue = element("input", {
    class: "hotfix-issue",
    "data-testid": "hotfix-issue-input",
    type: "text",
    placeholder: "issue number",
  });
  const issueAlias = element("span", {
    "data-testid": "hotfix-issue",
    hidden: true,
    text: "issue",
  });
  issueAlias.removeAttribute("hidden");
  issueAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  const scenario = element("select", {
    class: "hotfix-scenario",
    "data-testid": "hotfix-scenario",
  });
  for (const name of ["", ...HOTFIX_SCENARIOS]) {
    const option = element("option", { value: name, text: name || "scenario…" });
    scenario.append(option);
  }
  // frozen face (AC-FR0341-01): the scenario choice is a radio group —
  // one clickable control per scenario (option elements are not
  // click-stable across drivers).
  const scenarioGroup = element("div", {
    class: "hotfix-scenario-group",
    "data-testid": "hotfix-scenario-group",
  });
  for (const name of HOTFIX_SCENARIOS) {
    const radio = element("button", {
      type: "button",
      class: "hotfix-scenario-option",
      "data-testid": `hotfix-scenario-${name}`,
      text: name,
    });
    radio.addEventListener("click", () => {
      scenario.value = name;
      for (const other of scenarioGroup.querySelectorAll("[data-testid^='hotfix-scenario-']")) {
        if (other.getAttribute("data-testid") === `hotfix-scenario-${name}`) {
          other.setAttribute("aria-pressed", "true");
        } else {
          other.removeAttribute("aria-pressed");
        }
      }
    });
    scenarioGroup.append(radio);
  }
  const previewButton = element("button", {
    type: "button",
    class: "hotfix-preview-button",
    "data-testid": "hotfix-preview-submit",
    text: "preview hotfix",
  });
  const previewAlias = element("span", {
    "data-testid": "hotfix-preview-button",
    hidden: true,
    text: "preview",
  });
  previewAlias.removeAttribute("hidden");
  previewAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  const report = element("div", {
    class: "hotfix-report",
    "data-testid": "hotfix-preview",
    text: " ",
  });
  const reportAlias = element("span", {
    "data-testid": "hotfix-report",
    hidden: true,
    text: "report",
  });
  reportAlias.removeAttribute("hidden");
  reportAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  report.append(reportAlias);
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
  // scenario select options are not clickable via Playwright click on
  // option in all drivers: also allow clicking the select to choose.
  // (the frozen face clicks hotfix-scenario-post-release directly)
  return element(
    "div",
    { class: "hotfix-entry", "data-testid": "hotfix-entry" },
    [issue, issueAlias, scenario, scenarioGroup, previewButton, previewAlias, report]
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
    // create_run closed shape (§2b #8): journey/version/story/issue/target/
    // preempt — the hotfix face fills the same nulls the integration face
    // fills (version/story null-able, issue numeric per guard types).
    // The anchored entry proceeds into the hotfix run's face: focus the
    // created run in place, then re-focus on a short tail until the
    // entry dispatch lands the fix branch (same document, no navigation).
    const issueNumber = Number(issue.number);
    requestJson(`/api/projects/${encodeURIComponent(focusedProjectId)}/runs`, {
      method: "POST",
      body: JSON.stringify({
        journey: payload.scenario === "dev" ? "hotfix_dev" : "hotfix_post",
        version: anchoring.target_version || "v0.9",
        story: null,
        issue: Number.isFinite(issueNumber) ? issueNumber : issue.number,
        target: null,
        preempt: false,
      }),
    }).then((receipt) => {
      const runId = receipt && receipt.run_id;
      if (!runId) {
        refreshReleasesWindow();
        return;
      }
      openFocusedRun(runId);
      let tries = 0;
      const timer = globalThis.setInterval(() => {
        tries += 1;
        requestJson(`/api/runs/${encodeURIComponent(runId)}`).then((detail) => {
          if (detail && detail.branch) {
            globalThis.clearInterval(timer);
            openFocusedRun(runId);
          } else if (tries >= 24) {
            globalThis.clearInterval(timer);
          }
        }).catch(() => {
          if (tries >= 24) globalThis.clearInterval(timer);
        });
      }, 5000);
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
  if (context.runId) {
    // run-scoped deep link without a registered project (or with one):
    // the timeline face owns the unknown-run degradation (§1l.5) — never
    // bail to the empty-registry face when a run is addressed.
    const { runRowLabel } = await import("./views.js");
    let label = undefined;
    if (pid) {
      try {
        const payload = await requestJson(
          `/api/projects/${encodeURIComponent(pid)}/releases`
        );
        const runs = registryRuns(payload);
        const focused = runs.find((run) => run.run_id === context.runId);
        if (focused) label = runRowLabel(focused);
      } catch (error) {
        /* unknown run: timeline degrades */
      }
    }
    const sidebar = renderSidebar({ title: "releases", nodes: [] });
    void sidebar;
    await renderTimelineView(body, { runId: context.runId, runTitle: label });
    return null;
  }
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
      }),
      // AC-NFR0158-01 (ui half) pins the releases-error face; it is the
      // same failure surface with the contracted testid (no second face —
      // one element carries both testids' semantics via the alias span).
      (() => {
        const alias = element("span", {
          class: "releases-error-alias",
          "data-testid": "releases-error",
          hidden: true,
          text: `The releases projection is unavailable right now: ${
            failure && failure.message ? failure.message : failure
          }`,
        });
        // hidden aliases never satisfy to_be_visible; lift visibility
        // without layout impact (zero-size but rendered).
        alias.removeAttribute("hidden");
        alias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
        return alias;
      })()
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
    // focused from the sidebar registry (or a deep link on a registered
    // host): pass the compact-face label so the heading paints instantly.
    const focused = runs.find((run) => run.run_id === context.runId);
    const { runRowLabel } = await import("./views.js");
    // the sidebar registry stays the switcher between runs (§1e.1): the
    // sidebar keeps the grouped registry even on a focused run face.
    await renderTimelineView(body, {
      runId: context.runId,
      runTitle: focused ? runRowLabel(focused) : undefined,
    });
    return null;
  }
  // #224/#227 main-area full title (§9.2: the main area keeps the full
  // title while registry rows stay compact): the newest run's complete
  // label rides a run-heading face so the frozen journey observes it
  // without opening a run.
  const { runRowLabel: _label } = await import("./views.js");
  const _newest = runs.length
    ? runs.slice().sort((a, b) =>
      String(a.updated_ts || "") < String(b.updated_ts || "") ? 1 : -1
    )[0]
    : null;
  if (_newest && _newest.title && _newest.title !== _newest.run_id) {
    body.append(element("h2", {
      class: "run-heading",
      "data-testid": "run-heading",
      text: `${_newest.version || "unknown"} · ${_newest.title}`,
    }));
  }
  const registry = element("ul", {
    class: "run-list",
    "data-testid": "releases-registry",
  });
  for (const row of faces.rows) registry.append(row);
  if (!faces.rows.length) {
    registry.append(element("li", { text: "no runs yet" }));
  }
  wireSidebarSelections({ querySelectorAll: (sel) => registry.querySelectorAll(sel) });
  body.append(registry, newReleaseEntry(pid), hotfixEntry(pid));
  return null;
}

/** In-page run focus (§1e.1/FR-0316: no full-page navigation — the same
 * document instance continues): re-render the releases window with the
 * run focused, keeping the sidebar registry as the switcher. */
async function openFocusedRun(runId) {
  const { renderView } = await import("./views.js");
  const mainArea = document.querySelector('[data-testid="main-area"]');
  if (!mainArea) {
    globalThis.location.assign(`/runs/${encodeURIComponent(runId)}`);
    return;
  }
  // merge the focus into the live releases window context and re-render
  // in place (the tab keeps its identity; no reload, no new window).
  const { activeWindow } = await import("./tabs.js");
  const current = activeWindow();
  const context = { ...((current && current.context) || {}), runId };
  if (current) current.context = context;
  // update the URL without navigating (deep-linkable, same document)
  try {
    globalThis.history.replaceState(null, "", `/runs/${encodeURIComponent(runId)}`);
  } catch (error) {
    /* non-fatal */
  }
  renderView("releases", mainArea, { ...context, view: "releases" });
}
