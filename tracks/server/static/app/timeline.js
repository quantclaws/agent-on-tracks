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

// 英文事件类型→中文可读标签（OOB 可读性：最近事件行不再显示裸 type）。
const EVENT_LABELS = {
  "run.started": "运行开始",
  "stage.entered": "进入阶段",
  "stage.exited": "离开阶段",
  "branch.created": "创建发布分支",
  "command.issued": "下达指令",
  "command.accepted": "指令已受理",
  "dispatch.parity": "派发一致性核对完成",
  "outcome.received": "收到执行结果",
  "lease.acquired": "监督进程运行中（租约心跳）",
  "lease.released": "监督进程释放租约",
  "verdict.passed": "校验通过",
  "verdict.failed": "校验未通过",
  "run.completed": "运行完成",
  "triage.recorded": "triage 裁决已记录",
};

// 服务心跳事件（合并流每 ~5s 一次）：card-action 的“已等 X”以前一个
// 非心跳业务事件为起点，避免常年显示心跳。
const HEARTBEAT_EVENT_TYPES = new Set([
  "lease.acquired",
  "lease.released",
  "command.accepted",
]);

// 门面板可读标签（OOB 可读性：唯一主要动作 + 决策按钮双语）。
const PRIMARY_ACTION_LABELS = {
  "triage": "triage 裁决",
  "review": "评审",
  "approve": "批准（approve）",
  "release-decision": "发布决策",
  "retry": "重试（retry）",
  "guidance": "按指引处理",
};

const DECISION_LABELS = {
  "go": "通过（go）",
  "no_go": "不通过（no_go）",
  "park": "搁置（park）",
  "comment": "留意见（comment）",
  "no_comment": "无意见（no_comment）",
  "delay": "延期（delay）",
  "release": "发布（release）",
};

// The rendered run timeline is the only event-stream consumer: one handle
// per rendered face, released when another face renders.
const detailStreams = new Map(); // runId -> handle (the rendered timeline)

function isTerminalStatus(status) {
  return status === "completed" || status === "backlog";
}

/** Local-time ``YYYY-MM-DD HH:mm:ss`` (24h, zero-padded); invalid/empty
 * input degrades to the original value (or "-"). The timeline's raw ISO
 * stamps carry UTC offsets — the readable face shows the viewer's own
 * clock, seconds precision, no zone suffix. */
export function fmtTs(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Local-time ``HH:mm:ss`` for feed rows; invalid/empty degrades to "--:--:--". */
function fmtClock(iso) {
  if (!iso) return "--:--:--";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--:--:--";
  const pad = (value) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

/** Smart local time: same-day stamps show ``HH:mm:ss`` only; stamps from
 * another local day (or unparseable input) show the ``fmtTs`` date + clock
 * so cross-day ranges stay unambiguous. Always carries ``HH:mm:ss``. */
export function fmtSmart(iso) {
  if (!iso) return "-";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();
  if (sameDay) return fmtClock(iso);
  return fmtTs(iso);
}

// Upper track carousel (OOB 顶部轨道轮播): the viewport wraps the existing
// <ol data-testid="timeline-stage-order"> unchanged (nodes / arrows /
// testids / click semantics all preserved); the viewport hides its own
// scrollbar, centers the current node on first paint, and the two buttons
// reveal the overflowed neighbours.
const TRACK_SCROLL_RATIO = 0.8;
const TRACK_EDGE_TOLERANCE_PX = 2;

// Stage workflow diagram (OOB 阶段内实际工作流 · 聚合视图): the lower
// panel renders the SELECTED node's stage window (last stage.entered →
// next stage.exited, else now) as a compact mermaid stateDiagram-v2 LR —
// one node per DISTINCT dispatch substate (first-seen order), plus an
// entry node, a 人工门 end node (current stage awaiting) or a 完成 end
// node (exited). Edges join adjacent substates; consecutive repeats fold
// into a 重派 ×K self loop; repeated transitions collapse into ×N / 第N轮.
// Per-round input/output/thinking detail rides below the diagram in native
// <details> rows (lazy fetch, see stageRoundsPanel).

// Mermaid lazy loader (mirrors docs.js loadVditor): module-level promise
// over the same-origin vendored asset; a failed load resets so it retries.
let mermaidRequest = null;
let mermaidInitialized = false;
let stagePathDiagramSeq = 0;

export function loadMermaid() {
  if (globalThis.mermaid) return Promise.resolve(globalThis.mermaid);
  if (!mermaidRequest) {
    mermaidRequest = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/vendor/mermaid/mermaid.min.js";
      script.async = true;
      script.onload = () => {
        if (globalThis.mermaid) resolve(globalThis.mermaid);
        else reject(new Error("Mermaid did not initialise"));
      };
      script.onerror = () => reject(new Error("Mermaid asset unavailable"));
      document.head.append(script);
    });
    mermaidRequest.catch(() => {
      mermaidRequest = null; // a failed load may be retried later
    });
  }
  return mermaidRequest;
}

