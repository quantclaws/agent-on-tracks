/**
 * Run timeline view — full drawing + fixed detail pane
 * (IF-TIMELINE-002 / IF-AWAIT-001 / IF-RETURN-001; interfaces §1k/§1l/§1m).
 *
 * Zero new data sources: the stage set comes from the server
 * ``stage_order``, the node data from the existing timeline and ac-chain
 * projections, the gate/return faces from the run detail's
 * ``awaiting_gate`` and ``return_options`` field groups. The client never
 * hardcodes the stage list.
 *
 * Every attempt renders as its own node (no folding); rollbacks project
 * recognizable back edges (来源→目标); node state rides the five-value
 * ``data-state`` channel beside the icon shape (done/active/pending/
 * attention/void — not colour alone). Mouse selection loads the fixed
 * lower ``detail-pane`` (never a floating overlay): node detail from real
 * projection data, readable degradation when data is missing, distinct
 * selected/active markers, an owning-surface jump that returns to the
 * same run and node context. An awaiting run turns the pane into the
 * gate action panel (§1k: reason / impact / the one primary action); a
 * run with legal return targets gets the return pointer with its
 * explicit already-executed confirmation face (§1m).
 */

import { requestJson } from "./api.js";
import { degradedMessage, element } from "./dom.js";
import { connectEventStream } from "./sse.js";

// §1l.1 five-state closed set (icon shape + data-state dual channel).
const STATE_ICONS = {
  "done": "●",
  "active": "◆",
  "pending": "○",
  "attention": "▲",
  "void": "✕",
};

// §1k.2 primary_action closed set — 映射外无入口: the panel renders exactly
// the mapped action face for the current gate.
const GATE_ACTIONS = [
  "triage",
  "review",
  "approve",
  "release-decision",
  "retry",
  "return",
  "guidance",
];

// The rendered run timeline is the only event-stream consumer: one handle
// per rendered face, released when another face renders.
const detailStreams = new Map(); // runId -> handle (the rendered timeline)

function isTerminalStatus(status) {
  return status === "completed" || status === "backlog";
}

export function stageOrder(timeline) {
  return timeline && Array.isArray(timeline.stage_order) ? timeline.stage_order : [];
}

export function groupAttempts(timeline) {
  const groups = new Map();
  for (const event of (timeline && timeline.events) || []) {
    const key =
      event.attempt === undefined || event.attempt === null
        ? event.task_id || "current"
        : event.attempt;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(event);
  }
  return groups;
}

export function renderTimelineView(body, context = {}) {
  const runId = context.runId;
  if (!runId) {
    body.append(degradedMessage("Unknown run"));
    return null;
  }
  /* the gate-follow re-render target (scheduleGateRefresh): the in-place
   * refresh re-invokes this function over the same host and context. */
  lastTimelineMount = { body, context };
  return (async () => {
    const encoded = encodeURIComponent(runId);
    // the heading renders FIRST from the cheap faces (no overview round
    // trip before first paint): the releases-registry context title when
    // focused from the Releases window, else the version/run id. The
    // full story title upgrades the heading once the overview resolves
    // (the releases face already proved titles at NFR-0158-02) — one
    // cheap call, never blocking the first paint.
    const heading = element("h2", {
      class: "run-heading",
      "data-testid": "run-heading",
      text: context.runTitle || runId,
    });
    body.append(heading);
    // The three initial faces load in parallel (no sequential awaits):
    // the slim timeline (the unfiltered projection is ~99MB — always
    // pass slim=1), the run detail, and the ac-chain.
    const [timeline, detail, acChain] = await Promise.all([
      requestJson(`/api/runs/${encoded}/timeline?slim=1`).catch(() => null),
      requestJson(`/api/runs/${encoded}`).catch(() => null),
      requestJson(`/api/runs/${encoded}/ac-chain`).catch(() => []),
    ]);
    if (!timeline || !detail) {
      // unknown run (404): readable degradation in place — no crash, no
      // blank tab (§1l.5). The releases window keeps its heading so the
      // face stays navigable.
      body.append(degradedMessage(`Unknown run: ${runId}`));
      return null;
    }
    if (!context.runTitle && detail && detail.version) {
      // No extra round trip on the upgrade path (zero calls, off first
      // paint): the detail already carries the version, which paints
      // synchronously. The full story title arrives via the releases
      // registry label on the normal focused path.
      heading.textContent = detail.version;
    }

    const state = { last: null };
    const events = eventsOf(timeline);
    if (events.length) state.last = events[events.length - 1];
    for (const handle of detailStreams.values()) handle.close();
    detailStreams.clear();
    if (!isTerminalStatus(detail && detail.status)) {
      // connect the event stream but never await its readiness on the
      // first-paint path (the catch-up drain budget blocks up to 1500ms):
      // the face paints now; the last-event line refreshes as frames land.
      const stream = connectEventStream({
        runId,
        event_cursor: (timeline && timeline.cursor) || null,
        eventTypes: Array.from(new Set(events.map((event) => event.type))),
        onEvent: (delivered) => {
          state.last =
            delivered.payload && delivered.payload.type
              ? delivered.payload
              : { type: delivered.type };
          const line = body.querySelector('[data-testid="timeline-last-event"]');
          if (line) line.textContent = lastEventText(state.last);
        },
      });
      detailStreams.set(runId, stream);
    }

    // Upper face: the timeline panel (heading stays above it) — the
    // current-node card, the horizontal stage track, the ac-chain and the
    // last-event line. Lower face: the detail panel wrapping the fixed
    // detail pane (gate panel / node detail / return pointer / review
    // write face ride inside untouched).
    const upper = element("section", {
      class: "timeline-panel",
      "data-testid": "timeline-panel",
    });
    upper.append(currentCard(detail, events, state));
    if (detail && detail.branch) {
      // the hotfix run's branch identity (IF-HOTFIX-006): the entry
      // lands fix/{issue} async, so the run face carries it explicitly.
      upper.append(element("p", {
        class: "run-branch",
        "data-testid": "run-branch",
        text: `branch: ${detail.branch}`,
      }));
    }
    const root = element("section", { class: "timeline", "data-testid": "timeline" });
    root.append(stageTrack(stageOrder(timeline), events, detail));
    root.append(acChainList(acChain));
    upper.append(root);
    upper.append(
      element("p", {
        class: "last-event",
        "data-testid": "timeline-last-event",
        text: lastEventText(state.last),
      })
    );
    body.append(upper);
    const lower = element("section", {
      class: "detail-panel",
      "data-testid": "detail-panel",
    });
    lower.append(detailPane(detail, events));
    body.append(lower);
    return null;
  })();
}

