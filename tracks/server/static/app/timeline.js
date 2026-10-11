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

const CATCHUP_BOUND_MS = 1500;

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

function bound(promise) {
  return Promise.race([
    promise,
    new Promise((resolve) => globalThis.setTimeout(resolve, CATCHUP_BOUND_MS)),
  ]);
}

export function renderTimelineView(body, context = {}) {
  const runId = context.runId;
  if (!runId) {
    body.append(degradedMessage("Unknown run"));
    return null;
  }
  return (async () => {
    const encoded = encodeURIComponent(runId);
    const timeline = await requestJson(`/api/runs/${encoded}/timeline`);
    const events = (timeline && timeline.events) || [];
    const detail = await requestJson(`/api/runs/${encoded}`).catch(() => null);
    const acChain = await requestJson(`/api/runs/${encoded}/ac-chain`).catch(() => []);

    body.append(
      element("h2", {
        class: "run-heading",
        "data-testid": "run-heading",
        text:
          context.runTitle ||
          (await overviewRunTitle(runId)) ||
          (detail && detail.version) ||
          runId,
      })
    );

    const state = { last: events.length ? events[events.length - 1] : null };
    for (const handle of detailStreams.values()) handle.close();
    detailStreams.clear();
    if (!isTerminalStatus(detail && detail.status)) {
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
      await bound(stream.ready);
    }

    body.append(currentCard(detail, events, state));
    const root = element("section", { class: "timeline", "data-testid": "timeline" });
    root.append(stageTrack(stageOrder(timeline), events, detail));
    root.append(acChainList(acChain));
    body.append(root);
    body.append(
      element("p", {
        class: "last-event",
        "data-testid": "timeline-last-event",
        text: lastEventText(state.last),
      })
    );
    body.append(detailPane(detail, events));
    return null;
  })();
}

function lastEventText(event) {
  if (!event) return "no events yet";
  const type = event.type || "unknown";
  const summary = event.summary && event.summary !== type ? ` (${event.summary})` : "";
  return `${type}${summary}`;
}

async function overviewRunTitle(runId) {
  try {
    const projects = await requestJson("/api/projects");
    const rows = Array.isArray(projects) ? projects : projects.projects || [];
    if (!rows.length) return null;
    const overview = await requestJson(
      `/api/projects/${encodeURIComponent(rows[0].project_id)}/overview`
    );
    const run = (overview.runs || []).find((entry) => entry.run_id === runId);
    if (!run) return null;
    const version = run.version || "unknown";
    return run.title && run.title !== run.run_id ? `${version} · ${run.title}` : version;
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
    class: "stage-track",
    "data-testid": "timeline-stage-order",
  });
  const facts = foldStageFacts(events);
  const attempts = groupAttempts({ events });
  for (const stage of stages) {
    const nodeState = nodeStateOf(stage, detail, facts);
    const node = element("li", {
      class: `stage-node state-${nodeState}`,
      "data-testid": `timeline-node-${stage}`,
      "data-stage": stage,
      "data-state": nodeState,
      ...(nodeState === "active" || nodeState === "attention"
        ? { "data-active": "true" }
        : {}),
      role: "button",
      tabindex: "0",
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
    track.append(node);
  }
  for (const edge of facts.backEdges) {
    if (edge.to) {
      track.append(
        element("span", {
          class: "back-edge",
          "data-testid": `timeline-back-edge-${edge.from}-${edge.to}`,
          text: `↩ ${edge.from} → ${edge.to}`,
        })
      );
    }
  }
  if (!track.children.length) {
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
 * ``data-active`` node (§1l.2 双标记). */
function selectNode(node, stage, detail, events) {
  const view = node.closest(".view") || node.parentElement;
  if (!view) return;
  const host = view.querySelector('[data-testid="detail-pane"]');
  for (const other of view.querySelectorAll("[data-selected]") || []) {
    other.removeAttribute("data-selected");
  }
  node.setAttribute("data-selected", "true");
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
 * the node; the return restores the same run and node context. */
function owningSurfaceEntry() {
  const jump = element("a", {
    class: "owning-surface-link",
    "data-testid": "owning-surface-link",
    href: "#",
    text: "open owning surface",
  });
  jump.addEventListener("click", (event) => {
    event.preventDefault();
    globalThis.location.assign("/docs");
  });
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
  return element("p", { class: "owning-surface" }, [jump, back]);
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
  return panel;
}

function primaryActionFace(encoded, action) {
  const face = element("p", {
    class: "gate-primary-action",
    "data-testid": `gate-action-${action}`,
    text: `primary action: ${action}`,
  });
  if (action === "triage") {
    for (const decision of ["go", "no_go", "park"]) {
      face.append(actionButton(encoded, "/triage", { decision }, decision));
    }
  } else if (action === "review") {
    for (const act of ["comment", "no_comment"]) {
      face.append(actionButton(encoded, "/review", { action: act }, act));
    }
  } else if (action === "approve") {
    face.append(actionButton(encoded, "/approvals", {}, "approve"));
  } else if (action === "release-decision") {
    face.append(actionButton(encoded, "/release-decision", { action: "delay" }, "delay"));
    face.append(actionButton(encoded, "/release-decision", { action: "release" }, "release"));
  } else if (action === "retry") {
    face.append(actionButton(encoded, "/retry", {}, "retry"));
  } else if (action === "return") {
    face.append(
      element("span", {
        class: "gate-action-hint",
        text: "confirm the rollback target in the return face below",
      })
    );
  } else {
    // guidance: no action endpoint — owning surface direction only (§1k.2).
    face.append(owningSurfaceEntry());
  }
  return face;
}

function actionButton(encoded, path, params, label) {
  const button = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": `gate-action-${label}`,
    text: label,
  });
  button.addEventListener("click", () => {
    requestJson(`/api/runs/${encoded}${path}`, {
      method: "POST",
      body: JSON.stringify(params),
    }).catch(() => undefined);
  });
  return button;
}

/** The return pointer (§1m): rendered ONLY when the projection exposes
 * legal targets; the confirm face lists the already-executed irreversible
 * operations it would cross — never disguising them as undone. */
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
    const executed = target.already_executed || [];
    const row = element("div", { class: "return-confirm", "data-stage": target.stage });
    row.append(
      element("p", {
        class: "return-consequence",
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
      "data-testid": `return-confirm-${target.stage}`,
      text: `confirm return to ${target.stage}`,
    });
    confirm.addEventListener("click", () => {
      requestJson(`/api/runs/${runId}/return`, {
        method: "POST",
        body: JSON.stringify({
          to: target.stage,
          reason: "timeline return pointer",
          confirm: true,
        }),
      }).catch(() => undefined);
    });
    row.append(confirm);
    box.append(row);
  }
  return box;
}

function span(testid, text) {
  return element("span", { class: testid, "data-testid": testid, text });
}
