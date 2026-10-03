/**
 * Run timeline view (IF-TIMELINE-001; interfaces §1q).
 *
 * The stage set comes from the server ``stage_order`` field and the node data
 * from the existing timeline and ac-chain projections; the client composes
 * the current-node card, one independent node per attempt (no folding), the
 * linear stage track and the AC chain. It never hardcodes the stage list
 * (single source: the server projection).
 */

import { requestJson } from "./api.js";
import { degradedMessage, element, loadInto } from "./dom.js";

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
  return loadInto(body, async () => {
    const runId = context.runId;
    if (!runId) {
      body.append(degradedMessage("Unknown run"));
      return;
    }
    const encoded = encodeURIComponent(runId);
    const timeline = await requestJson(`/api/runs/${encoded}/timeline`);
    const acChain = await requestJson(`/api/runs/${encoded}/ac-chain`);
    const attempts = groupAttempts(timeline);
    body.append(currentCard(context));
    body.append(stageTrack(stageOrder(timeline), attempts));
    body.append(attemptList(attempts));
    body.append(acChainList(acChain));
  });
}

function currentCard(context) {
  return element("section", { class: "current-card", "data-testid": "current-node-card" }, [
    span("current-owner", context.owner || "Unknown"),
    span("current-attempt", context.attempt === undefined ? "-" : String(context.attempt)),
    span("current-duration", context.duration || "-"),
    span("current-action", context.action || "-"),
  ]);
}

function stageTrack(stages, attempts) {
  const track = element("ol", { class: "stage-track", "data-testid": "stage-track" });
  for (const stage of stages) {
    const node = element(
      "li",
      { class: "stage-node", "data-testid": `stage-${stage}`, "data-stage": stage },
      [element("span", { class: "stage-label", text: stage })]
    );
    for (const [attempt, events] of attempts) {
      if (!events.some((event) => event.stage === stage)) continue;
      node.append(
        element("span", {
          class: "stage-attempt",
          "data-testid": `stage-attempt-${stage}-${attempt}`,
          "data-attempt": String(attempt),
          text: `attempt ${attempt}`,
        })
      );
    }
    track.append(node);
  }
  return track;
}

function attemptList(attempts) {
  const list = element("ul", { class: "attempt-list", "data-testid": "attempt-list" });
  for (const [attempt, events] of attempts) {
    const item = element("li", {
      class: "attempt-node",
      "data-testid": `attempt-${attempt}`,
      "data-attempt": String(attempt),
    });
    item.append(element("span", { class: "attempt-label", text: `attempt ${attempt}` }));
    for (const event of events) {
      item.append(
        element("span", {
          class: "attempt-event",
          text: event.summary || event.type || "event",
        })
      );
    }
    list.append(item);
  }
  return list;
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
  return list;
}

function span(testid, text) {
  return element("span", { class: testid, "data-testid": testid, text });
}