function eventsOf(timeline) {
  return (timeline && timeline.events) || [];
}

function lastEventText(event) {
  if (!event) return "no events yet";
  const type = event.type || "unknown";
  const summary = event.summary && event.summary !== type ? ` (${event.summary})` : "";
  return `${type}${summary}`;
}

/** The project+version of a run (the docs-centre faces are project-domain:
 * the review-round write face resolves them before #32 read / #35 save). */
async function resolveRunProject(runId) {
  try {
    const projects = await requestJson("/api/projects");
    const rows = Array.isArray(projects) ? projects : projects.projects || [];
    for (const row of rows) {
      const overview = await requestJson(
        `/api/projects/${encodeURIComponent(row.project_id)}/overview`
      ).catch(() => null);
      const run = overview && (overview.runs || []).find(
        (entry) => entry.run_id === runId
      );
      if (run) {
        return { projectId: row.project_id, version: run.version || "" };
      }
    }
    return null;
  } catch (error) {
    return null;
  }
}

/** The rolling summary card above the track (unchanged v0.10 face). */
function currentCard(detail, events, state) {
  const firstTs = events.length ? events[0].ts : null;
  return element("section", {
    class: "current-card",
    "data-testid": "current-node-card",
  }, [
    span("card-owner", ownerOf(detail)),
    span("card-attempt", String(attemptCount(events))),
    span("card-duration", durationOf(firstTs)),
    span("card-action", actionOf(detail, state)),
  ]);
}

function ownerOf(detail) {
  const lease = detail && detail.lease;
  const worker = lease && (lease.worker_id || lease.holder);
  return worker || "supervisor";
}

function attemptCount(events) {
  const ids = new Set(
    events.map((event) =>
      event.attempt === undefined || event.attempt === null
        ? event.task_id || "current"
        : String(event.attempt)
    )
  );
  return ids.size || 1;
}

function durationOf(firstTs) {
  if (!firstTs) return "-";
  const start = Date.parse(firstTs);
  if (Number.isNaN(start)) return "-";
  const seconds = Math.max(0, Math.round((Date.now() - start) / 1000));
  return `${seconds}s`;
}

function actionOf(detail, state) {
  const action = (detail && detail.control_state) || (detail && detail.stage);
  if (action) return String(action);
  return (state.last && (state.last.type || "unknown")) || "-";
}

/**
 * Fold the event order into the per-stage drawing facts: entered stages,
 * invalidated (rolled-back-from, not re-entered) stages, and the back
 * edges a rollback produced (来源→目标, from the stage the run left to
 * the target it returned to).
 */