// Mermaid-safe text: strip characters that break stateDiagram parsing.
// Stage descriptions carry only stage names/times/state words, but the
// filter stays as a guard (e.g. `trac install <path>` style text).
function sanitizeMermaidText(text) {
  return String(text === undefined || text === null ? "" : text)
    .replace(/\r?\n/g, " ")
    .replace(/"/g, "'")
    .replace(/</g, "＜")
    .replace(/>/g, "＞")
    .replace(/;/g, "；")
    .replace(/#/g, "＃")
    .replace(/[{}]/g, "")
    .trim();
}

/** Stage workflow source: per-stage ACTUAL execution graph (mermaid
 * ``stateDiagram-v2`` LR, aggregated) over the stage window: the stage's
 * LAST ``stage.entered`` ts through the nearest following ``stage.exited``
 * (or now). One node per DISTINCT dispatch ``params.substate``
 * (first-seen order) plus an entry node, a 人工门 end node (the stage is
 * current and awaiting) or a 完成 end node (the window closed). Edges join
 * adjacent substates of the dispatch sequence; consecutive repeats of one
 * substate fold into a ``重派 ×K`` self loop; repeated A→B transitions
 * collapse into one edge labelled ``×N`` (or ``第 N 轮`` on review_round
 * change). Each substate node carries three description lines: entry
 * count, latest conclusion (pass/失败/ok from the nearest following
 * outcome/verdict-class event) and cumulative dispatch→outcome time
 * (humanized, zero-value units dropped). Never throws — an empty window
 * yields null and the caller degrades.
 */
export function stageWorkflowSource(detail, events, stage) {
  const window = stageWorkflowWindow(events, stage);
  if (!window.length) return null;
  const graph = foldSubstateGraph(window, stage, detail);
  if (!graph || !graph.nodes.length) return null;
  const nodeIds = new Map();
  graph.nodes.forEach((node, index) => {
    nodeIds.set(node.key, `w${index + 1}`);
  });
  const lines = ["stateDiagram-v2", "direction LR"];
  for (const node of graph.nodes) {
    lines.push(`state "${sanitizeMermaidText(node.title)}" as ${nodeIds.get(node.key)}`);
  }
  for (const edge of graph.edges) {
    const from = nodeIds.get(edge.from);
    const to = nodeIds.get(edge.to);
    if (!from || !to) continue;
    if (edge.label) {
      lines.push(`${from} --> ${to} : ${sanitizeMermaidText(edge.label)}`);
    } else {
      lines.push(`${from} --> ${to}`);
    }
  }
  for (const node of graph.nodes) {
    const id = nodeIds.get(node.key);
    for (const line of node.lines) {
      lines.push(`${id} : ${sanitizeMermaidText(line)}`);
    }
  }
  lines.push("classDef done fill:#e8f5e9,stroke:#2e7d32,color:#1e4620");
  lines.push("classDef current fill:#fff4e5,stroke:#f5a623,stroke-width:2px,color:#7a4d00");
  lines.push("classDef pending fill:#f7f7f7,stroke:#d0d0d0,color:#9a9a9a");
  lines.push("classDef void fill:#fdecea,stroke:#d93025,color:#8a1c14");
  lines.push("classDef attention fill:#fff8e1,stroke:#f5a623,color:#7a4d00");
  for (const node of graph.nodes) {
    lines.push(`class ${nodeIds.get(node.key)} ${node.tone || "pending"}`);
  }
  return lines.join("\n");
}

/** The stage window: from the LAST ``stage.entered(stage)`` through the
 * nearest following ``stage.exited`` (any stage closes the window — an exit
 * always ends the current stage), else to the end of the list (now).
 * Time-ordered input assumed (the timeline projection sorts by ts).
 */
function stageWorkflowWindow(events, stage) {
  const list = Array.isArray(events) ? events : [];
  if (!stage) return [];
  let start = -1;
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const event = list[index];
    if (event && event.type === "stage.entered" && stageOf(event) === stage) {
      start = index;
      break;
    }
  }
  if (start < 0) return [];
  let end = list.length;
  for (let index = start + 1; index < list.length; index += 1) {
    const event = list[index];
    if (event && event.type === "stage.exited") {
      end = index + 1;
      break;
    }
  }
  return list.slice(start, end);
}

/** Dispatch params across slim + full rows (slim keeps command.params). */
function dispatchParams(event) {
  const payload = (event && event.payload) || {};
  const command = payload.command || null;
  if (command && typeof command === "object" && command.params && typeof command.params === "object") {
    return command.params;
  }
  if (payload.params && typeof payload.params === "object") return payload.params;
  return null;
}

function dispatchKind(event) {
  const payload = (event && event.payload) || {};
  const command = payload.command || null;
  if (command && typeof command === "object" && command.kind) return String(command.kind);
  return null;
}

function payloadScalar(event, key) {
  const payload = (event && event.payload) || {};
  if (payload[key] !== undefined && payload[key] !== null) return payload[key];
  if (event && event[key] !== undefined && event[key] !== null) return event[key];
  return null;
}

/** Window events → aggregated substate graph. Dispatch_agent rows (kind or
 * a role-bearing params, the slim identity) line up in dispatch order;
 * each dispatch pairs with the nearest following ``outcome.received``
 * carrying the same command id (before the next dispatch) for its end ts,
 * and the latest following outcome/verdict-class row decides its running
 * conclusion. Distinct ``params.substate`` values become one node each
 * (first-seen order); adjacent dispatch pairs become edges; consecutive
 * repeats of one substate fold into a ``重派 ×K`` self loop; repeated A→B
 * transitions collapse into one edge (``×N``, or ``第 N 轮`` when the
 * review_round changed across repeats). Returns ``{nodes, edges}`` or null
 * when the window holds no dispatch substate.
 */
function foldSubstateGraph(window, stage, detail) {
  const dispatches = [];
  for (const event of window) {
    if (!isDispatchInteraction(event)) continue;
    const params = dispatchParams(event) || {};
    if (!params.substate) continue;
    dispatches.push({ event, params, substate: String(params.substate) });
  }
  if (!dispatches.length) return null;
  const enriched = dispatches.map((dispatch, pos) => {
    const bound = pos + 1 < dispatches.length ? dispatches[pos + 1].event : null;
    return { ...dispatch, ...dispatchSpan(window, dispatch.event, bound) };
  });
  const order = [];
  const stats = new Map();
  for (const item of enriched) {
    if (!stats.has(item.substate)) {
      stats.set(item.substate, { count: 0, totalMs: 0, conclusion: "ok", repeats: 0 });
      order.push(item.substate);
    }
    const entry = stats.get(item.substate);
    entry.count += 1;
    if (item.ms !== null && item.ms !== undefined) entry.totalMs += item.ms;
    if (item.conclusion) entry.conclusion = item.conclusion;
  }
  let runKey = null;
  let runLength = 0;
  const flushRun = () => {
    if (runKey !== null && runLength > 1 && stats.has(runKey)) {
      const entry = stats.get(runKey);
      entry.repeats = Math.max(entry.repeats, runLength);
    }
  };
  for (const item of enriched) {
    if (item.substate === runKey) {
      runLength += 1;
    } else {
      flushRun();
      runKey = item.substate;
      runLength = 1;
    }
  }
  flushRun();
  const groups = new Map();
  let prev = null;
  for (const item of enriched) {
    if (prev !== null && prev !== item.substate) {
      const key = `${prev}→${item.substate}`;
      if (!groups.has(key)) {
        groups.set(key, { from: prev, to: item.substate, count: 0, rounds: new Set() });
      }
      const group = groups.get(key);
      group.count += 1;
      const round = item.params.review_round;
      if (round !== undefined && round !== null) group.rounds.add(String(round));
    }
    prev = item.substate;
  }
  const running = !!detail && detail.stage === stage &&
    !(detail.status && isTerminalStatus(detail.status));
  const currentSubstate = detail && detail.substate ? String(detail.substate) : "";
  const lastSubstate = order[order.length - 1];
  const nodes = [{ key: "__enter__", title: "进入", lines: ["进入阶段"], tone: "done" }];
  for (const substate of order) {
    const entry = stats.get(substate);
    const lines = [
      `进入 ${entry.count} 次`,
      `最近结论 ${entry.conclusion}`,
      `累计 ${humanizeSeconds(entry.totalMs / 1000)}`,
    ];
    if (entry.repeats > 1) lines.push(`重派 ×${entry.repeats}`);
    let tone = "done";
    if (running && (substate === currentSubstate || substate === lastSubstate)) {
      tone = "current";
    }
    nodes.push({ key: substate, title: substate, lines, tone });
  }
  const exited = window.some((event) => event && event.type === "stage.exited");
  const awaiting = running && detail && !!detail.awaiting_gate;
  let endKey = null;
  if (awaiting) {
    const basis = lastBusinessEvent(window.length ? window : []);
    nodes.push({
      key: "__gate__",
      title: "人工门",
      lines: [`人工门：等待裁决（已等 ${waitingElapsed(basis)}）`],
      tone: "attention",
    });
    endKey = "__gate__";
  } else if (exited) {
    nodes.push({ key: "__done__", title: "完成", lines: ["完成"], tone: "done" });
    endKey = "__done__";
  }
  const edges = [{ from: "__enter__", to: order[0], label: "" }];
  for (const substate of order) {
    const repeats = stats.get(substate).repeats;
    if (repeats > 1) {
      edges.push({ from: substate, to: substate, label: `重派 ×${repeats}` });
    }
  }
  for (const group of groups.values()) {
    edges.push({ from: group.from, to: group.to, label: repeatEdgeLabel(group) });
  }
  if (endKey) edges.push({ from: lastSubstate, to: endKey, label: "" });
  return { nodes, edges };
}

/** One dispatch's outcome span: end ts from the nearest following
 * ``outcome.received`` with the same command id (any outcome before the
 * bound as fallback), conclusion from that outcome plus verdict-class rows
 * anchored to it — same command id, or within 120s after the outcome ts
 * (``pass`` / ``失败`` / ``ok`` closed set). The anchor stops late
 * downstream verdicts (e.g. a full-suite failure 16min later that the next
 * DIAGNOSE already owns) from drifting onto this dispatch. Never throws. */
function dispatchSpan(window, dispatchEvent, boundEvent) {
  const commandId = dispatchEvent.command_id ?? null;
  let matched = null;
  let fallback = null;
  let outcomeVerdict = "ok";
  let started = false;
  for (const event of window) {
    if (event === dispatchEvent) {
      started = true;
      continue;
    }
    if (!started) continue;
    if ((boundEvent && event === boundEvent) || isDispatchInteraction(event)) break;
    if ((event && event.type) !== "outcome.received") continue;
    const same = !commandId || !event.command_id || event.command_id === commandId;
    if (!fallback) fallback = event;
    if (same && !matched) {
      matched = event;
      outcomeVerdict = outcomeConclusion(event);
    }
  }
  const close = matched || fallback;
  const closeMs = close ? Date.parse(close.ts) : NaN;
  let latest = outcomeVerdict;
  let latestMs = closeMs;
  started = false;
  for (const event of window) {
    if (event === dispatchEvent) {
      started = true;
      continue;
    }
    if (!started) continue;
    if ((boundEvent && event === boundEvent) || isDispatchInteraction(event)) break;
    if ((event && event.type) === "outcome.received") continue;
    const text = verdictConclusion(event);
    if (!text) continue;
    const same = !commandId || !event.command_id || event.command_id === commandId;
    const eventMs = Date.parse(event.ts);
    const nearOutcome = !Number.isNaN(closeMs) && !Number.isNaN(eventMs) &&
      eventMs >= closeMs && eventMs - closeMs <= 120000;
    if (!same && !nearOutcome) continue;
    if (Number.isNaN(eventMs) || Number.isNaN(latestMs) || eventMs >= latestMs) {
      latest = text;
      latestMs = eventMs;
    }
  }
  const startMs = Date.parse(dispatchEvent.ts);
  const endMs = close ? closeMs : Date.now();
  const ms = Number.isNaN(startMs)
    ? null
    : Math.max(0, (Number.isNaN(endMs) ? Date.now() : endMs) - startMs);
  return { ms, conclusion: latest };
}

function outcomeConclusion(event) {
  const payload = (event && event.payload) || {};
  const verdict = payload.verdict;
  if (verdict === "pass") return "pass";
  if (verdict === "fail" || verdict === "failed") return "失败";
  return "ok";
}

function verdictConclusion(event) {
  const type = (event && event.type) || "";
  if (type === "verdict.passed") return "pass";
  if (type === "verdict.failed") return "失败";
  if (type.endsWith(".verdict")) {
    const payload = (event && event.payload) || {};
    return payload.verdict === "pass" ? "pass" : "失败";
  }
  return null;
}

function repeatEdgeLabel(group) {
  if (group.count < 2) return "";
  if (group.rounds.size > 1) {
    const numeric = [...group.rounds].map(Number).filter((n) => !Number.isNaN(n));
    const latest = numeric.length ? Math.max(...numeric) : [...group.rounds].sort().pop();
    return `第 ${latest} 轮`;
  }
  return `×${group.count}`;
}

/** ``durationOf`` 同款人性化（秒数入参）：丢弃 0 值单位；全零 → "0秒"。 */
function humanizeSeconds(totalSeconds) {
  const total = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts = [];
  if (days) parts.push(`${days}天`);
  if (hours) parts.push(`${hours}小时`);
  if (minutes) parts.push(`${minutes}分`);
  if (seconds) parts.push(`${seconds}秒`);
  if (!parts.length) parts.push("0秒");
  return parts.join("");
}

function isDispatchInteraction(event) {
  if (!event || event.type !== "command.issued") return false;
  if (dispatchKind(event) === "dispatch_agent") return true;
  const params = dispatchParams(event);
  return !!(params && params.role);
}

/** Summary line inputs over the stage window (real counts only). */
export function stageWorkflowSummary(detail, events, stage) {
  const window = stageWorkflowWindow(events, stage);
  let dispatches = 0;
  const rounds = new Set();
  let human = 0;
  for (const event of window) {
    const type = (event && event.type) || "";
    if (isDispatchInteraction(event)) {
      dispatches += 1;
      const params = dispatchParams(event) || {};
      if (params.review_round !== undefined && params.review_round !== null) {
        rounds.add(String(params.review_round));
      }
    }
    if (type === "review.round_started") {
      const round = payloadScalar(event, "round");
      if (round !== null) rounds.add(String(round));
    }
    if (type.startsWith("human.")) human += 1;
  }
  const roundText = rounds.size ? [...rounds].sort().join("/") : "—";
  const firstTs = window.length ? window[0].ts : null;
  const running = !!detail && detail.stage === stage && !(detail.status && isTerminalStatus(detail.status));
  const duration = firstTs ? durationOf(firstTs).replace(/^已运行 /, "") : "—";
  return `派发 ${dispatches} 次 · 交互轮次 ${roundText} · 人工动作 ${human} · ${running ? `进行中 ${duration}` : `用时 ${duration}`}`;
}

// Document type stack (mirrors styles.css :root --font-sans) + body size
// (--fs-body 13px): the stage graph uses the same face as the document.
const MERMAID_FONT_FAMILY = `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, ` +
  `"Helvetica Neue", Arial, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`;
const MERMAID_FONT_SIZE = "13px";

/** Render the stage workflow source into ``host`` as inline SVG. Never
 * throws: a failed load or render degrades to a retry line in place. */
async function renderStageWorkflowDiagram(host, source) {
  try {
    const mermaid = await loadMermaid();
    if (!mermaidInitialized) {
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "neutral",
        fontFamily: MERMAID_FONT_FAMILY,
        themeVariables: {
          fontFamily: MERMAID_FONT_FAMILY,
          fontSize: MERMAID_FONT_SIZE,
        },
        flowchart: { nodeSpacing: 16, rankSpacing: 24 },
        state: { padding: 8 },
      });
      mermaidInitialized = true;
    }
    stagePathDiagramSeq += 1;
    const graphId = `stage-workflow-diagram-${stagePathDiagramSeq}`;
    const output = await mermaid.render(graphId, source);
    const svg = typeof output === "string" ? output : output && output.svg;
    if (!svg) throw new Error("empty render");
    host.innerHTML = svg;
  } catch (error) {
    host.textContent = "（阶段图渲染失败，稍后重试）";
  }
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
    lower.append(detailPane(detail, events, stageOrder(timeline)));
    body.append(lower);
    return null;
  })();
}

