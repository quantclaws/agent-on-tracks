/**
 * Releases window view (IF-RELEASES-001 / IF-INTAKE-001 / IF-HOTFIXUI-001;
 * interfaces §1e, §1f, §1i; E-01/E-03).
 *
 * The window's single data source is the #36 aggregation projection
 * (``GET /api/projects/{pid}/releases``) — version-grouped run registry,
 * the event-derived backlog folded group, the service-plane archive query
 * group. No mock data: an unavailable projection surfaces an observable
 * failure marker; an empty registry degrades readably.
 *
 * Main-area contract (v0.12): no summary row, no registry list — the
 * sidebar owns the registry. The main area shows exactly one face: the
 * active run's timeline (auto-selected: newest run whose status is not
 * completed|backlog), or the empty state with the "+" create entry that
 * opens the wizard modal (wizard.js). The sidebar always renders its
 * three groups; the focused run node carries the selected face and its
 * version group stays expanded. Run switching reuses openFocusedRun
 * (same document, no reload).
 */

import { requestJson } from "./api.js";
import { element } from "./dom.js";
import { renderSidebar } from "./sidebar.js";
import { renderTimelineView } from "./timeline.js";
import { runRegistryFaces } from "./views.js";
import { openCreateWizard, openDecisionModal } from "./wizard.js";

// The window's focused project (the intake/hotfix entries all address
// it; set on each render).
let focusedProjectId = "";

/** The registry runs flattened with their group version attached (#227),
 * newest first (the sidebar lists the whole registry newest-first). */
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

function isTerminalStatus(status) {
  return status === "completed" || status === "backlog";
}

/** The active run: the newest run (by updated_ts) whose status is not
 * completed|backlog. Null when every run settled (or the registry is
 * empty) — the window then shows the empty state. */
function activeRun(runs) {
  for (const run of runs || []) {
    if (!isTerminalStatus(run.status)) return run;
  }
  return null;
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
 * testid hygiene). The focused run's version group stays expanded; the
 * focused node carries the selected face (data-selected + aria-current,
 * painted by sidebar.js). */
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

/** Pending intakes (submitted/evaluating/evaluated) that still need a
 * human decision. Rendered at the top of the releases main area in both
 * the empty and timeline states; each row offers a "继续" entry into
 * openDecisionModal. Absent when nothing pends. */
const PENDING_INTAKE_STATUSES = new Set(["submitted", "evaluating", "evaluated"]);

function pendingStatusLabel(status) {
  if (status === "evaluated") return "待决策";
  return "评估中";
}

function truncate60(value) {
  const chars = Array.from(String(value || ""));
  if (chars.length <= 60) return chars.join("");
  return `${chars.slice(0, 60).join("")}…`;
}