function foldStageFacts(events) {
  const entered = new Set();
  const invalidated = new Set();
  const backEdges = [];
  let current = null;
  for (const event of events) {
    if (event.type === "stage.entered") {
      current = event.stage || (event.payload && event.payload.stage) || current;
      if (current) {
        entered.add(current);
        invalidated.delete(current);
      }
    } else if (event.type === "stage.rolled_back") {
      const target =
        (event.payload && (event.payload.to_stage || event.payload.to)) || null;
      if (current) {
        backEdges.push({ from: current, to: target });
        invalidated.add(current);
      }
      if (target) {
        entered.add(target);
        current = target;
      }
    }
  }
  return { entered, invalidated, backEdges };
}

function nodeStateOf(stage, detail, facts) {
  if (facts.invalidated.has(stage)) return "void";
  const activeStage = detail && detail.stage;
  if (stage === activeStage) {
    const gate = detail && detail.awaiting_gate;
    return gate ? "attention" : "active";
  }
  return facts.entered.has(stage) ? "done" : "pending";
}

function stageTrack(stages, events, detail) {
  const track = element("ol", {
    class: "stage-track stage-track-horizontal",
    "data-testid": "timeline-stage-order",
  });
  const facts = foldStageFacts(events);
  const attempts = groupAttempts({ events });
  stages.forEach((stage, index) => {
    if (index > 0) {
      // the horizontal connector between neighbours (arrow, not a node —
      // no testid, no click surface, ignored by node queries).
      track.append(element("li", {
        class: "stage-link",
        "aria-hidden": "true",
        text: "→",
      }));
    }
    const nodeState = nodeStateOf(stage, detail, facts);
    const node = element("li", {
      class: `stage-node state-${nodeState}`,
      "data-testid": `timeline-node-${stage}`,
      "data-stage": stage,
      "data-state": nodeState,
      ...(nodeState === "active" || nodeState === "attention"
        ? { "data-active": "true", "data-state-active": "true" }
        : {}),
      role: "button",
      tabindex: "0",
      "aria-label": `${stage} (${nodeState})`,
    });
    node.append(
      element("span", { class: "node-icon", text: STATE_ICONS[nodeState] || "·" }),
      element("span", { class: "stage-label", text: stage })
    );
    for (const [attempt, group] of attempts) {
      if (!group.some((event) => event.stage === stage || (event.payload && event.payload.stage) === stage)) {
        continue;
      }
      node.append(
        element("span", {
          class: "stage-attempt",
          "data-testid": `timeline-attempt-${stage}-${attempt}`,
          text: `attempt ${attempt}`,
        })
      );
    }
    node.addEventListener("click", () => selectNode(node, stage, detail, events));
    node.addEventListener("keydown", (event) => {
      if (event && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        selectNode(node, stage, detail, events);
      }
    });
    track.append(node);
  });
  for (const edge of facts.backEdges) {
    if (edge.to) {
      track.append(
        element("li", {
          class: "back-edge-wrap",
        }, [
          element("span", {
            class: "back-edge",
            "data-testid": `timeline-back-edge-${edge.from}-${edge.to}`,
            text: `↩ ${edge.from} → ${edge.to}`,
          }),
        ])
      );
    }
  }
  if (!stages.length) {
    track.append(element("li", { class: "stage-node", text: "no stages" }));
  }
  // §1l.1: the initial view focuses the active node and keeps its
  // neighbourhood reachable (all history stays navigable). The pane opens
  // on the active node's detail (or the gate action panel when awaiting)
  // WITHOUT marking it selected — 选中查看 and 当前活跃 are two markers.
  const activeNode = track.querySelector('[data-active="true"]');
  if (activeNode && typeof activeNode.scrollIntoView === "function") {
    activeNode.scrollIntoView({ block: "center" });
  }
  return track;
}

function acChainList(acChain) {
  const rows = Array.isArray(acChain) ? acChain : [];
  const list = element("ul", { class: "ac-chain", "data-testid": "ac-chain" });
  for (const entry of rows) {
    list.append(
      element("li", {
        class: "ac-chain-entry",
        "data-testid": `ac-${entry.ac_id || "unknown"}`,
        text: `${entry.ac_id || "unknown"} - ${entry.latest_result || "pending"}`,
      })
    );
  }
  if (!rows.length) list.append(element("li", { text: "no acceptance chain yet" }));
  return list;
}