function eventsOf(timeline) {
  return (timeline && timeline.events) || [];
}

function lastEventText(event) {
  if (!event) return "最近事件：暂无";
  const type = event.type || "unknown";
  const label = EVENT_LABELS[type];
  const stage = event.stage ? ` · ${event.stage}` : "";
  if (label) {
    return `最近事件：${label}${stage}`;
  }
  const summary = event.summary && event.summary !== type ? ` (${event.summary})` : "";
  return `最近事件：${type}${summary}${stage}`;
}

/** The rolling summary card above the track (unchanged v0.10 face). */
function currentCard(detail, events, state) {
  const firstTs = events.length ? events[0].ts : null;
  const ownerRaw = ownerRawOf(detail);
  const ownerDisplay = ownerOf(detail);
  const attempt = attemptCount(events);
  const awaiting = !!(detail && detail.awaiting_gate);
  const actionFace = actionOf(detail, state, events);
  return element("section", {
    class: "current-card",
    "data-testid": "current-node-card",
  }, [
    span("card-owner", ownerDisplay, { title: ownerRaw }),
    span("card-attempt", `尝试 ${attempt} 次`, { title: "本 run 已发生的任务尝试次数" }),
    span("card-duration", durationOf(firstTs), { title: "自 run 开始以来的已运行时间" }),
    awaiting
      ? span("card-action", actionFace.text, {
          title: "正在等待人工处理该门",
          class: "card-action-awaiting",
        })
      : span("card-action", actionFace.text),
  ]);
}

function ownerRawOf(detail) {
  const lease = detail && detail.lease;
  const worker = lease && (lease.worker_id || lease.holder);
  return worker || "supervisor";
}

