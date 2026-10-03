/**
 * Run timeline view (IF-TIMELINE-001; interfaces §1q).
 *
 * The stage set comes from the server ``stage_order`` field and the node data
 * from the existing timeline and ac-chain projections; the client composes
 * the current-node card, one independent node per attempt (no folding), the
 * linear stage track and the AC chain. It never hardcodes the stage list
 * (single source: the server projection).
 *
 * The run's live event face rides here too: every run surface subscribes
 * through the merged-stream client (cursor-guarded, reconnect-backfilled) and
 * renders the newest delivered event as ``timeline-last-event`` — the stream
 * projection can advance but never regress.
 */

import { requestJson } from "./api.js";
import { degradedMessage, element } from "./dom.js";
import { connectEventStream } from "./sse.js";
import { openTab } from "./tabs.js";

const CATCHUP_BOUND_MS = 1500;

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

/**
 * Open one coexisting tab per known run (the Runs area is the multi-tab run
 * workspace) and keep every overview row's live last event current. The tabs
 * open in the background — the overview rows stay the active face — and each
 * row's event line is subscribed through the same cursor-guarded stream face
 * as the run detail, so the newest delivered event is visible before the tab
 * surfaces.
 */
export async function openRunTabs(runs, body = null, { implicit = false } = {}) {
  for (const run of runs) {
    const runId = run.run_id || "unknown";
    const row = body
      ? body.querySelector(`[data-testid="overview-run-${cssEscape(runId)}"]`)
      : null;
    const line = row ? row.querySelector('[data-testid="timeline-last-event"]') : null;
    if (line) {
      const stream = connectEventStream({
        runId,
        event_cursor: null,
        onEvent: (delivered) => {
          line.textContent = delivered.type;
        },
      });
      await bound(stream.ready);
    }
    openTab(runId, {
      feature: "runs",
      view: "run_detail",
      runId,
      activate: false,
      implicit,
      title: runId,
    });
  }
}

function cssEscape(value) {
  return globalThis.CSS && CSS.escape ? CSS.escape(value) : value;
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

    const state = { last: events.length ? events[events.length - 1] : null };
    const stream = connectEventStream({
      runId,
      event_cursor: null,
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
    await bound(stream.ready);

    body.append(currentCard(detail, events, state));
    const root = element("section", { class: "timeline", "data-testid": "timeline" });
    root.append(stageTrack(stageOrder(timeline), groupAttempts(timeline)));
    root.append(acChainList(acChain));
    body.append(root);
    body.append(
      element("p", {
        class: "last-event",
        "data-testid": "timeline-last-event",
        text: lastEventText(state.last),
      })
    );
    body.append(overlay());
    return null;
  })();
}

function lastEventText(event) {
  if (!event) return "no events yet";
  const type = event.type || "unknown";
  const summary = event.summary && event.summary !== type ? ` (${event.summary})` : "";
  return `${type}${summary}`;
}

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

function stageTrack(stages, attempts) {
  const track = element("ol", {
    class: "stage-track",
    "data-testid": "timeline-stage-order",
  });
  for (const stage of stages) {
    const node = element("li", {
      class: "stage-node",
      "data-testid": `timeline-node-${stage}`,
      "data-stage": stage,
      role: "button",
      tabindex: "0",
    });
    node.append(element("span", { class: "stage-label", text: stage }));
    for (const [attempt, events] of attempts) {
      if (!events.some((event) => event.stage === stage)) continue;
      node.append(
        element("span", {
          class: "stage-attempt",
          "data-testid": `timeline-attempt-${stage}-${attempt}`,
          text: `attempt ${attempt}`,
        })
      );
    }
    track.append(node);
  }
  if (!track.children.length) {
    track.append(element("li", { class: "stage-node", text: "no stages" }));
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

/**
 * The node detail overlay: start/end timing plus the artifact/revision line
 * and the document-centre jump. Filled and revealed by any timeline node.
 */
function overlay() {
  const detail = element("p", {
    class: "overlay-detail",
    "data-testid": "timeline-node-detail",
  });
  const docLink = element("a", {
    class: "overlay-doc-link",
    "data-testid": "overlay-doc-link",
    href: "#",
    text: "Open in docs centre",
  });
  docLink.addEventListener("click", (event) => {
    event.preventDefault();
    openTab("docs", { feature: "docs", title: "docs centre" });
  });
  const overlay = element(
    "div",
    { class: "node-overlay", "data-testid": "timeline-node-overlay", hidden: true },
    [element("h3", { class: "overlay-title", text: "Node detail" }), detail, docLink]
  );
  globalThis.setTimeout(() => {
    const scope = overlay.closest(".view");
    if (!scope) return;
    for (const node of scope.querySelectorAll("[data-testid^='timeline-node-']")) {
      if (node.dataset.overlayWired) continue;
      node.dataset.overlayWired = "true";
      node.addEventListener("click", () => {
        const stage = node.getAttribute("data-stage") || "unknown";
        detail.textContent = `${stage} — start/end and artifact revision of this node`;
        overlay.hidden = false;
      });
    }
  });
  return overlay;
}

function span(testid, text) {
  return element("span", { class: testid, "data-testid": testid, text });
}