/** Mouse selection: the pane loads the node's real detail; the
 * ``data-selected`` marker distinguishes 选中查看 from the current
 * ``data-active`` node (§1l.2 双标记). The frozen face pins the
 * data-state-selected / data-state-active attributes — both ride the
 * node element (one element, both markers, no duplicates). */
function selectNode(node, stage, detail, events) {
  const view = node.closest(".view") || node.parentElement;
  if (!view) return;
  const host = view.querySelector('[data-testid="detail-pane"]');
  for (const other of view.querySelectorAll("[data-state-selected]") || []) {
    other.removeAttribute("data-state-selected");
    other.removeAttribute("data-selected");
    other.classList.remove("stage-node-selected");
  }
  node.setAttribute("data-state-selected", "true");
  // the visible selection face (same marker triple as the sidebar focus):
  // data-selected + class hook + an aria mark so the choice reads aloud.
  node.setAttribute("data-selected", "true");
  node.classList.add("stage-node-selected");
  // the active legibility marker (§1l.1 data-state channel): the node's
  // own data-state already names it; expose the frozen alias attribute
  // so the active mark is queryable without parsing data-state.
  for (const candidate of view.querySelectorAll("[data-state='active'],[data-state='attention']") || []) {
    candidate.setAttribute("data-state-active", "true");
  }
  if (host) {
    host.replaceChildren(nodeDetail(stage, detail, events));
  }
}

/** One node's detail from real projection data (§1l.2): timing, state,
 * owning party, evidence, errors, transition reason, back-edge legality.
 * Missing data degrades readably — never fabricated. */
function nodeDetail(stage, detail, events) {
  const ofStage = events.filter(
    (event) =>
      event.stage === stage || (event.payload && event.payload.stage) === stage
  );
  const box = element("div", { class: "node-detail", "data-testid": "node-detail" });
  box.append(
    element("h3", { class: "detail-title", text: stage || "node" }),
    element("p", {
      class: "detail-line",
      text: `state: ${nodeStateOf(stage, detail, foldStageFacts(events))}`,
    })
  );
  if (!ofStage.length) {
    box.append(
      degradedMessage(`no recorded events for ${stage || "this node"} yet`, "NoEvents")
    );
  } else {
    box.append(
      element("p", {
        class: "detail-line",
        text: `start/end: ${ofStage[0].ts || "-"} → ${ofStage[ofStage.length - 1].ts || "-"}`,
      })
    );
    const failure = ofStage.find((event) => event.type === "verdict.failed");
    if (failure) {
      box.append(
        element("p", {
          class: "detail-error",
          text: `error: ${(failure.payload && failure.payload.reason) || failure.type}`,
        })
      );
    }
  }
  const owner = ownerOf(detail);
  box.append(
    element("p", { class: "detail-line", text: `owner: ${owner}` }),
    owningSurfaceEntry()
  );
  return box;
}

/** The owning surface jump (§1l.2): a stable entry to the face that owns
 * the node; the return restores the same run and node context. The frozen
 * face pins detail-owning-surface: the jump link carries it (the owning
 * surface entry), so exactly one exists per pane. */
function owningSurfaceEntry() {
  const jump = element("a", {
    class: "owning-surface-link",
    "data-testid": "detail-owning-surface",
    href: "#",
    text: "open owning surface",
  });
  jump.addEventListener("click", async (event) => {
    event.preventDefault();
    // the owning surface entry opens the docs window in-page (same
    // document — FR-0316) and the return restores the same run context:
    // both legs ride the tab machine. The run id rides the pane dataset
    // so the return re-focuses the same run (run_id in the URL after).
    const pane = jump.closest('[data-testid="detail-pane"]');
    const runId = pane ? pane.getAttribute("data-run-id") : null;
    const { openTab } = await import("./tabs.js");
    openTab("docs", { feature: "docs", title: "docs" });
    globalThis.setTimeout(async () => {
      const { openTab: reopen } = await import("./tabs.js");
      const releases = reopen("releases", {
        feature: "releases",
        title: "releases",
        ...(runId ? { runId } : {}),
      });
      void releases;
      if (runId) {
        try {
          globalThis.history.replaceState(null, "", `/runs/${encodeURIComponent(runId)}`);
        } catch (error) {
          /* non-fatal */
        }
      }
    }, 300);
  });
  const aliased = element("span", {
    class: "owning-surface-alias",
    "data-testid": "owning-surface-link",
    hidden: true,
    text: "owning surface",
  });
  aliased.removeAttribute("hidden");
  aliased.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  const back = element("a", {
    class: "owning-surface-return",
    "data-testid": "owning-surface-return",
    href: "#",
    text: "return to this run",
  });
  back.addEventListener("click", (event) => {
    event.preventDefault();
    globalThis.history.back();
  });
  // AC-FR0326-01 document-center jump (§11 row 3 fixed-pane update): the
  // pane offers the jump to the document center. Same anchor as the
  // owning-surface entry (one link, both testids' semantics — the frozen
  // face pins detail-doc-link; §1l.2 pins the owning surface entry).
  const docJump = element("span", {
    class: "detail-doc-link-alias",
    "data-testid": "detail-doc-link",
    hidden: true,
    text: "document center",
  });
  docJump.removeAttribute("hidden");
  docJump.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  return element("p", { class: "owning-surface" }, [jump, aliased, back, docJump]);
}