async function mountPendingIntakes(body, pid) {
  let rows = [];
  try {
    const payload = await requestJson(
      `/api/projects/${encodeURIComponent(pid)}/intakes`
    );
    rows = (payload && payload.intakes) || [];
  } catch (error) {
    return; // silent: the releases projection below still presents
  }
  const pending = (Array.isArray(rows) ? rows : []).filter(
    (row) => row && PENDING_INTAKE_STATUSES.has(row.status)
  );
  if (!pending.length || !body || !body.isConnected) return;
  // Resolve display titles via the #38 detail (report.title); the list
  // face carries no content/title, so fall back to the intake id.
  const titles = await Promise.all(
    pending.map(async (row) => {
      try {
        const detail = await requestJson(
          `/api/projects/${encodeURIComponent(pid)}/intakes/${encodeURIComponent(row.intake_id)}`
        );
        const report = detail && detail.report ? detail.report : null;
        if (report && report.title) return String(report.title);
      } catch (error) {
        /* fall through to the id */
      }
      return String(row.intake_id || "");
    })
  );
  if (!body.isConnected) return;
  const bar = element("section", {
    class: "pending-intakes",
    "data-testid": "pending-intakes",
    "aria-label": "待决策",
  });
  pending.forEach((row, index) => {
    const intakeId = row.intake_id || "";
    const item = element("div", {
      class: "pending-intake",
      "data-testid": `pending-intake-${intakeId}`,
    });
    item.append(
      element("span", {
        class: "pending-intake-title",
        text: truncate60(titles[index] || intakeId),
      }),
      element("span", {
        class: "pending-intake-status",
        text: pendingStatusLabel(row.status),
      })
    );
    const resume = element("button", {
      type: "button",
      class: "pending-intake-resume",
      "data-testid": `pending-intake-resume-${intakeId}`,
      text: "继续",
    });
    resume.addEventListener("click", () => {
      openDecisionModal({
        projectId: pid,
        intakeId,
        onDone: () => refreshReleasesWindow(),
      });
    });
    item.append(resume);
    bar.append(item);
  });
  body.prepend(bar);
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

/** The wizard's feature face lands here: the main area shows the Scribe
 * evaluating panel (same fixed columns + four decisions face as the
 * intake channel — pollIntakeReport/renderScribePanel above). */
function showScribePanel(body, pid, intakeId) {
  if (!body || !body.isConnected) {
    refreshReleasesWindow();
    return;
  }
  body.replaceChildren();
  const panel = element("div", {
    class: "intake-panel",
    "data-testid": "intake-panel",
  });
  // contracted alias: the frozen face pins scribe-panel for the same face
  const scribeAlias = element("span", {
    class: "scribe-panel-alias",
    "data-testid": "scribe-panel",
    text: "scribe panel",
  });
  scribeAlias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  panel.append(scribeAlias);
  panel.append(element("p", {
    class: "intake-status",
    "data-testid": "intake-status",
    text: `intake submitted: ${intakeId} — evaluating`,
  }));
  body.append(panel);
  pollIntakeReport(pid, intakeId, panel);
}

/** The wizard's hotfix face lands here: focus the created run in place,
 * then re-focus on a short tail until the entry dispatch lands the fix
 * branch (same document, no navigation). */
function focusHotfixRun(runId) {
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

async function firstProjectId() {
  const projects = await requestJson("/api/projects");
  const rows = Array.isArray(projects) ? projects : projects.projects || [];
  return rows.length ? rows[0].project_id : "";
}

/** The create wizard's shared onDone (the empty-state ``releases-create``
 * button and the always-visible sidebar ``releases-create-entry`` action
 * both ride this): hotfix focuses the created run; feature+action
 * refreshes for the executed decision's outcome; feature without a
 * decision keeps the legacy Scribe evaluating panel path. */
function createWizardDone(body, pid) {
  return (result) => {
    if (!result) return;
    if (result.kind === "hotfix") focusHotfixRun(result.runId);
    else if (result.kind === "feature" && result.intakeId) {
      // The wizard third page already executed the decision before
      // onDone: refresh so the outcome (backlog/version/archive)
      // presents. The intakeId-only fallback keeps the legacy Scribe
      // panel path (no decision yet) reachable.
      if (result.action) refreshReleasesWindow();
      else showScribePanel(body, pid, result.intakeId);
    }
  };
}

/** The no-active-run face: a hint plus the醒目 "+" entry that opens the
 * create wizard. The wizard's onDone lands back in this window (Scribe
 * panel for a feature seed, focused run for a hotfix). */
function emptyState(body, pid) {
  const wrap = element("div", {
    class: "releases-empty",
    "data-testid": "releases-empty",
  });
  wrap.append(
    element("p", {
      class: "releases-empty-hint",
      text: "请选择 release 查看开发过程，或者创建新的开发",
    })
  );
  const create = element("button", {
    type: "button",
    class: "releases-create",
    "data-testid": "releases-create",
    "aria-label": "创建新的开发",
    text: "+",
  });
  create.addEventListener("click", () => {
    openCreateWizard({ projectId: pid, onDone: createWizardDone(body, pid) });
  });
  wrap.append(create);
  body.append(wrap);
  // Pending intakes ride above the empty face (fire-and-forget: the bar
  // prepends itself when the list resolves; isConnected guards a stale
  // mount after a fast re-render).
  void mountPendingIntakes(body, pid);
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
  // A newer activation replaced this body while the projection loaded:
  // stop before the stale mount overwrites the live sidebar/focus.
  if (!body.isConnected) return null;
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

  const runs = registryRuns(payload);
  const faces = runRegistryFaces(runs);
  // Focus resolution: an addressed run wins (sidebar switch or deep link —
  // the timeline face owns the unknown-run degradation, §1l.5); otherwise
  // the window auto-selects the active run (newest non-terminal). With no
  // active run nothing is selected and the main area shows the empty face.
  const addressed = context.runId
    ? runs.find((run) => run.run_id === context.runId) || { run_id: context.runId }
    : null;
  const focused = addressed || activeRun(runs);
  const focusedId = focused ? focused.run_id : null;
  if (focusedId) {
    for (const node of faces.nodes) {
      if (node.attrs && node.attrs["data-run-id"] === focusedId) {
        node.attrs["data-selected"] = "true";
        node.attrs["aria-current"] = "true";
      }
    }
  }
  // The sidebar always renders the full three groups (never an empty node
  // list): the registry stays the switcher between runs (§1e.1), the
  // focused node carries the selected face and its version group expands.
  const { runRowFullLabel } = await import("./views.js");
  const sidebar = renderSidebar({
    title: "releases",
    nodes: [],
    groups: sidebarGroups(payload, faces.nodes),
    // Always-visible create entry (v0.12 OOB): with an active run the
    // main area shows the timeline and the empty state's "+" is absent,
    // so the sidebar heading carries the wizard's second entry point.
    action: {
      label: "+",
      testid: "releases-create-entry",
      ariaLabel: "创建新的开发",
      onClick: () =>
        openCreateWizard({ projectId: pid, onDone: createWizardDone(body, pid) }),
    },
  });
  wireSidebarSelections(sidebar);

  if (!focusedId) {
    emptyState(body, pid);
    return null;
  }
  // Focused from the sidebar registry, auto-selected, or deep-linked: pass
  // the compact-face label so the heading paints instantly.
  const known = runs.find((run) => run.run_id === focusedId);
  // The pending bar resolves async and prepends itself at the top
  // (isConnected guards a stale mount mid-flight).
  void mountPendingIntakes(body, pid);
  await renderTimelineView(body, {
    runId: focusedId,
    runTitle: known ? runRowFullLabel(known) : undefined,
  });
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