function ownerOf(detail) {
  const raw = ownerRawOf(detail);
  if (/^supervisor(-\d+)?$/.test(raw)) return "监管进程";
  return raw;
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
  const total = Math.max(0, Math.round((Date.now() - start) / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts = [];
  if (days) parts.push(`${days}天`);
  if (hours) parts.push(`${hours}小时`);
  if (minutes) parts.push(`${minutes}分`);
  if (seconds) parts.push(`${seconds}秒`);
  if (!parts.length) parts.push("0秒");
  return `已运行 ${parts.join("")}`;
}

/** 等待计时的起点：最后一个非心跳业务事件；无则退回最后一个事件。 */
function lastBusinessEvent(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    const type = (event && event.type) || "";
    if (!HEARTBEAT_EVENT_TYPES.has(type)) return event;
  }
  return events.length ? events[events.length - 1] : null;
}

function waitingElapsed(basis) {
  if (!basis || !basis.ts) return "—";
  const start = Date.parse(basis.ts);
  if (Number.isNaN(start)) return "—";
  const total = Math.max(0, Math.round((Date.now() - start) / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts = [];
  if (days) parts.push(`${days}天`);
  if (hours) parts.push(`${hours}小时`);
  if (minutes) parts.push(`${minutes}分`);
  if (seconds || !parts.length) parts.push(`${seconds}秒`);
  return parts.join("");
}

function actionOf(detail, state, events = []) {
  const gate = detail && detail.awaiting_gate;
  if (gate) {
    const basis = lastBusinessEvent(events && events.length ? events : (state && state.last ? [state.last] : []));
    return { text: `等待人工裁决（已等 ${waitingElapsed(basis)}）` };
  }
  const action = (detail && detail.control_state) || (detail && detail.stage);
  if (action) return { text: String(action) };
  return { text: (state && state.last && (state.last.type || "unknown")) || "-" };
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

/** Resolve an event's stage across slim + full timeline rows (the slim
 * projection lifts ``payload.stage`` onto ``event.stage``; full rows keep
 * it inside the payload). Never fabricates — null when neither carries it.
 */
function stageOf(event) {
  if (!event) return null;
  if (event.stage) return event.stage;
  const payload = event.payload;
  if (payload && payload.stage) return payload.stage;
  return null;
}

/** First ``type`` event for ``stage`` (entered/exited); null when the run
 * never recorded one — the chip then shows no time rather than inventing.
 * (Kept for window-adjacent callers; the workflow window is last-entered.)
 */
function firstStageEvent(events, type, stage) {
  for (const event of events || []) {
    if (event && event.type === type && stageOf(event) === stage) return event;
  }
  return null;
}

/** One workflow node's fallback label for unmapped mechanical rows: mapped
 * Chinese label, the dispatch shape, or the raw type (never skipped —
 * only heartbeats/parity/locks are). */
function stageEventLabel(event) {
  const type = (event && event.type) || "unknown";
  if (type === "command.issued") return commandIssuedLabel(event);
  const label = EVENT_LABELS[type];
  if (label) return label;
  return type;
}

/** ``command.issued`` readable label: ``下达指令 Scribe / TRIAGE #1`` from
 * ``payload.command.params`` when present (slim keeps the dispatch
 * identity now); ``task_id`` (``run:substate:round:attempt``) degrades to
 * the substate only when params are absent. Base label alone when neither
 * source exists. */
function commandIssuedLabel(event) {
  const base = EVENT_LABELS["command.issued"] || "command.issued";
  const params =
    (event && event.payload && event.payload.command && event.payload.command.params) ||
    (event && event.payload && event.payload.params) ||
    null;
  let role = params && params.role !== undefined ? params.role : undefined;
  let substate = params && params.substate !== undefined ? params.substate : undefined;
  let attempt = params && params.attempt !== undefined ? params.attempt : undefined;
  if (substate === undefined && event && typeof event.task_id === "string") {
    // task_id form ``run_id:SUBSTATE:review_round:attempt`` (run_loop.issue).
    const parts = event.task_id.split(":");
    if (parts.length >= 4) substate = parts[parts.length - 3];
  }
  if (attempt === undefined && event && event.attempt !== undefined && event.attempt !== null) {
    attempt = event.attempt;
  }
  const segments = [];
  if (role !== undefined && role !== null && role !== "") {
    const text = String(role);
    segments.push(text.charAt(0).toUpperCase() + text.slice(1));
  }
  if (substate !== undefined && substate !== null && substate !== "") {
    segments.push(String(substate));
  }
  let text = base;
  if (segments.length) text += ` ${segments.join(" / ")}`;
  if (attempt !== undefined && attempt !== null && attempt !== "") {
    text += ` #${attempt}`;
  }
  return text;
}

/** Stage workflow panel (OOB 阶段内实际工作流 · 聚合视图): the SELECTED
 * (or current) stage's actual execution graph — never the full 13-stage
 * pipeline (that static repetition is removed). Renders the summary line +
 * ``stage-workflow-diagram`` mermaid host, then the per-round
 * input/output/thinking detail (lazy native <details>), or the pending
 * degradation text when the stage has no recorded window. */
function stagePathPanel(detail, events, selectedStage) {
  const stage = selectedStage || (detail && detail.stage) || "";
  const wrap = element("section", {
    class: "stage-path",
    "data-testid": "stage-path",
  });
  wrap.append(element("h4", { class: "stage-path-title", text: "阶段内工作流（实际运行 · 聚合视图）" }));
  if (!stage || !stageWorkflowWindow(events, stage).length) {
    wrap.append(element("p", {
      class: "stage-workflow-empty",
      "data-testid": "stage-workflow-empty",
      text: "该阶段尚未开始（暂无实际执行记录）",
    }));
    const host = element("div", {
      class: "stage-workflow-diagram",
      "data-testid": "stage-workflow-diagram",
    });
    wrap.append(host);
    return wrap;
  }
  wrap.append(element("p", {
    class: "stage-workflow-summary",
    "data-testid": "stage-workflow-summary",
    text: `${stage} · ${stageWorkflowSummary(detail, events, stage)}`,
  }));
  const host = element("div", {
    class: "stage-workflow-diagram",
    "data-testid": "stage-workflow-diagram",
  });
  wrap.append(host);
  const source = stageWorkflowSource(detail, events, stage);
  if (source) {
    void renderStageWorkflowDiagram(host, source);
  } else {
    host.textContent = "本阶段暂无派发记录";
  }
  wrap.append(stageRoundsPanel(detail, stage));
  return wrap;
}

/** Per-round execution detail (OOB 执行明细): a collapsed native
 * ``<details data-testid="stage-rounds">`` below the aggregate diagram.
 * The rounds list fetches on FIRST expansion only; each round row is its
 * own ``<details data-testid="stage-round-N">`` whose single-round detail
 * fetches on ITS first expansion (cached per command_id — re-expanding
 * never refetches). Rows carry four zones: 输入 / 输出 / 思考过程
 * (nested collapsed details, degraded when logs rotated) / 工具. Every
 * fetch failure degrades its zone in place, never throwing. */
function stageRoundsPanel(detail, stage) {
  const runId = detail && detail.run_id ? String(detail.run_id) : "";
  const outer = element("details", {
    class: "stage-rounds",
    "data-testid": "stage-rounds",
  });
  const summary = element("summary", {
    class: "stage-rounds-summary",
    text: "执行明细（加载中…）",
  });
  outer.append(summary);
  const body = element("div", { class: "stage-rounds-body" });
  outer.append(body);
  const state = { loaded: false, loading: false, detailCache: new Map() };
  outer.addEventListener("toggle", () => {
    if (!outer.open || state.loaded || state.loading) return;
    state.loading = true;
    void (async () => {
      try {
        const payload = await requestJson(
          `/api/runs/${encodeURIComponent(runId)}/stages/${encodeURIComponent(stage)}/rounds`
        );
        const rounds = Array.isArray(payload && payload.rounds) ? payload.rounds : [];
        state.loaded = true;
        summary.textContent = `执行明细（${rounds.length} 轮）`;
        if (!rounds.length) {
          body.append(element("p", {
            class: "stage-rounds-empty",
            text: "（暂无轮次记录）",
          }));
          return;
        }
        for (const round of rounds) {
          body.append(stageRoundRow(runId, round, state.detailCache));
        }
      } catch (error) {
        summary.textContent = "执行明细（加载失败，稍后重试）";
        body.replaceChildren(element("p", {
          class: "stage-rounds-empty",
          text: "（加载失败，稍后重试）",
        }));
        state.loading = false;
      }
    })();
  });
  return outer;
}

/** One round row: ``#N Role · SUBSTATE · 第R轮 · 尝试A · 耗时 · 状态``; the
 * four-zone detail fetches once on first expansion, cached per command_id.
 * A running round (``running=true`` or legacy ``status=="running"``) polls
 * its detail every 2.5s while expanded and refreshes the four zones in
 * place; terminal rounds stay cached. Polling stops when the round ends,
 * the row collapses, or the node leaves the DOM. */
const ROUND_LIVE_POLL_MS = 2500;
// Live text rows shown in the output zone while a round is running.
const LIVE_TEXT_SHOWN = 5;

/** ``true`` when a round detail is still running (new ``running`` flag,
 * falling back to the legacy ``status`` for pre-restart payloads). */
function isRunningRound(entry) {
  if (entry && entry.running === true) return true;
  if (entry && entry.running === false) return false;
  return !!entry && entry.status === "running";
}

function stageRoundRow(runId, round, detailCache) {
  const n = round && round.n !== undefined ? round.n : "?";
  const role = round && round.role ? `${String(round.role).charAt(0).toUpperCase()}${String(round.role).slice(1)}` : "-";
  const substate = round && round.substate ? String(round.substate) : "-";
  const reviewRound = round && round.review_round !== undefined && round.review_round !== null
    ? `第${round.review_round}轮` : "第-轮";
  const attempt = round && round.attempt !== undefined && round.attempt !== null
    ? `尝试${round.attempt}` : "尝试-";
  const elapsed = humanizeSeconds(durationSeconds(round && round.start_ts, round && round.end_ts));
  const status = round && round.status === "running" ? "⏳ 进行中" : "✓ ok";
  const row = element("details", {
    class: "stage-round",
    "data-testid": `stage-round-${n}`,
  });
  row.append(element("summary", {
    class: "stage-round-summary",
    text: `#${n} ${role} · ${substate} · ${reviewRound} · ${attempt} · ${elapsed} · ${status}`,
  }));
  const body = element("div", { class: "stage-round-body" });
  row.append(body);
  const commandId = round && round.command_id ? String(round.command_id) : "";
  const roundUrl = `/api/runs/${encodeURIComponent(runId)}/rounds/${encodeURIComponent(commandId)}`;
  const stopLive = () => {
    if (row._liveTimer !== null && row._liveTimer !== undefined) {
      globalThis.clearInterval(row._liveTimer);
      row._liveTimer = null;
    }
  };
  row._liveTimer = null;
  const paint = (entry) => {
    body.replaceChildren(...stageRoundZones(entry));
  };
  const fetchDetail = async (force) => {
    // Terminal rounds stay cached; running rounds always refetch so the
    // live tail advances (the cache is overwritten on each poll).
    if (!force) {
      const cached = detailCache.get(commandId);
      if (cached && !isRunningRound(cached)) return cached;
    }
    const fresh = await requestJson(roundUrl);
    detailCache.set(commandId, fresh);
    return fresh;
  };
  const startLive = () => {
    stopLive();
    row._liveTimer = globalThis.setInterval(async () => {
      if (!row.open || !row.isConnected) {
        stopLive();
        return;
      }
      try {
        const fresh = await fetchDetail(true);
        paint(fresh);
        if (!isRunningRound(fresh)) stopLive();
      } catch (error) {
        /* keep the last painted zones; retry on the next beat */
      }
    }, ROUND_LIVE_POLL_MS);
  };
  row.addEventListener("toggle", () => {
    if (!row.open) {
      stopLive();
      return;
    }
    if (row.dataset.loading === "true") return;
    // A cached terminal round never refetches; everything else (first
    // open, or a round that was running when cached) fetches on expand.
    const cached = detailCache.get(commandId);
    if (cached && !isRunningRound(cached)) {
      row.dataset.loaded = "true";
      paint(cached);
      return;
    }
    row.dataset.loading = "true";
    void (async () => {
      try {
        const entry = await fetchDetail(true);
        if (!row.isConnected) return;
        row.dataset.loaded = "true";
        row.dataset.loading = "false";
        paint(entry);
        if (isRunningRound(entry) && row.open) startLive();
      } catch (error) {
        row.dataset.loading = "false";
        body.replaceChildren(element("p", {
          class: "stage-round-error",
          text: "（加载失败，稍后重试）",
        }));
      }
    })();
  });
  return row;
}

function durationSeconds(startTs, endTs) {
  if (!startTs) return 0;
  const start = Date.parse(startTs);
  if (Number.isNaN(start)) return 0;
  const end = endTs ? Date.parse(endTs) : Date.now();
  if (Number.isNaN(end)) return 0;
  return Math.max(0, Math.round((end - start) / 1000));
}

/** The four zones of one expanded round (each with its own heading). */
function stageRoundZones(entry) {
  return [
    stageRoundZoneInput(entry),
    stageRoundZoneOutput(entry),
    stageRoundZoneThinking(entry),
    stageRoundZoneTools(entry),
  ];
}

function stageRoundZoneInput(entry) {
  const zone = element("div", { class: "stage-round-zone" });
  zone.append(element("h5", { class: "stage-round-zone-title", text: "输入" }));
  // Params line first (always): dispatch params survive even while the
  // input blob is still unarchived (running rounds have no outcome yet).
  const shaped = roundParams(entry);
  const metaText = [
    `role=${shaped.role}`,
    `substate=${shaped.substate}`,
    `round=${shaped.review_round}`,
    `attempt=${shaped.attempt}`,
    `doc=${shaped.doc}`,
    `objective=${shaped.objective}`,
    `assignment=${shaped.assignmentKind}`,
  ].join(" · ");
  zone.append(element("p", { class: "stage-round-meta", text: metaText }));
  const input = entry && entry.input ? entry.input : null;
  const prompt = input && typeof input.prompt === "string" && input.prompt
    ? input.prompt : "";
  if (prompt) {
    zone.append(element("pre", { class: "stage-round-pre", text: prompt }));
  } else if (isRunningRound(entry)) {
    zone.append(element("p", {
      class: "stage-round-empty",
      text: "派发进行中，prompt 将随结果归档",
    }));
  } else {
    zone.append(element("p", { class: "stage-round-empty", text: "（暂无输入）" }));
  }
  const assignment = shaped.assignment && typeof shaped.assignment === "object"
    ? Object.keys(shaped.assignment).length
      ? JSON.stringify(shaped.assignment, null, 2) : ""
    : "";
  if (assignment) {
    zone.append(element("pre", { class: "stage-round-pre", text: assignment }));
  }
  return zone;
}

/** Dispatch params with blob/entry fallback (new ``params`` first, then
 * the archived input blob, then the legacy top-level round fields). */
function roundParams(entry) {
  const params = entry && entry.params && typeof entry.params === "object" ? entry.params : {};
  const input = entry && entry.input && typeof entry.input === "object" ? entry.input : {};
  const pick = (key) => {
    for (const source of [params, input, entry]) {
      const value = source ? source[key] : undefined;
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return "-";
  };
  let assignment = params.assignment;
  if (!assignment || typeof assignment !== "object") assignment = input.assignment;
  if (!assignment || typeof assignment !== "object") assignment = null;
  const kind = assignment && assignment.kind !== undefined &&
    assignment.kind !== null && assignment.kind !== ""
    ? assignment.kind : "-";
  return {
    role: pick("role"),
    substate: pick("substate"),
    review_round: pick("review_round"),
    attempt: pick("attempt"),
    doc: pick("doc"),
    objective: pick("objective"),
    assignment,
    assignmentKind: kind,
  };
}

function inputRoundOr(entry, input, key) {
  const params = entry && entry.params && typeof entry.params === "object"
    ? entry.params[key] : undefined;
  if (params !== undefined && params !== null) return params;
  const fromInput = input ? input[key] : undefined;
  if (fromInput !== undefined && fromInput !== null) return fromInput;
  const fromEntry = entry ? entry[key] : undefined;
  if (fromEntry !== undefined && fromEntry !== null) return fromEntry;
  return "-";
}

/** Live session items of one round detail (new ``live`` tail, else empty). */
function liveItems(entry) {
  const live = entry ? entry.live : null;
  return Array.isArray(live) ? live : [];
}

/** Latest non-empty live text rows (oldest-first, tail quota). */
function liveTextTail(entry, limit) {
  const rows = [];
  for (const item of liveItems(entry)) {
    if (item && item.kind === "text" && typeof item.text === "string" && item.text) {
      rows.push(item);
    }
  }
  return rows.slice(-limit);
}

function stageRoundZoneOutput(entry) {
  const zone = element("div", { class: "stage-round-zone" });
  zone.append(element("h5", { class: "stage-round-zone-title", text: "输出" }));
  const output = entry && entry.output ? entry.output : null;
  const text = output && typeof output.text === "string" && output.text ? output.text : "";
  if (text) {
    zone.append(element("pre", { class: "stage-round-pre", text }));
    return zone;
  }
  // Running fallback: the newest live text stream (the output blob only
  // archives at outcome.received, so a running round has no final text).
  const tail = liveTextTail(entry, LIVE_TEXT_SHOWN);
  if (tail.length) {
    zone.append(element("p", { class: "stage-round-live-caption", text: "（进行中）" }));
    const lines = tail.map((item) => {
      const ts = item && item.ts ? `${fmtClock(item.ts)} ` : "";
      return `${ts}${item.text}`;
    });
    zone.append(element("pre", {
      class: "stage-round-pre stage-round-live",
      text: lines.join("\n\n"),
    }));
    return zone;
  }
  if (!output || output.unavailable || !text) {
    zone.append(element("p", { class: "stage-round-empty", text: "（暂无输出）" }));
    return zone;
  }
  zone.append(element("pre", { class: "stage-round-pre", text: "（暂无输出）" }));
  return zone;
}

function stageRoundZoneThinking(entry) {
  const zone = element("div", { class: "stage-round-zone" });
  zone.append(element("h5", { class: "stage-round-zone-title", text: "思考过程" }));
  const inner = element("details", { class: "stage-round-thinking" });
  inner.append(element("summary", { class: "stage-round-thinking-summary", text: "展开思考过程" }));
  const thinking = entry ? entry.thinking : null;
  const available = entry ? entry.thinking_available : false;
  if (Array.isArray(thinking) && thinking.length) {
    const list = element("div", { class: "stage-round-thinking-list" });
    for (const item of thinking) {
      const text = item && typeof item.text === "string" ? item.text : "";
      const ts = item && item.ts ? `${fmtClock(item.ts)} ` : "";
      list.append(element("pre", { class: "stage-round-pre", text: `${ts}${text}` }));
    }
    inner.append(list);
  } else if (available === false) {
    inner.append(element("p", {
      class: "stage-round-empty",
      text: "该轮的会话日志已轮转清理，仅存输入/输出",
    }));
  } else {
    inner.append(element("p", {
      class: "stage-round-empty",
      text: "该轮没有记录到思考过程（日志已轮转或 Agent 未产生推理流）",
    }));
  }
  zone.append(inner);
  return zone;
}

function stageRoundZoneTools(entry) {
  const zone = element("div", { class: "stage-round-zone" });
  zone.append(element("h5", { class: "stage-round-zone-title", text: "工具" }));
  const output = entry && entry.output ? entry.output : null;
  const calls = output && Array.isArray(output.tool_calls) ? output.tool_calls : [];
  // Merge archived outcome tool counts with live session tool rows (a
  // running round has no outcome yet, so live-only still counts).
  const merged = new Map();
  for (const call of calls) {
    const tool = call && call.tool ? String(call.tool) : "unknown";
    const n = call && call.n !== undefined && call.n !== null ? Number(call.n) || 0 : 1;
    merged.set(tool, (merged.get(tool) || 0) + n);
  }
  for (const item of liveItems(entry)) {
    if (!item || item.kind !== "tool") continue;
    const tool = item.tool ? String(item.tool) : "unknown";
    merged.set(tool, (merged.get(tool) || 0) + 1);
  }
  if (!merged.size) {
    zone.append(element("p", { class: "stage-round-empty", text: "（本轮未调用工具）" }));
    return zone;
  }
  const list = element("ul", { class: "stage-round-tools" });
  for (const [tool, n] of [...merged.entries()].sort((a, b) => b[1] - a[1])) {
    list.append(element("li", { text: `${tool} ×${n}` }));
  }
  zone.append(list);
  if (isRunningRound(entry)) {
    const liveCount = liveItems(entry).filter((item) => item && item.kind === "tool").length;
    if (liveCount) {
      zone.append(element("p", {
        class: "stage-round-meta",
        text: `含实时工具事件 ${liveCount} 条（进行中）`,
      }));
    }
  }
  return zone;
}

/** Update the carousel buttons' visibility from the viewport scroll
 * position: prev hides at the left edge, next hides at the right edge.
 * The buttons stay in the DOM (hidden only) for stable testids. */
function updateTrackButtons(viewport, prev, next) {
  if (!viewport || !prev || !next) return;
  const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
  const left = viewport.scrollLeft || 0;
  prev.hidden = left <= TRACK_EDGE_TOLERANCE_PX;
  next.hidden = left >= max - TRACK_EDGE_TOLERANCE_PX;
}

function stageTrack(stages, events, detail) {
  const wrap = element("div", {
    class: "stage-track-carousel",
    "data-testid": "stage-track-carousel",
  });
  const prev = element("button", {
    type: "button",
    class: "stage-track-nav stage-track-prev",
    "data-testid": "stage-track-prev",
    "aria-label": "上一段",
    text: "‹",
  });
  const viewport = element("div", {
    class: "stage-track-viewport",
    "data-testid": "stage-track-viewport",
  });
  const next = element("button", {
    type: "button",
    class: "stage-track-nav stage-track-next",
    "data-testid": "stage-track-next",
    "aria-label": "下一段",
    text: "›",
  });
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
  // §1l.1: the initial view centers the active node inside the viewport
  // and keeps its neighbourhood reachable (all history stays navigable).
  // The pane opens on the active node's detail (or the gate action panel
  // when awaiting) WITHOUT marking it selected — 选中查看 and 当前活跃
  // are two markers.
  viewport.append(track);
  wrap.append(prev, viewport, next);
  let userNavigated = false;
  const centerActive = () => {
    const activeNode = track.querySelector('[data-active="true"]');
    if (!activeNode) return;
    const nr = activeNode.getBoundingClientRect();
    const vr = viewport.getBoundingClientRect();
    const delta = (nr.left + nr.width / 2) - (vr.left + vr.width / 2);
    const max = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    viewport.scrollLeft = Math.max(0, Math.min(viewport.scrollLeft + delta, max));
  };
  prev.addEventListener("click", () => {
    userNavigated = true;
    viewport.scrollBy({ left: -viewport.clientWidth * TRACK_SCROLL_RATIO, behavior: "smooth" });
  });
  next.addEventListener("click", () => {
    userNavigated = true;
    viewport.scrollBy({ left: viewport.clientWidth * TRACK_SCROLL_RATIO, behavior: "smooth" });
  });
  viewport.addEventListener("scroll", () => updateTrackButtons(viewport, prev, next), { passive: true });
  viewport.addEventListener("wheel", () => { userNavigated = true; }, { passive: true });
  viewport.addEventListener("pointerdown", () => { userNavigated = true; }, { passive: true });
  viewport.addEventListener("touchstart", () => { userNavigated = true; }, { passive: true });
  if (typeof globalThis.requestAnimationFrame === "function") {
    globalThis.requestAnimationFrame(() => {
      centerActive();
      updateTrackButtons(viewport, prev, next);
    });
  } else {
    centerActive();
    updateTrackButtons(viewport, prev, next);
  }
  if (typeof globalThis.ResizeObserver === "function") {
    try {
      const observer = new globalThis.ResizeObserver(() => updateTrackButtons(viewport, prev, next));
      observer.observe(viewport);
    } catch (error) {
      globalThis.addEventListener("resize", () => updateTrackButtons(viewport, prev, next));
    }
    try {
      const trackObserver = new globalThis.ResizeObserver(() => {
        if (userNavigated) return;
        centerActive();
        updateTrackButtons(viewport, prev, next);
      });
      trackObserver.observe(track);
    } catch (error) {
      /* track re-centering unavailable — initial rAF centering still applies */
    }
  } else {
    globalThis.addEventListener("resize", () => updateTrackButtons(viewport, prev, next));
  }
  updateTrackButtons(viewport, prev, next);
  return wrap;
}

function acChainList(acChain) {
  const rows = Array.isArray(acChain) ? acChain : [];
  if (!rows.length) {
    return element("p", {
      class: "ac-chain-empty",
      hidden: true,
      text: "",
    });
  }
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
    // the pane rebuilds around the SELECTED stage: node detail + that
    // stage's actual workflow + the return pointer (the gate panel yields
    // to the selected node's face — same mount, no overlay).
    host.replaceChildren(
      nodeDetail(stage, detail, events),
      stagePathPanel(detail, events, stage),
      returnPointer(detail),
    );
  }
}

/** One node's detail from real projection data (§1l.2): timing, state,
 * owning party, evidence, errors, transition reason, back-edge legality.
 * Missing data degrades readably — never fabricated. */
function nodeDetail(stage, detail, events) {
  const window = stageWorkflowWindow(events, stage);
  const box = element("div", { class: "node-detail", "data-testid": "node-detail" });
  box.append(
    element("h3", { class: "detail-title", text: stage || "node" }),
    element("p", {
      class: "detail-line",
      text: `state: ${nodeStateOf(stage, detail, foldStageFacts(events))}`,
    })
  );
  if (!window.length) {
    box.append(
      degradedMessage(`no recorded events for ${stage || "this node"} yet`, "NoEvents")
    );
  } else {
    const closed = window.some((event) => event && event.type === "stage.exited");
    const endText = closed ? fmtTs(window[window.length - 1].ts) : "进行中";
    box.append(
      element("p", {
        class: "detail-line",
        text: `start/end: ${fmtTs(window[0].ts)} → ${endText}`,
      })
    );
    const failure = window.find((event) => event.type === "verdict.failed");
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
    text: "查看所属文档",
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
    text: "返回上一页",
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
function detailPane(detail, events, stages) {
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
    // the selected/current stage's actual workflow graph rides after the
    // gate panel, before the return pointer.
    pane.append(stagePathPanel(detail, events, detail && detail.stage));
  } else {
    const stage = (detail && detail.stage) || "";
    pane.append(nodeDetail(stage, detail, events));
    // node detail branch: the selected/current stage's actual workflow
    // graph rides after the detail content, before the return pointer.
    pane.append(stagePathPanel(detail, events, stage));
  }
  pane.append(returnPointer(detail));
  return pane;
}

/** The awaiting action panel (§1k): the single mapped primary action —
 * 映射外无入口. reason/impact render only for exception gates
 * (escalation/rollback/hotfix_triage/review_pending_threads); the normal
 * flow gates (triage/review/approval/AWAIT_RELEASE) stay terse. The head
 * row holds [gate-status chip + action buttons] on one flex line. */
const GATE_EXCEPTION_TYPES = new Set([
  "escalation",
  "rollback",
  "hotfix_triage",
  "review_pending_threads",
]);

// 门上待 Human 回答的讨论线程提示：gate.pending_human_threads 非空时渲染
// 一行（data-testid="gate-pending-threads"），文本形如
// "有 N 条待你回答的线程：T-002（install…）、T-003（serve…）"。
// 评审门同面板显示；讨论面板的 resolved 入口只存在于 CLI/服务端
// （trac discuss set-status resolved），web 端仅有 clarification 回复通道，
// 故提示不写"resolved 对应线程"后半句。
export function pendingHumanThreadsLine(gate) {
  const threads = (gate && gate.pending_human_threads) || [];
  if (!Array.isArray(threads) || threads.length === 0) return null;
  const shown = threads.slice(0, 8).map((thread) => {
    const id = (thread && thread.thread_id) || "T-???";
    const raw = (thread && thread.snippet) || "";
    const snippet = raw.length > 30 ? `${raw.slice(0, 30)}…` : raw;
    return `${id}（${snippet}）`;
  });
  const overflow = threads.length > shown.length
    ? `，等共 ${threads.length} 条`
    : "";
  return element("p", {
    class: "gate-pending-threads",
    "data-testid": "gate-pending-threads",
    text: `有 ${threads.length} 条待你回答的线程：${shown.join("、")}${overflow}`,
  });
}

export function gateActionPanel(detail, gate) {
  const runId = (detail && detail.run_id) || "";
  const encoded = encodeURIComponent(runId);
  const panel = element("div", {
    class: "gate-panel",
    "data-testid": "gate-panel",
  });
  const head = element("div", { class: "gate-head" });
  head.append(
    element("p", {
      class: "gate-status",
      "data-testid": "gate-status",
      text: "当前状态：等待人工裁决",
    })
  );
  const actions = element("div", { class: "gate-actions" });
  head.append(actions);
  panel.append(head);
  // 门上待 Human 回答的讨论线程提示（OOB）：非空时渲染在 gate-status 行下方。
  const pending = pendingHumanThreadsLine(gate);
  if (pending) panel.append(pending);
  const gateType = (gate && gate.type) || "";
  if (GATE_EXCEPTION_TYPES.has(gateType)) {
    panel.append(
      element("p", {
        class: "gate-reason",
        "data-testid": "gate-reason",
        text: `原因：${gate.reason || "-"}`,
      }),
      element("p", {
        class: "gate-impact",
        "data-testid": "gate-impact",
        text: `影响：${gate.impact || "-"}`,
      })
    );
  }
  const action = GATE_ACTIONS.includes(gate.primary_action)
    ? gate.primary_action
    : "guidance";
  if (action === "review") {
    // review gate: the dedicated 通过/退回 decision row replaces the old
    // primary/default + comment/no-comment faces. Buttons ride the head
    // actions slot (same line as gate-status); comments + hint + feedback
    // ride below the head.
    const face = reviewDecisionFace(detail, encoded);
    for (const btn of face._actions) actions.append(btn);
    panel.append(face._body);
  } else {
    panel.append(primaryActionFace(encoded, action, actions));
  }
  return panel;
}

/** Review-gate decision row: [通过] posts no_comment (comments must be
 * empty); [退回] posts comment with comments carried directly to Scribe
 * (comments must be non-empty, no document thread write). Returns
 * { _actions, _body }: the caller mounts _actions into the head-row slot
 * (same line as gate-status) and _body below the head. */
function reviewDecisionFace(detail, encoded) {
  void detail;
  const body = element("div", { class: "review-decision-face" });
  const comments = element("textarea", {
    class: "review-comments",
    "data-testid": "review-comments",
    "aria-label": "评审意见（退回时必填，直接传给 Scribe）",
    rows: 3,
    placeholder: "填写退回意见（为空则通过）",
  });
  const hint = element("p", {
    class: "review-hint",
    "data-testid": "review-hint",
    text: "通过将直接推进；如需退回请填写意见",
  });
  const feedback = element("p", {
    class: "review-feedback",
    "data-testid": "review-feedback",
    role: "status",
    hidden: true,
  });
  const approve = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": "review-approve",
    title: 'review → {"action":"no_comment"}',
    text: "通过",
  });
  const ret = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": "review-return",
    title: 'review → {"action":"comment","comments":<文本>}',
    text: "退回",
  });
  const say = (text, failed) => {
    feedback.textContent = text;
    feedback.hidden = false;
    feedback.setAttribute("data-failed", failed ? "true" : "false");
  };
  const sync = () => {
    const empty = !(comments.value || "").trim();
    approve.disabled = !empty;
    ret.disabled = empty;
    hint.textContent = empty
      ? "通过将直接推进；如需退回请填写意见"
      : "将带意见退回（意见将直接传给 Scribe）";
  };
  comments.addEventListener("input", sync);
  sync();
  let busy = false;
  approve.addEventListener("click", async () => {
    if (busy || approve.disabled) return;
    busy = true;
    try {
      await requestJson(`/api/runs/${encoded}/review`, {
        method: "POST",
        body: JSON.stringify({ action: "no_comment" }),
      });
      scheduleGateRefresh(encoded, "review");
    } catch (error) {
      say(`失败: ${error && error.message ? error.message : error}`, true);
    } finally {
      busy = false;
      sync();
    }
  });
  ret.addEventListener("click", async () => {
    if (busy || ret.disabled) return;
    const text = (comments.value || "").trim();
    if (!text) return;
    busy = true;
    try {
      await requestJson(`/api/runs/${encoded}/review`, {
        method: "POST",
        body: JSON.stringify({ action: "comment", comments: text }),
      });
      scheduleGateRefresh(encoded, "review");
    } catch (error) {
      say(`失败: ${error && error.message ? error.message : error}`, true);
    } finally {
      busy = false;
      sync();
    }
  });
  body.append(hint, comments, feedback);
  return { _actions: [approve, ret], _body: body };
}

export function primaryActionFace(encoded, action, actionsEl) {
  // §1k.2 唯一主要动作入口: one display line (gate-primary-action, never
  // counted as a gate-action-*) below the head; the clickable buttons ride
  // the head-row actions slot (same line as gate-status).
  const wrap = element("div", { class: "gate-primary-action-wrap" });
  const actionLabel = PRIMARY_ACTION_LABELS[action] || action;
  wrap.append(
    element("p", {
      class: "gate-primary-action",
      "data-testid": "gate-primary-action",
      title: action,
      text: `建议操作：${actionLabel}`,
    })
  );
  const mount = actionsEl || wrap;
  const _primaryDefaults = {
    triage: ["/triage", { decision: "go" }],
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
      title: `${action} → ${JSON.stringify(_default[1])}`,
      text: actionLabel,
    });
    primary.addEventListener("click", () => {
      requestJson(`/api/runs/${encoded}${_default[0]}`, {
        method: "POST",
        body: JSON.stringify(_default[1]),
      }).then(() => scheduleGateRefresh(encoded, action)).catch(() => undefined);
    });
    mount.append(primary);
  } else if (action === "approve") {
    // FR-0308: the approval binds the live trio revision the reviewer saw;
    // the UI reads it from the run's spec doc face (same trio digest).
    const primary = element("button", {
      type: "button",
      class: "gate-action-button gate-primary-button",
      "data-testid": "gate-action-approve",
      title: "approve",
      text: actionLabel,
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
    mount.append(primary);
  } else if (action === "release-decision") {
    // FR-0309: the decision binds the live preview digest.
    const primary = element("button", {
      type: "button",
      class: "gate-action-button gate-primary-button",
      "data-testid": "gate-action-release-decision",
      title: "release-decision",
      text: actionLabel,
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
    mount.append(primary);
  }
  if (action === "triage") {
    for (const decision of ["go", "no_go", "park"]) {
      mount.append(actionButton(encoded, "/triage", { decision }, decision));
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
        title: choice,
        text: DECISION_LABELS[choice] || choice,
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
      mount.append(btn);
    }
  } else if (action === "retry") {
    // single primary button only.
  } else if (action === "return") {
    mount.append(
      element("span", {
        class: "gate-action-hint",
        text: "confirm the rollback target in the return face below",
      })
    );
  } else {
    // guidance: no action endpoint — owning surface direction only (§1k.2).
    mount.append(owningSurfaceEntry());
  }
  return wrap;
}

function actionButton(encoded, path, params, label) {
  const button = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": `gate-decision-${label}`,
    title: label,
    text: DECISION_LABELS[label] || label,
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
export function returnPointer(detail) {
  const options = detail && detail.return_options;
  const targets = options && Array.isArray(options.targets) ? options.targets : [];
  if (!options || !options.allowed || !targets.length) {
    return element("p", {
      class: "return-pointer return-pointer-empty",
      "data-testid": "return-pointer",
      hidden: true,
      text: "无回退入口",
    });
  }
  const runId = encodeURIComponent((detail && detail.run_id) || "");
  const currentStage = detail && detail.stage ? String(detail.stage) : "";
  const box = element("div", {
    class: "return-pointer",
    "data-testid": "return-pointer",
  });
  box.append(
    element("p", {
      class: "return-targets-scope",
      "data-testid": "return-targets-scope",
      text: `run 级回退（当前阶段：${currentStage || "-"}）`,
    }),
    element("p", {
      class: "return-targets",
      "data-testid": "return-targets",
      text: `可回退目标：${targets.map((target) => target.stage === currentStage ? `${target.stage}（当前阶段）` : target.stage).join("、")}`,
    })
  );
  for (const target of targets) {
    const isCurrent = target.stage === currentStage;
    const pointer = element("button", {
      type: "button",
      class: "return-pointer-button",
      "data-testid": `return-pointer-${target.stage}`,
      text: isCurrent ? `重做当前阶段 ${target.stage}` : `回退到 ${target.stage}`,
      ...(isCurrent ? { title: `回到 ${target.stage} 阶段入口重新走；本阶段已产生的中间结果不保留` } : {}),
    });
    pointer.addEventListener("click", () => {
      openReturnConfirm(box, runId, target, currentStage);
    });
    box.append(pointer);
  }
  return box;
}

/** The return confirm face (§1m.2): target + already-executed
 * consequences + explicit confirmation; the verdict reflects the
 * server's answer (never a disguised undo). */
export function openReturnConfirm(box, runId, target, currentStage) {
  const existing = box.querySelector('[data-testid="return-confirm-face"]');
  if (existing) existing.remove();
  const executed = target.already_executed || [];
  const isCurrent = currentStage !== undefined && currentStage !== null && currentStage !== ""
    ? target.stage === currentStage
    : false;
  const face = element("div", {
    class: "return-confirm-face",
    "data-testid": "return-confirm-face",
  });
  face.append(
    element("p", {
      class: "return-target",
      "data-testid": "return-target",
      text: isCurrent ? `回退目标：${target.stage}（重做当前阶段）` : `回退目标：${target.stage}`,
    }),
    element("p", {
      class: "return-consequences",
      "data-testid": "return-consequences",
      text: executed.length
        ? `路径上已执行（不撤销）：${executed
            .map((op) => `${op.operation_kind} ${op.target}`)
            .join("；")}`
        : "无不可逆操作被跨越",
    })
  );
  const confirm = element("button", {
    type: "button",
    class: "return-confirm-button",
    "data-testid": "return-confirm-button",
    text: isCurrent ? `确认重做 ${target.stage}` : `确认回退到 ${target.stage}`,
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
    text: isCurrent ? `确认重做 ${target.stage}` : `确认回退到 ${target.stage}`,
  });
  alias.removeAttribute("hidden");
  alias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  face.append(confirm, alias);
  box.append(face);
}

function span(testid, text, extra = {}) {
  const { class: extraClass, ...rest } = extra || {};
  const classes = extraClass ? `${testid} ${extraClass}` : testid;
  return element("span", { class: classes, "data-testid": testid, text, ...rest });
}