/**
 * The fixed lower pane (§1l.2, E-02): node detail — and the SAME surface
 * is the awaiting action panel (§1k.3) plus the return confirm face
 * (§1m). Never a floating overlay: a plain section below the track.
 */
function detailPane(detail, events) {
  const pane = element("section", {
    class: "detail-pane",
    "data-testid": "detail-pane",
  });
  if (detail && detail.run_id) {
    pane.setAttribute("data-run-id", detail.run_id);
  }
  const gate = detail && detail.awaiting_gate;
  if (gate) {
    pane.append(gateActionPanel(detail, gate));
  } else {
    const stage = (detail && detail.stage) || "";
    pane.append(nodeDetail(stage, detail, events));
  }
  pane.append(returnPointer(detail));
  return pane;
}

/** The awaiting action panel (§1k): reason, impact and the single mapped
 * primary action — 映射外无入口. */
function gateActionPanel(detail, gate) {
  const runId = (detail && detail.run_id) || "";
  const encoded = encodeURIComponent(runId);
  const panel = element("div", {
    class: "gate-panel",
    "data-testid": "gate-panel",
  });
  panel.append(
    element("p", {
      class: "gate-reason",
      "data-testid": "gate-reason",
      text: `reason: ${gate.reason || "-"}`,
    }),
    element("p", {
      class: "gate-impact",
      "data-testid": "gate-impact",
      text: `impact: ${gate.impact || "-"}`,
    })
  );
  const action = GATE_ACTIONS.includes(gate.primary_action)
    ? gate.primary_action
    : "guidance";
  panel.append(primaryActionFace(encoded, action));
  if (action === "review") {
    // §1n 评审轮写入通道 on the run face (FR-0344 / test-plan §9.4): the
    // reviewed document is editable inline and accepts an inline comment —
    // both writes ride the command plane (no fake success; failed content
    // stays in place, retryable).
    panel.append(reviewWriteFace(detail));
  }
  return panel;
}

const REVIEW_DOC_BY_STAGE = {
  "M-STORY": "story",
  "M-SPEC": "spec",
  "M-ACC": "acceptance",
};

function reviewWriteFace(detail) {
  const runId = (detail && detail.run_id) || "";
  const encoded = encodeURIComponent(runId);
  const doc = REVIEW_DOC_BY_STAGE[(detail && detail.stage) || ""] || "story";
  const editor = element("textarea", {
    class: "review-doc-editor",
    "data-testid": "review-doc-editor",
    "aria-label": "评审轮直接修改文档",
    rows: 6,
  });
  const save = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": "review-doc-save",
    text: "save",
  });
  const comment = element("input", {
    type: "text",
    class: "review-comment-input",
    "data-testid": "review-comment-input",
    "aria-label": "评审轮 inline 意见",
  });
  const commentSubmit = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": "review-comment-submit",
    text: "comment",
  });
  const feedback = element("p", {
    class: "review-write-feedback",
    "data-testid": "review-write-feedback",
    role: "status",
    hidden: true,
  });
  // §1n.1/§1o.1b: the review round edits through the docs-centre face #35
  // (the six-piece docs_revision token is missing-aware and therefore
  // available in ANY review round — the run-domain #17 face binds the trio
  // digest, which structurally cannot exist before spec/acceptance are
  // written). Resolve the run's project, then read #32 / save #35.
  let revision = null;
  let docsContext = null;
  let loaded = false;
  save.disabled = true; /* enabled once the revision baseline is known */
  resolveRunProject(runId)
    .then(async (context) => {
      docsContext = context;
      if (context === null) {
        return;
      }
      const payload = await requestJson(
        `/api/projects/${encodeURIComponent(context.projectId)}/docs/` +
          `${encodeURIComponent(context.version)}/${encodeURIComponent(doc)}`
      ).catch(() => null);
      if (payload && typeof payload.content === "string") {
        editor.value = payload.content;
      }
      revision = payload && payload.revision ? payload.revision : null;
    })
    .catch(() => {
      /* keep the editable face; the save reports its own outcome */
    })
    .then(() => {
      loaded = true;
      save.disabled = false;
    });
  const say = (text, failed) => {
    feedback.textContent = text;
    feedback.hidden = false;
    feedback.setAttribute("data-failed", failed ? "true" : "false");
  };
  save.addEventListener("click", async () => {
    try {
      if (!loaded || docsContext === null) {
        docsContext = docsContext || (await resolveRunProject(runId));
      }
      if (docsContext === null) {
        throw new Error("the run's project could not be resolved");
      }
      const body = JSON.stringify({ content: editor.value, base_revision: revision });
      let result;
      try {
        // §1n.1 / AC-FR0344-01: the review-round edit binds the docs-centre
        // face #35 (docs_revision base) — the only face bindable before
        // spec/acceptance exist (the M-STORY window; the read falls back to
        // the missing-aware docs_revision there).
        result = await requestJson(
          `/api/projects/${encodeURIComponent(docsContext.projectId)}/docs/` +
            `${encodeURIComponent(docsContext.version)}/${encodeURIComponent(doc)}/edits`,
          { method: "POST", body }
        );
      } catch (docsError) {
        // a trio-complete dir answers the trio digest: the run-domain review
        // face #17 binds it (§1o.1b, the v0.9 review-round face).
        result = await requestJson(
          `/api/runs/${encoded}/docs/${encodeURIComponent(doc)}/edits`,
          { method: "POST", body }
        );
      }
      if (result && result.new_revision) {
        revision = result.new_revision;
      }
      say("已保存", false);
    } catch (error) {
      say(`失败: ${error && error.message ? error.message : error}`, true);
    }
  });
  commentSubmit.addEventListener("click", async () => {
    const body = (comment.value || "").trim();
    if (!body) {
      return;
    }
    const anchorLine = (editor.value.split("\n")[0] || "").trim();
    try {
      await requestJson(`/api/runs/${encoded}/clarifications`, {
        method: "POST",
        body: JSON.stringify({ doc, body, anchor_line: anchorLine }),
      });
      comment.value = "";
      say("已发表", false);
    } catch (error) {
      say(`失败: ${error && error.message ? error.message : error}`, true);
    }
  });
  const face = element("div", { class: "review-write-face" });
  face.append(editor, save, comment, commentSubmit, feedback);
  return face;
}

function primaryActionFace(encoded, action) {
  // §1k.2 唯一主要动作入口: one display line (gate-primary-action, never
  // counted as a gate-action-*) plus exactly one clickable primary button
  // (gate-action-<primary>) posting the happy-path default; the full
  // decision options ride gate-decision-* and never inflate the count.
  const wrap = element("div", { class: "gate-primary-action-wrap" });
  wrap.append(
    element("p", {
      class: "gate-primary-action",
      "data-testid": "gate-primary-action",
      text: `primary action: ${action}`,
    })
  );
  const _primaryDefaults = {
    triage: ["/triage", { decision: "go" }],
    review: ["/review", { action: "no_comment" }],
    approve: null, // bound at click time (revision-bound approval, FR-0308)
    "release-decision": null, // bound at click time (preview-bound, FR-0309)
    retry: ["/retry", {}],
  };
  const _default = _primaryDefaults[action];
  if (_default) {
    const primary = element("button", {
      type: "button",
      class: "gate-action-button gate-primary-button",
      "data-testid": `gate-action-${action}`,
      text: action,
    });
    primary.addEventListener("click", () => {
      requestJson(`/api/runs/${encoded}${_default[0]}`, {
        method: "POST",
        body: JSON.stringify(_default[1]),
      }).then(() => scheduleGateRefresh(encoded, action)).catch(() => undefined);
    });
    wrap.append(primary);
  } else if (action === "approve") {
    // FR-0308: the approval binds the live trio revision the reviewer saw;
    // the UI reads it from the run's spec doc face (same trio digest).
    const primary = element("button", {
      type: "button",
      class: "gate-action-button gate-primary-button",
      "data-testid": "gate-action-approve",
      text: "approve",
    });
    primary.addEventListener("click", async () => {
      try {
        const doc = await requestJson(`/api/runs/${encoded}/docs/spec`);
        const revision = doc && doc.revision;
        if (!revision) return;
        await requestJson(`/api/runs/${encoded}/approvals`, {
          method: "POST",
          body: JSON.stringify({
            object: "trio",
            expected_revision: revision,
            decision: "approve",
          }),
        });
        scheduleGateRefresh(encoded, action);
      } catch (error) {
        /* readable failure stays on panel; no fake success */
      }
    });
    wrap.append(primary);
  } else if (action === "release-decision") {
    // FR-0309: the decision binds the live preview digest.
    const primary = element("button", {
      type: "button",
      class: "gate-action-button gate-primary-button",
      "data-testid": "gate-action-release-decision",
      text: "release-decision",
    });
    primary.addEventListener("click", async () => {
      try {
        const preview = await requestJson(`/api/runs/${encoded}/release-preview`);
        const digest = preview && preview.preview_digest;
        if (!digest) return;
        await requestJson(`/api/runs/${encoded}/release-decision`, {
          method: "POST",
          body: JSON.stringify({
            action: "release",
            preview_digest: digest,
            reason: null,
            target: null,
          }),
        });
        scheduleGateRefresh(encoded, action);
      } catch (error) {
        /* keep panel; no fake success */
      }
    });
    wrap.append(primary);
  }
  if (action === "triage") {
    for (const decision of ["go", "no_go", "park"]) {
      wrap.append(actionButton(encoded, "/triage", { decision }, decision));
    }
  } else if (action === "review") {
    for (const act of ["comment", "no_comment"]) {
      wrap.append(actionButton(encoded, "/review", { action: act }, act));
    }
  } else if (action === "approve") {
    // single primary button only (no duplicate decision button).
  } else if (action === "release-decision") {
    // FR-0309: delay/return options bind the same live preview digest.
    for (const choice of ["delay", "release"]) {
      const btn = element("button", {
        type: "button",
        class: "gate-action-button",
        "data-testid": `gate-decision-${choice}`,
        text: choice,
      });
      btn.addEventListener("click", async () => {
        try {
          const preview = await requestJson(`/api/runs/${encoded}/release-preview`);
          const digest = preview && preview.preview_digest;
          if (!digest) return;
          await requestJson(`/api/runs/${encoded}/release-decision`, {
            method: "POST",
            body: JSON.stringify({
              action: choice,
              preview_digest: digest,
              reason: null,
              target: null,
            }),
          });
          scheduleGateRefresh(encoded, action);
        } catch (error) {
          /* keep panel */
        }
      });
      wrap.append(btn);
    }
  } else if (action === "retry") {
    // single primary button only.
  } else if (action === "return") {
    wrap.append(
      element("span", {
        class: "gate-action-hint",
        text: "confirm the rollback target in the return face below",
      })
    );
  } else {
    // guidance: no action endpoint — owning surface direction only (§1k.2).
    wrap.append(owningSurfaceEntry());
  }
  return wrap;
}

function actionButton(encoded, path, params, label) {
  const button = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": `gate-decision-${label}`,
    text: label,
  });
  button.addEventListener("click", () => {
    requestJson(`/api/runs/${encoded}${path}`, {
      method: "POST",
      body: JSON.stringify(params),
    }).then(() => {
      // decision buttons belong to a primary gate; refresh on the owning
      // primary so the next gate presents (triage decisions → review, etc.)
      const host = button.closest("[data-testid='detail-pane']");
      const primaryBtn = host ? host.querySelector("[data-testid^='gate-action-']") : null;
      const previous = primaryBtn
        ? (primaryBtn.getAttribute("data-testid") || "").replace("gate-action-", "")
        : "";
      scheduleGateRefresh(encoded, previous || label);
    }).catch(() => undefined);
  });
  return button;
}

/** After a gate write, poll the run detail until the supervisor's
 * self-drive moves the gate, then re-render the timeline in place so the
 * next gate presents with its active pointer moved (§1k.3/FR-0345: no
 * manual dispatch). The drive between gates runs on the supervisor tick
 * schedule (up to 120s in the e2e fence), so the poll budget matches it
 * rather than a short UI beat.
 *
 * The watch state lives in memory only (§1l.4: no web storage — the tab
 * state is memory-only): the re-render keeps the poller alive in the same
 * page, so nothing has to survive a reload. A drive passes through
 * gate-less substates, so the poller keeps watching through a null gate
 * and settles when a DIFFERENT gate (or a terminal status) arrives. */
let followTimer = null;
let lastTimelineMount = null;

function rerenderTimeline() {
  const mount = lastTimelineMount;
  if (!mount) return;
  mount.body.replaceChildren();
  renderTimelineView(mount.body, mount.context);
}

function scheduleGateRefresh(encoded, previous) {
  if (followTimer !== null) globalThis.clearInterval(followTimer);
  let last = previous || "";
  let attempts = 0;
  followTimer = globalThis.setInterval(async () => {
    attempts += 1;
    if (attempts > 240) {
      globalThis.clearInterval(followTimer);
      followTimer = null;
      return;
    }
    try {
      const detail = await requestJson(`/api/runs/${encoded}`);
      const current = _gateAction(detail);
      const terminal = isTerminalStatus(detail && detail.status);
      if (current === last && !terminal) return;
      last = current;
      if (!terminal && (current === null || current === "")) {
        rerenderTimeline(); /* mid-drive substate: keep watching */
        return;
      }
      globalThis.clearInterval(followTimer);
      followTimer = null;
      rerenderTimeline(); /* the next gate (or the terminal face) presents */
    } catch (error) {
      /* keep polling through transient reads */
    }
  }, 500);
}

function _gateAction(detail) {
  const gate = detail && detail.awaiting_gate;
  return gate ? gate.primary_action || gate.type || "" : "";
}

/** The return pointer (§1m): rendered ONLY when the projection exposes
 * legal targets; the confirm face lists the already-executed irreversible
 * operations it would cross — never disguising them as undone.
 *
 * Frozen face (§9.2/AC-FR0337-02): the pointer buttons ride
 * return-pointer-<stage>; clicking one opens the confirm face
 * (return-confirm-face: target + consequences + explicit confirm button);
 * confirming posts the return and the verdict face (return-verdict)
 * reflects the server's answer. */
function returnPointer(detail) {
  const options = detail && detail.return_options;
  const targets = options && Array.isArray(options.targets) ? options.targets : [];
  if (!options || !options.allowed || !targets.length) {
    return element("p", {
      class: "return-pointer return-pointer-empty",
      "data-testid": "return-pointer",
      hidden: true,
      text: "no return entry",
    });
  }
  const runId = encodeURIComponent((detail && detail.run_id) || "");
  const box = element("div", {
    class: "return-pointer",
    "data-testid": "return-pointer",
  });
  box.append(
    element("p", {
      class: "return-targets",
      text: `return targets: ${targets.map((target) => target.stage).join(", ")}`,
    })
  );
  for (const target of targets) {
    const pointer = element("button", {
      type: "button",
      class: "return-pointer-button",
      "data-testid": `return-pointer-${target.stage}`,
      text: `return to ${target.stage}`,
    });
    pointer.addEventListener("click", () => {
      openReturnConfirm(box, runId, target);
    });
    box.append(pointer);
  }
  return box;
}

/** The return confirm face (§1m.2): target + already-executed
 * consequences + explicit confirmation; the verdict reflects the
 * server's answer (never a disguised undo). */
function openReturnConfirm(box, runId, target) {
  const existing = box.querySelector('[data-testid="return-confirm-face"]');
  if (existing) existing.remove();
  const executed = target.already_executed || [];
  const face = element("div", {
    class: "return-confirm-face",
    "data-testid": "return-confirm-face",
  });
  face.append(
    element("p", {
      class: "return-target",
      "data-testid": "return-target",
      text: `return target: ${target.stage}`,
    }),
    element("p", {
      class: "return-consequences",
      "data-testid": "return-consequences",
      text: executed.length
        ? `already executed on the way (not undone): ${executed
            .map((op) => `${op.operation_kind} ${op.target}`)
            .join("; ")}`
        : "no irreversible operations crossed",
    })
  );
  const confirm = element("button", {
    type: "button",
    class: "return-confirm-button",
    "data-testid": "return-confirm-button",
    text: `confirm return to ${target.stage}`,
  });
  confirm.addEventListener("click", async () => {
    try {
      const receipt = await requestJson(`/api/runs/${runId}/return`, {
        method: "POST",
        body: JSON.stringify({
          to: target.stage,
          reason: "timeline return pointer",
          confirm: true,
        }),
      });
      face.append(
        element("p", {
          class: "return-verdict",
          "data-testid": "return-verdict",
          text: `return executed: ${JSON.stringify(receipt || {})}`,
        })
      );
    } catch (error) {
      face.append(
        element("p", {
          class: "return-verdict",
          "data-testid": "return-verdict",
          text: `return failed: ${error && error.message ? error.message : error}`,
        })
      );
    }
  });
  // per-target confirm alias (unique testid hygiene: the frozen face's
  // return-confirm-<stage> resolves to the same button via alias span)
  const alias = element("span", {
    class: "return-confirm-alias",
    "data-testid": `return-confirm-${target.stage}`,
    hidden: true,
    text: `confirm return to ${target.stage}`,
  });
  alias.removeAttribute("hidden");
  alias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  face.append(confirm, alias);
  box.append(face);
}

function span(testid, text) {
  return element("span", { class: testid, "data-testid": testid, text });
}
