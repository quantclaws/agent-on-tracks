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

// §1l.1 closed state set (icon shape + data-state dual channel).
// `blocked` is the operational attention.required face: the current step
// cannot proceed without an operator action, so it is distinct from both
// the machine `active` state and the human `attention` gate.
const STATE_ICONS = {
  "done": "●",
  "active": "◆",
  "pending": "○",
  "attention": "▲",
  "blocked": "■",
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
  const runningTaskId = detail && detail.stage === stage && detail.task_progress
    ? detail.task_progress.current_task_id
    : null;
  const currentSubstate = detail && detail.substate ? String(detail.substate) : "";
  const lines = ["stateDiagram-v2", "direction LR"];
  for (const node of graph.nodes) {
    const title = runningTaskId && node.key === currentSubstate
      ? `${node.title} · ${runningTaskId}`
      : node.title;
    lines.push(`state "${sanitizeMermaidText(title)}" as ${nodeIds.get(node.key)}`);
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
    if (!event) continue;
    if (
      (event.type === "stage.entered" || event.type === "stage.recovered") &&
      stageOf(event) === stage
    ) {
      start = index;
      break;
    }
    if (event.type === "stage.rolled_back") {
      const payload = event.payload || {};
      const to = payload.to_stage || payload.to || event.to_stage || event.to;
      // A rollback INTO this stage opens a new visit: the window must start
      // at the rollback, not at the previous visit's stage.entered.
      if (to === stage) {
        start = index;
        break;
      }
    }
  }
  if (start < 0) return [];
  let end = list.length;
  for (let index = start + 1; index < list.length; index += 1) {
    const event = list[index];
    if (!event) continue;
    // Any exit, rollback or a recovery into another stage ends this window.
    if (event.type === "stage.exited" || event.type === "stage.rolled_back") {
      end = index + 1;
      break;
    }
    if (event.type === "stage.recovered" && stageOf(event) !== stage) {
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
      lines: ["人工门：等待裁决"],
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

const PLAN_DIAGRAM_MAX_TASKS = 12;

/** Flowchart label safety: quoted labels still break on structural glyphs. */
function sanitizeMermaidLabel(text) {
  return sanitizeMermaidText(text)
    .replace(/\[/g, "［")
    .replace(/\]/g, "］")
    .replace(/\(/g, "（")
    .replace(/\)/g, "）")
    .replace(/\|/g, "｜");
}

/** Plan/execution map: one subgraph per batch, dependency edges between
 * tasks. The phase diagram stays the ACTUAL flow; this one is the planned
 * work units plus their live execution state. */
export function planExecutionSource(progress) {
  if (!progress || !Array.isArray(progress.batches_detail)) return null;
  const total = Number(progress.total);
  if (!Number.isFinite(total) || total <= 0 || total > PLAN_DIAGRAM_MAX_TASKS) {
    return null;
  }
  const nodeIds = new Map();
  const lines = ["flowchart LR"];
  let index = 0;
  for (let batchIndex = 0; batchIndex < progress.batches_detail.length; batchIndex += 1) {
    const group = progress.batches_detail[batchIndex] || {};
    const batch = group.batch === null || group.batch === undefined ? "-" : group.batch;
    lines.push(`subgraph b${batchIndex + 1}["批次 ${sanitizeMermaidLabel(batch)}"]`);
    for (const task of (Array.isArray(group.tasks) ? group.tasks : [])) {
      index += 1;
      const nodeId = `t${index}`;
      nodeIds.set(task.task_id, nodeId);
      const state = task.state || "pending";
      const icon = TASK_STATE_ICONS[state] || "○";
      let label = `${icon} ${task.task_id}${task.title ? ` ${task.title}` : ""}`;
      if (state === "running" && task.phase) label += ` · ${task.phase}`;
      lines.push(`  ${nodeId}["${sanitizeMermaidLabel(label)}"]`);
    }
    lines.push("end");
  }
  for (const group of progress.batches_detail) {
    for (const task of (group && Array.isArray(group.tasks) ? group.tasks : [])) {
      const to = nodeIds.get(task.task_id);
      if (!to) continue;
      for (const dep of (Array.isArray(task.depends_on) ? task.depends_on : [])) {
        const from = nodeIds.get(dep);
        if (from) lines.push(`${from} --> ${to}`);
      }
    }
  }
  lines.push("classDef done fill:#e8f5e9,stroke:#2e7d32,color:#1e4620");
  lines.push("classDef current fill:#fff4e5,stroke:#f5a623,stroke-width:2px,color:#7a4d00");
  lines.push("classDef pending fill:#f7f7f7,stroke:#d0d0d0,color:#9a9a9a");
  lines.push("classDef failed fill:#fdecea,stroke:#d93025,color:#8a1c14");
  for (const group of progress.batches_detail) {
    for (const task of (group && Array.isArray(group.tasks) ? group.tasks : [])) {
      const nodeId = nodeIds.get(task.task_id);
      if (!nodeId) continue;
      const state = task.state || "pending";
      const tone = state === "running"
        ? "current"
        : (state === "done" || state === "failed" ? state : "pending");
      lines.push(`class ${nodeId} ${tone}`);
    }
  }
  return lines.join("\n");
}

/** Render the plan/execution map beside the phase diagram. Failure is
 * non-fatal: the grouped batch rows below remain the readable fallback. */
async function renderPlanExecutionDiagram(host, progress) {
  const source = planExecutionSource(progress);
  if (!source) {
    host.hidden = true;
    return null;
  }
  try {
    await renderStageWorkflowDiagram(host, source);
  } catch (error) {
    host.hidden = true;
  }
  return source;
}

export function stageOrder(timeline) {
  return timeline && Array.isArray(timeline.stage_order) ? timeline.stage_order : [];
}

export function groupAttempts(timeline) {
  // stage -> (attempt -> [{event, index}]). The dispatch attempt lives in
  // ``command.params.attempt`` on slim rows (not on the row itself), and a
  // rollback re-entry resets the numbering, so callers filter by visit.
  const stages = new Map();
  const events = (timeline && timeline.events) || [];
  events.forEach((event, index) => {
    if (!event || event.type !== "command.issued") return;
    const params = dispatchParams(event) || {};
    const kind = dispatchKind(event);
    const stage = stageOf(event) || params.stage || null;
    if (!stage) return;
    if (kind !== "dispatch_agent" && !params.role) return;
    const raw = params.attempt !== undefined ? params.attempt : event.attempt;
    const attempt = Number(raw);
    if (!Number.isFinite(attempt)) return;
    if (!stages.has(stage)) stages.set(stage, new Map());
    const byAttempt = stages.get(stage);
    if (!byAttempt.has(attempt)) byAttempt.set(attempt, []);
    byAttempt.get(attempt).push({ event, index });
  });
  return stages;
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
    // A newer activation replaced this body while the fetches were in
    // flight: never let the stale mount close the newer run's stream or
    // append to a detached face (rapid run switching / landing focus).
    if (!body.isConnected) return null;
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

    // Upper face: the timeline panel (heading stays above it) — the page
    // return nav, the current-node card (the single run-level operation
    // zone), the horizontal stage track, the ac-chain and the last-event
    // line. Lower face: the detail panel wrapping the fixed, read-only
    // detail pane (node detail + the selected stage's workflow).
    const upper = element("section", {
      class: "timeline-panel",
      "data-testid": "timeline-panel",
    });
    upper.append(pageReturnEntry());
    upper.append(currentCard(detail, events, state, stageOrder(timeline)));
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

const VERIFY_STEP_ICONS = {
  done: "✓",
  failed: "✗",
  active: "◆",
  pending: "○",
};

/** M-VERIFY's handler chain exposes no per-step substate; the server folds
 * the chain events into freeze / local gates / version / CI / Prism so the
 * operator can read "local gates ✓ / CI ✗" at a glance. */
function verifyProgressPanel(detail) {
  const progress = detail && detail.verify_progress;
  const steps = progress && Array.isArray(progress.steps) ? progress.steps : [];
  if (!steps.length) return null;
  const wrap = element("section", {
    class: "verify-progress",
    "data-testid": "verify-progress",
    "aria-label": "M-VERIFY 子步骤进度",
  });
  wrap.append(
    element("p", {
      class: "verify-progress-title",
      text: "M-VERIFY 子步骤",
    })
  );
  const list = element("ol", { class: "verify-progress-steps" });
  for (const step of steps) {
    const state = ["done", "failed", "active", "pending"].includes(step.state)
      ? step.state
      : "pending";
    const row = element("li", {
      class: `verify-step verify-step-${state}`,
      "data-testid": `verify-step-${step.key || "unknown"}`,
      "data-state": state,
    });
    row.append(
      element("span", {
        class: "verify-step-icon",
        "aria-hidden": "true",
        text: VERIFY_STEP_ICONS[state],
      }),
      element("span", {
        class: "verify-step-label",
        text: step.label || step.key || "unknown",
      })
    );
    list.append(row);
  }
  wrap.append(list);
  return wrap;
}

/** One retry entry for an operational block. clear_evidence lets the
 * existing retry gate accept an active (not escalation) run; the recorded
 * human.retry event is the same surface the CLI writes. */
function blockedRetryButton(detail, encoded, blocked) {
  const retryable = blocked.retryable !== false;
  const button = element("button", {
    type: "button",
    class: retryable
      ? "gate-action-button gate-primary-button blocked-retry"
      : "gate-action-button blocked-retry",
    "data-testid": "blocked-retry",
    "data-retryable": retryable ? "true" : "false",
    title: retryable
      ? "重新派发当前步骤"
      : `请先按指引处理：${blocked.next || "排除阻塞原因"}，然后重试`,
    text: "重试当前步骤",
  });
  button.addEventListener("click", () => {
    button.disabled = true;
    requestJson(`/api/runs/${encoded}/retry`, {
      method: "POST",
      body: JSON.stringify({ clear_evidence: true }),
    })
      .then(() => scheduleGateRefresh(encoded, "blocked"))
      .catch(() => {
        button.disabled = false;
      });
  });
  return button;
}

/** The operational block banner: reason + detail + next instruction, with
 * retry as the primary action and the existing return modal as the
 * secondary escape. Rendered only while detail.blocked is present — the
 * server omits it once a later successful gate/stage exit resolves it. */
function blockedBanner(detail, events, stages) {
  const blocked = detail && detail.blocked;
  if (!blocked) return null;
  const encoded = encodeURIComponent((detail && detail.run_id) || "");
  const banner = element("section", {
    class: blocked.retryable === false
      ? "blocked-banner blocked-needs-action"
      : "blocked-banner",
    "data-testid": "blocked-banner",
    "data-area": blocked.area || "",
    "data-retryable": blocked.retryable === false ? "false" : "true",
  });
  banner.append(
    element("p", {
      class: "blocked-title",
      "data-testid": "blocked-reason",
      text: `当前步骤阻塞：${blocked.area || "run"} · ${blocked.reason || "attention_required"}`,
    }),
    element("p", {
      class: "blocked-detail",
      "data-testid": "blocked-detail",
      text: blocked.detail || "（无附加详情）",
    }),
    element("p", {
      class: "blocked-next",
      "data-testid": "blocked-next",
      text: `下一步：${blocked.next || "按运行日志排查后重试"}`,
    })
  );
  if (blocked.retryable === false) {
    banner.append(
      element("p", {
        class: "blocked-retry-hint",
        "data-testid": "blocked-retry-hint",
        text: "完成上述操作后，可点击重试继续当前步骤。",
      })
    );
  }
  const actions = element("div", { class: "blocked-actions" });
  actions.append(blockedRetryButton(detail, encoded, blocked));
  if (detail.return_options && detail.return_options.allowed) {
    const back = element("button", {
      type: "button",
      class: "gate-action-button",
      "data-testid": "blocked-return",
      title: "选择回退到更早阶段",
      text: "回退…",
    });
    back.addEventListener("click", () => {
      openReturnModal(detail, events, encoded, stages);
    });
    actions.append(back);
  }
  banner.append(actions);
  return banner;
}

/** The rolling summary card above the track AND the single run-level
 * operation zone (gate actions, run-level return, owning-surface jump).
 * The card always describes the CURRENT stage (detail.stage); selecting a
 * historical node only changes the read-only detail pane below. */
function currentCard(detail, events, state, stages = []) {
  const firstTs = events.length ? events[0].ts : null;
  const ownerRaw = ownerRawOf(detail);
  const ownerDisplay = ownerOf(detail);
  const attempt = attemptCount(events);
  const gate = detail && detail.awaiting_gate;
  const awaiting = !!gate;
  const stage = (detail && detail.stage) || "-";
  const substate = (detail && detail.substate) || (detail && detail.control_state) || "-";
  const control = detail && detail.control_state;
  const waitBasis = lastBusinessEvent(events || []);
  const wait = awaiting ? `已等待 ${waitingElapsed(waitBasis)}` : "";
  const controlNote = control && control !== substate ? control : "";
  const card = element("section", {
    class: "current-card",
    "data-testid": "current-node-card",
    ...(detail && detail.run_id ? { "data-run-id": detail.run_id } : {}),
  });
  card.append(
    element("p", {
      class: "card-status",
      "data-testid": "card-status",
      text: `当前阶段：${stage} · ${substate}`,
    })
  );
  const metrics = [
    span("card-owner", ownerDisplay, { title: ownerRaw }),
    span("card-attempt", `累计派发 ${attempt} 次`, { title: "本 run 已发生的派发次数" }),
    span("card-duration", durationOf(firstTs), { title: "自 run 开始以来的已运行时间" }),
  ];
  // card-action is a stable card field (v0.12 contract): the wait while
  // awaiting, otherwise the run's control state. No duplicate of the stage.
  const actionText = awaiting
    ? wait || "等待人工决定"
    : controlNote || "推进中";
  metrics.push(span("card-action", actionText, { title: "等待时间 / 控制状态" }));
  if (awaiting && controlNote) {
    metrics.push(
      span("card-control", `控制状态：${controlNote}`, { title: "run 的控制状态" })
    );
  }
  card.append(element("div", { class: "card-metrics" }, metrics));
  const blockedPanel = blockedBanner(detail, events, stages);
  if (blockedPanel) card.append(blockedPanel);
  const progressPanel = verifyProgressPanel(detail);
  if (progressPanel) card.append(progressPanel);
  const gateAction = gate
    ? (GATE_ACTIONS.includes(gate.primary_action) ? gate.primary_action : "guidance")
    : null;
  if (gate) card.append(gateActionPanel(detail, gate, events));
  const options = detail && detail.return_options;
  // A rollback gate (M-TEST/M-IMPL) is answered by `approve`, not `return`;
  // the web worker cannot bind the CLI --to choice, so surface the exact
  // escape instead of leaving a dead primary action.
  if (gate && gate.type === "rollback" && (!options || !options.allowed)) {
    card.append(
      element("p", {
        class: "gate-rollback-guidance",
        "data-testid": "gate-rollback-guidance",
        text: stage === "M-IMPL"
          ? "回滚门需要 CLI 确认目标：trac approve --to M-ACC|M-SPEC|M-DESIGN"
          : "回滚门需要 CLI 确认：trac approve（M-TEST 使用失败类型预设的回滚目标）",
      })
    );
  }
  // guidance gates already mount the owning-surface entry inside their
  // action face; every other face gets exactly one explicit entry here.
  if (gateAction !== "guidance") {
    card.append(element("div", { class: "card-links" }, [owningSurfaceEntry()]));
  }
  return card;
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
  // Count real dispatches: the slim face carries the attempt inside
  // ``command.params`` (the row-level ``attempt`` is usually null), and the
  // per-stage numbering resets on rollback — the card's "尝试 N 次" is the
  // run's total dispatch count.
  let count = 0;
  for (const event of events || []) {
    if (!event || event.type !== "command.issued") continue;
    const params = dispatchParams(event) || {};
    if (dispatchKind(event) === "dispatch_agent" || params.role) count += 1;
  }
  return count || 1;
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

/** Last verdict.failed payload in the visible window (the raw technical
 * evidence lives ONLY in the folded details; the headline is human). */
function lastFailure(events) {
  const list = Array.isArray(events) ? events : [];
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const event = list[index];
    if (event && event.type === "verdict.failed") {
      return event.payload && typeof event.payload === "object" ? event.payload : {};
    }
  }
  return null;
}

/** Event types that supersede an earlier failure inside the same window.
 * The failure banner is current-state feedback, not history: once later
 * progress lands, the old verdict is no longer the stage's headline. */
const FAILURE_SUPERSEDING_EVENTS = new Set([
  "verdict.passed",
  "task.completed",
  "task.started",
  "stage.entered",
  "stage.rolled_back",
  "human.retry",
]);

/** The latest verdict.failed payload that has not been superseded by later
 * progress in the same window; null when the stage moved on. */
function currentFailure(events) {
  const list = Array.isArray(events) ? events : [];
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const event = list[index];
    if (!event) continue;
    if (event.type === "verdict.failed") {
      return event.payload && typeof event.payload === "object" ? event.payload : {};
    }
    if (FAILURE_SUPERSEDING_EVENTS.has(event.type)) return null;
  }
  return null;
}

/** One-sentence, cause-only failure summary. The raw check/evidence stays
 * behind the "技术详情" fold; the summary never tells the human what to do. */
function failureSummary(failure) {
  if (!failure) return "";
  const check = String(failure.check || "");
  const reason = String(failure.reason || "");
  if (check === "island" || /missing from closure|reach check failed/.test(reason)) {
    const count = reason
      .split(";")
      .filter((part) => /missing from closure/.test(part)).length;
    if (count > 0) {
      return `实现计划没有覆盖全部接口契约（${count} 个接口未被任务覆盖）。`;
    }
    if (/reach|unreachable|not reachable/i.test(reason)) {
      return "实现计划的可达性校验未通过：部分任务无法从入口到达。";
    }
    return "实现计划的接口闭包校验未通过。";
  }
  if (check === "test_tasks") {
    return "计划任务校验未通过。";
  }
  if (check === "test_defect" || check === "full_suite") {
    return "测试或实现校验未通过（测试失败）。";
  }
  if (check === "lineage") {
    return "阶段追溯/血缘校验未通过。";
  }
  if (["reply_format_error", "manifest_malformed", "evidence_malformed"].includes(check)) {
    return "Agent 回复格式不符合协议（通常是机械性错误）。";
  }
  if (check === "tasks_md_projection") {
    return "计划文件生成方式不正确（tasks.md 应由 tasks.json 自动生成，不能手工修改）。";
  }
  if (check === "test_freeze_contamination") {
    return "测试基线被未提交的测试改动阻止冻结。";
  }
  if (check === "collect_defect" || check === "contract_error") {
    return "测试执行没有产出可读结果（环境或采集问题）。";
  }
  if (check === "trace") {
    return "阶段追溯校验未通过。";
  }
  return "本阶段校验未通过。";
}

/** Runtime-status line for an active (not awaiting) run: it reports the
 * failure without pretending the human has a pending decision. */
function activeStatusLine(failure, diagnosis) {
  const summary = failureSummary(failure);
  if (!summary) return "";
  const attempts = diagnosis && Number.isFinite(Number(diagnosis.attempts))
    ? Number(diagnosis.attempts)
    : Number(failure && failure.attempt);
  const retrying = !diagnosis || diagnosis.retry_worthy !== false;
  if (!retrying) return `上一轮失败：${summary}`;
  if (Number.isFinite(attempts) && attempts > 0) {
    return `第 ${attempts} 次尝试失败：${summary}。runtime 正在自动重试。`;
  }
  return `上一轮失败：${summary}。runtime 正在自动重试。`;
}

const DIAGNOSIS_ACTION_LABELS = {
  retry: "重试当前步骤",
  replan: "重新规划实现任务",
  redesign: "重新设计",
  return_m_test: "回到 M-TEST 阶段重新处理测试",
  unknown: "暂无明确建议",
};

/** The gate decision brief: root cause, attempts, convergence judgement,
 * the last agent's advice and a suggested path. Suggestions never widen
 * the legal return targets (return_options still owns that set). */
function diagnosisBrief(diagnosis) {
  const brief = element("div", {
    class: "gate-diagnosis",
    "data-testid": "gate-diagnosis",
  });
  const attempts = Number.isFinite(Number(diagnosis.attempts))
    ? Number(diagnosis.attempts)
    : 0;
  const max = Number.isFinite(Number(diagnosis.attempts_max))
    ? Number(diagnosis.attempts_max)
    : 3;
  brief.append(
    element("p", {
      class: "gate-diagnosis-cause",
      "data-testid": "gate-diagnosis-cause",
      text: `根因：${diagnosis.cause_label || "本阶段校验未通过"}`,
    }),
    element("p", {
      class: "gate-diagnosis-attempts",
      "data-testid": "gate-diagnosis-attempts",
      text: `已用尝试：${attempts}/${max} 次`,
    }),
    element("p", {
      class: "gate-diagnosis-retry",
      "data-testid": "gate-diagnosis-retry",
      text: diagnosis.retry_worthy === false
        ? `重试预计不会改变结果：${diagnosis.retry_reason || "失败原因未收敛"}`
        : `重试可能改变结果：${diagnosis.retry_reason || "尚未形成相同失败签名"}`,
    })
  );
  if (diagnosis.agent_advice) {
    brief.append(
      element("p", {
        class: "gate-diagnosis-advice",
        "data-testid": "gate-diagnosis-advice",
        text: `上一轮 agent 建议：${diagnosis.agent_advice}`,
      })
    );
  }
  const path = DIAGNOSIS_ACTION_LABELS[diagnosis.suggested_action] || DIAGNOSIS_ACTION_LABELS.unknown;
  const candidates = Array.isArray(diagnosis.return_candidates)
    ? diagnosis.return_candidates.filter((stage) => typeof stage === "string" && stage)
    : [];
  const stages = candidates.length ? `（建议回退到 ${candidates.join("、")}）` : "";
  brief.append(
    element("p", {
      class: "gate-diagnosis-suggestion",
      "data-testid": "gate-diagnosis-suggestion",
      text: `建议路径：${path}${stages}。`,
    })
  );
  return brief;
}

function gateSummary(gate, events) {
  const failure = currentFailure(events);
  if (gate && gate.type === "escalation") {
    const cause = failureSummary(failure);
    return cause
      ? `${cause}需要你决定：重试当前步骤，或回退到更早阶段。`
      : "本阶段失败，需要你决定：重试当前步骤，或回退到更早阶段重做。";
  }
  if (gate && gate.type === "rollback") return "本阶段需要确认回退到哪个阶段。";
  if (gate && gate.type === "approval") return "请批准本轮基线，或退回修改。";
  if (gate && gate.type === "review") return "请评审本阶段文档：通过，或退回修改。";
  if (gate && gate.type === "triage") return "请裁决本 story 是否进入开发。";
  if (gate && gate.type === "hotfix_triage") return "请确认这个 hotfix 应该归属到哪个阶段。";
  if (gate && gate.type === "AWAIT_RELEASE") return "请选择发布决策。";
  return "需要你做出决定后，run 才会继续推进。";
}

function formatSpan(totalSeconds) {
  const total = Math.max(0, Math.round(totalSeconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const parts = [];
  if (days) parts.push(`${days}天`);
  if (hours) parts.push(`${hours}小时`);
  if (minutes) parts.push(`${minutes}分`);
  if (!parts.length) parts.push(`${Math.max(1, total)}秒`);
  return parts.join("");
}

/** What a human loses when returning to a stage (code/tests/docs), in
 * plain language — the confirm face must not disguise a redo as an undo. */
function returnConsequences(target) {
  if (target === "M-TEST") {
    return "保留设计与计划，重新运行测试与实现；当前阶段已写的代码会作废并重做。";
  }
  if (target === "M-DESIGN") {
    return "保留需求与验收；设计、测试与实现作废并重做。";
  }
  if (target === "M-ACC") {
    return "保留 story 与 spec；验收、设计、测试与实现作废并重做。";
  }
  if (target === "M-SPEC") {
    return "保留 story；spec、验收、设计、测试与实现作废并重做。";
  }
  if (target === "M-STORY") {
    return "story、spec、验收、设计、测试与实现全部作废并重做。";
  }
  if (target === "M-IMPL") {
    return "保留设计与测试；实现及后续发布阶段作废并重做。";
  }
  return "目标阶段及其之后的产出全部作废并重做。";
}

/** Rough redo cost from this run's own stage durations (last visits).
 * Never a precise promise: the label says 粗略估计. */
function estimateReturnCost(events, target, current, order) {
  const stages = Array.isArray(order) ? order : [];
  const start = stages.indexOf(target);
  const end = stages.indexOf(current);
  if (start < 0 || end < 0 || end < start) return "";
  let total = 0;
  let known = 0;
  for (const stage of stages.slice(start, end + 1)) {
    let entered = null;
    let exited = null;
    for (const event of events || []) {
      if (!event) continue;
      if (event.type === "stage.entered" && stageOf(event) === stage) entered = event.ts;
      if (event.type === "stage.exited" && stageOf(event) === stage) exited = event.ts;
    }
    if (!entered) continue;
    const from = Date.parse(entered);
    const to = exited ? Date.parse(exited) : Date.now();
    if (Number.isNaN(from) || Number.isNaN(to) || to < from) continue;
    total += (to - from) / 1000;
    known += 1;
  }
  if (!known) return "预计需要重走若干阶段（暂无历史耗时数据）";
  return `粗略估计：约 ${formatSpan(total)}（含历史等待，实际取决于评审与执行）`;
}

let returnModalFocus = null;
let returnModalKeydown = null;

function closeReturnModal() {
  const existing = document.querySelector('[data-testid="return-modal"]');
  if (existing) existing.remove();
  if (returnModalKeydown) {
    document.removeEventListener("keydown", returnModalKeydown);
    returnModalKeydown = null;
  }
  if (returnModalFocus && typeof returnModalFocus.focus === "function") {
    returnModalFocus.focus();
  }
  returnModalFocus = null;
}

/** The return decision is a modal, not a row of buttons: pick a target,
 * read what is lost (code/tests/docs), the rough redo cost and any
 * irreversible operations, then confirm. */
function openReturnModal(detail, events, encoded, order) {
  const options = detail && detail.return_options;
  const targets =
    options && options.allowed && Array.isArray(options.targets) ? options.targets : [];
  if (!targets.length) return;
  closeReturnModal();
  const backdrop = element("div", {
    class: "return-modal-backdrop",
    "data-testid": "return-modal",
    role: "presentation",
  });
  const modal = element("div", {
    class: "return-modal",
    "data-testid": "return-modal-dialog",
    role: "dialog",
    "aria-modal": "true",
    "aria-labelledby": "return-modal-title",
    tabindex: "-1",
  });
  const current = detail && detail.stage ? String(detail.stage) : "";
  // No destructive default: the Human must choose a target explicitly.
  let selected = null;
  const title = element("h2", {
    id: "return-modal-title",
    class: "return-modal-title",
    text: "回退到更早阶段",
  });
  const close = element("button", {
    type: "button",
    class: "return-modal-close",
    "data-testid": "return-modal-close",
    "aria-label": "关闭",
    text: "×",
  });
  close.addEventListener("click", closeReturnModal);
  const intro = element("p", {
    class: "return-modal-intro",
    text: "回退会作废目标阶段及其之后的所有产出，并从该阶段重新执行。请选择一个目标；后果与耗时估计如下。",
  });
  const list = element("div", { class: "return-target-list", "data-testid": "return-target-list" });
  const face = element("div", { class: "return-confirm-face", "data-testid": "return-confirm-face" });
  const targetLine = element("p", { class: "return-target", "data-testid": "return-target" });
  const consequences = element("p", {
    class: "return-consequences",
    "data-testid": "return-consequences",
  });
  const verdict = element("p", {
    class: "return-verdict",
    "data-testid": "return-verdict",
    role: "status",
    hidden: true,
  });
  const confirm = element("button", {
    type: "button",
    class: "gate-action-button return-confirm-button",
    "data-testid": "return-confirm-button",
    text: "确认回退",
  });
  const cancel = element("button", {
    type: "button",
    class: "gate-action-button",
    "data-testid": "return-cancel",
    text: "取消",
  });
  const renderSelection = () => {
    if (!selected) {
      targetLine.textContent = "请选择一个回退目标";
      consequences.textContent = "选择后显示后果与耗时估计。";
      confirm.disabled = true;
      confirm.textContent = "确认回退";
      return;
    }
    targetLine.textContent = `将回退到 ${selected.stage}`;
    confirm.disabled = false;
    const cost = estimateReturnCost(events, selected.stage, current, order);
    const executed = Array.isArray(selected.already_executed) ? selected.already_executed : [];
    const span = order.indexOf(selected.stage) >= 0 && order.indexOf(current) >= 0
      ? `需重走 ${Math.max(0, order.indexOf(current) - order.indexOf(selected.stage) + 1)} 个阶段。`
      : "";
    consequences.textContent =
      `${span}${cost ? `${cost}。` : ""}` +
      (executed.length
        ? ` 已执行且不可撤销：${executed
            .map((op) => `${op.operation_kind} ${op.target}`)
            .join("；")}。`
        : " 无不可逆操作被跨越。");
    confirm.textContent = `确认回退到 ${selected.stage}`;
  };
  for (const target of targets) {
    const option = element("label", {
      class: "return-target-option",
      "data-testid": `return-target-option-${target.stage}`,
    });
    const radio = element("input", {
      type: "radio",
      name: "return-target",
      value: target.stage,
    });
    radio.checked = false;
    radio.addEventListener("change", () => {
      selected = target;
      renderSelection();
    });
    option.append(
      radio,
      element("span", { class: "return-target-name", text: target.stage }),
      element("span", {
        class: "return-target-consequence",
        text: returnConsequences(target.stage),
      })
    );
    list.append(option);
  }
  renderSelection();
  confirm.addEventListener("click", async () => {
    confirm.disabled = true;
    try {
      await requestJson(`/api/runs/${encoded}/return`, {
        method: "POST",
        body: JSON.stringify({
          to: selected.stage,
          reason: "human return from UI",
          confirm: true,
        }),
      });
      verdict.hidden = false;
      verdict.removeAttribute("data-failed");
      verdict.textContent = `已提交回退：${selected.stage}`;
      scheduleGateRefresh(encoded, "return");
      globalThis.setTimeout(closeReturnModal, 800);
    } catch (error) {
      verdict.hidden = false;
      verdict.setAttribute("data-failed", "true");
      verdict.textContent = `回退失败：${error && error.message ? error.message : error}`;
      confirm.disabled = false;
    }
  });
  cancel.addEventListener("click", closeReturnModal);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) closeReturnModal();
  });
  face.append(
    targetLine,
    consequences,
    element("div", { class: "return-confirm-actions" }, [cancel, confirm]),
    verdict
  );
  modal.append(title, close, intro, list, face);
  backdrop.append(modal);
  returnModalFocus = document.activeElement;
  returnModalKeydown = (event) => {
    if (event.key === "Escape") closeReturnModal();
  };
  document.addEventListener("keydown", returnModalKeydown);
  document.body.append(backdrop);
  modal.focus();
}

/**
 * Fold the event order into per-stage VISITS (a stage can be entered more
 * than once through a rollback) plus the real back edges a rollback
 * produced (来源→目标, from the stage the run left to the target it
 * returned to).
 *
 * A rollback of a stage onto itself, or an operator repair (payload
 * ``operator_repair``), is a repair note — never a void visit and never a
 * back edge.
 */
function foldStageFacts(events) {
  const visits = new Map();
  const backEdges = [];
  let current = null;
  const openVisit = (stage, index) => {
    if (!visits.has(stage)) visits.set(stage, []);
    visits.get(stage).push({ state: "open", startIndex: index, endIndex: null });
  };
  const closeVisit = (stage, state, index) => {
    const list = visits.get(stage);
    if (!list || !list.length) return;
    const latest = list[list.length - 1];
    // exit closes an OPEN visit only; a late exit never revives a visit
    // that a rollback already voided. A rollback marks the latest visit.
    if (state === "done" && latest.state !== "open") return;
    latest.state = state;
    latest.endIndex = index;
  };
  const list = Array.isArray(events) ? events : [];
  for (let index = 0; index < list.length; index += 1) {
    const event = list[index];
    if (!event) continue;
    if (event.type === "stage.entered" || event.type === "stage.recovered") {
      const stage = stageOf(event) || current;
      if (!stage) continue;
      if (event.type === "stage.recovered") {
        // stage.recovered is a real entry boundary (kernel maps it onto
        // _on_stage_entered): close the stage we came from, then open the
        // recovered stage's visit. Without this the next rollback's
        // from_stage fell back to a stale ``current`` and the edge vanished.
        const payload = event.payload || {};
        const from = payload.from_stage || payload.from || event.from_stage || current;
        if (from && from !== stage) closeVisit(from, "done", index);
      }
      openVisit(stage, index);
      current = stage;
    } else if (event.type === "stage.exited") {
      const stage = stageOf(event) || current;
      if (stage) closeVisit(stage, "done", index);
    } else if (event.type === "stage.rolled_back") {
      const payload = event.payload || {};
      const from =
        payload.from_stage || payload.from || event.from_stage || event.from || current;
      const to =
        payload.to_stage || payload.to || event.to_stage || event.to || null;
      const reason =
        typeof payload.reason === "string"
          ? payload.reason
          : (typeof payload.summary === "string" ? payload.summary : null);
      const repair = !!(payload.operator_repair || event.operator_repair);
      const human = reason === "human_return";
      if (repair || (from && to && from === to && human)) {
        // Operator repair / human re-do of the current stage: a note on the
        // live visit, never a void and never a back edge.
        continue;
      }
      if (from) closeVisit(from, "void", index);
      if (from && to) {
        // A self rollback (from === to, e.g. an automatic diagnose re-entry)
        // voids the old visit and opens a new one; a back-edge to the same
        // node has no meaningful geometry, so only real past edges render.
        if (from !== to) backEdges.push({ from, to, reason, index });
        openVisit(to, index);
        current = to;
      }
    }
  }
  return { visits, backEdges };
}

function nodeStateOf(stage, detail, facts) {
  const activeStage = detail && detail.stage;
  if (stage === activeStage) {
    // The current stage is never void: a same-stage repair/rollback is a
    // note on the live visit, not an invalidation of where the run is.
    // An unresolved attention.required is an operational block, distinct
    // from the human gate's `attention` face.
    if (detail && detail.blocked) return "blocked";
    const gate = detail && detail.awaiting_gate;
    return gate ? "attention" : "active";
  }
  const list = facts.visits.get(stage) || [];
  if (!list.length) return "pending";
  const latest = list[list.length - 1];
  if (latest.state === "void") return "void";
  if (latest.state === "done") return "done";
  return "pending";
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
      ...(nodeState === "active" || nodeState === "attention" || nodeState === "blocked"
        ? { "data-active": "true", "data-state-active": "true" }
        : {}),
      "aria-label": `${stage} (${nodeState})`,
    });
    // A native button inside the list item carries selection/keyboard; the
    // retry/return buttons are SIBLINGS, never nested in a role=button
    // element (screen readers would flatten them).
    const select = element("button", {
      type: "button",
      class: "stage-node-select",
      "aria-pressed": "false",
      "aria-label": `${stage} (${nodeState})`,
    });
    select.append(
      element("span", { class: "node-icon", text: STATE_ICONS[nodeState] || "·" }),
      element("span", { class: "stage-label", text: stage })
    );
    node.append(select);
    // One compact attempt badge (the count), not one chip per attempt: a
    // rollback re-entry resets the numbering, so only dispatches at/after
    // the current visit's start count.
    const byAttempt = attempts.get(stage) || new Map();
    const visit = (facts.visits.get(stage) || []).slice(-1)[0] || null;
    const visitStart = visit ? visit.startIndex : -1;
    const stageAttempts = [];
    for (const [attempt, entries] of byAttempt) {
      if (entries.some((entry) => visitStart < 0 || entry.index >= visitStart)) {
        stageAttempts.push(attempt);
      }
    }
    const attemptTotal = stageAttempts.length;
    if (attemptTotal > 1) {
      const numeric = stageAttempts.filter((value) => Number.isFinite(value));
      const latest = numeric.length ? Math.max(...numeric) : attemptTotal;
      node.append(
        element("span", {
          class: "stage-attempt stage-attempt-badge",
          "data-testid": `timeline-attempt-${stage}-${latest}`,
          title: `本阶段已尝试 ${attemptTotal} 次`,
          text: `⟳ ${attemptTotal}`,
        })
      );
    }
    // Stage-level decisions live on the active/attention node: retry
    // restarts THIS stage; return opens the target/consequence modal.
    const isCurrent = stage === (detail && detail.stage);
    const gate = detail && detail.awaiting_gate;
    const blocked = detail && detail.blocked;
    const encoded = encodeURIComponent((detail && detail.run_id) || "");
    const nodeActions = element("div", { class: "stage-node-actions" });
    nodeActions.addEventListener("keydown", (event) => event.stopPropagation());
    const gateRetry = gate && gate.primary_action === "retry";
    if (isCurrent && (gateRetry || blocked)) {
      const retryWorthy = blocked
        ? blocked.retryable !== false
        : !(gate.diagnosis && gate.diagnosis.retry_worthy === false);
      const retry = element("button", {
        type: "button",
        class: "stage-node-action",
        "data-testid": "stage-action-retry",
        title: blocked && !retryWorthy
          ? `请先按指引处理：${blocked.next || "排除阻塞原因"}，然后重试`
          : retryWorthy
            ? "重新派发当前步骤（保留本阶段已产出）"
            : "重试预计不会改变结果；建议改用「回退…」选择更早阶段",
        ...(retryWorthy ? {} : { "data-retry-worthy": "false" }),
        ...(blocked ? { "data-retryable": retryWorthy ? "true" : "false" } : {}),
        text: "重试当前步骤",
      });
      const body = blocked ? { clear_evidence: true } : {};
      retry.addEventListener("click", (event) => {
        event.stopPropagation();
        retry.disabled = true;
        requestJson(`/api/runs/${encoded}/retry`, {
          method: "POST",
          body: JSON.stringify(body),
        })
          .then(() => scheduleGateRefresh(encoded, blocked ? "blocked" : "retry"))
          .catch(() => {
            retry.disabled = false;
          });
      });
      nodeActions.append(retry);
    }
    if (
      isCurrent &&
      detail &&
      detail.return_options &&
      detail.return_options.allowed
    ) {
      const back = element("button", {
        type: "button",
        class: "stage-node-action",
        "data-testid": "return-pointer-open",
        title: "选择回退到更早阶段",
        text: "回退…",
      });
      back.addEventListener("click", (event) => {
        event.stopPropagation();
        openReturnModal(detail, events, encoded, stages);
      });
      nodeActions.append(back);
    }
    if (nodeActions.childElementCount) node.append(nodeActions);
    node.addEventListener("click", (event) => {
      if (event.target.closest(".stage-node-actions")) return;
      selectNode(node, stage, detail, events);
    });
    track.append(node);
  });
  if (!stages.length) {
    track.append(element("li", { class: "stage-node", text: "no stages" }));
  }
  // §1l.1: the initial view centers the active node inside the viewport
  // and keeps its neighbourhood reachable (all history stays navigable).
  // The pane opens on the current node's read-only detail WITHOUT marking
  // it selected — 选中查看 and 当前活跃 are two markers.
  const stack = element("div", { class: "stage-track-stack" });
  stack.append(track);
  const backLayer = backEdgeLayer(facts.backEdges, track);
  if (backLayer) stack.append(backLayer);
  viewport.append(stack);
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

const SVG_NS = "http://www.w3.org/2000/svg";

function svgElement(tag, attributes = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(name, String(value));
  }
  return node;
}

/**
 * Real rollback edges ride an SVG layer DIRECTLY BELOW the track: each
 * edge is a return line from the source node to the target node with the
 * server-recorded reason as its label. The layer lives inside the same
 * horizontally scrolling content as the track, so it follows the scroll;
 * it re-measures on window resize and track reflow.
 */
let backEdgeSeq = 0;

function backEdgeLayer(edges, track) {
  if (!edges.length) return null;
  const layer = svgElement("svg", {
    class: "stage-back-edges",
    "data-testid": "timeline-back-edges",
    role: "group",
    "aria-label": "回退线",
  });
  const height = 40;
  const markerId = `timeline-back-arrow-${(backEdgeSeq += 1)}`;
  let frame = 0;
  let observer = null;
  const detachIfStale = () => {
    if (layer.isConnected) return false;
    globalThis.removeEventListener("resize", schedule);
    if (observer) {
      try {
        observer.disconnect();
      } catch (error) {
        /* already disconnected */
      }
      observer = null;
    }
    return true;
  };
  const layout = () => {
    if (detachIfStale()) return;
    const trackRect = track.getBoundingClientRect();
    const width = Math.max(
      1,
      Math.round(trackRect.width),
      Math.round(track.scrollWidth || 0)
    );
    layer.setAttribute("width", String(width));
    layer.setAttribute("height", String(height));
    layer.setAttribute("viewBox", `0 0 ${width} ${height}`);
    layer.replaceChildren();
    const marker = svgElement("marker", {
      id: markerId,
      markerWidth: "6",
      markerHeight: "6",
      refX: "3",
      refY: "3",
      orient: "auto",
    });
    marker.append(svgElement("path", {
      d: "M0,0 L6,3 L0,6 z",
      class: "back-edge-arrow",
    }));
    const defs = svgElement("defs");
    defs.append(marker);
    layer.append(defs);
    const nodes = new Map();
    for (const node of track.querySelectorAll('[data-testid^="timeline-node-"]')) {
      const stage = node.getAttribute("data-stage");
      if (stage) nodes.set(stage, node);
    }
    for (const edge of edges) {
      const from = nodes.get(edge.from);
      const to = nodes.get(edge.to);
      if (!from || !to) continue;
      const fr = from.getBoundingClientRect();
      const tr = to.getBoundingClientRect();
      const sx = fr.left - trackRect.left + fr.width / 2;
      const tx = tr.left - trackRect.left + tr.width / 2;
      const group = svgElement("g", {
        class: "back-edge-group",
        "data-testid": `timeline-back-edge-${edge.from}-${edge.to}`,
      });
      const title = svgElement("title");
      title.textContent = `↩ ${edge.from} → ${edge.to}（${edge.reason || "退回"}）`;
      group.append(title);
      group.append(svgElement("path", {
        class: "back-edge-path",
        d: `M ${sx} 2 C ${sx} 32, ${tx} 32, ${tx} 2`,
        "marker-end": `url(#${markerId})`,
      }));
      const label = svgElement("text", {
        class: "back-edge-label",
        x: String((sx + tx) / 2),
        y: "37",
        "text-anchor": "middle",
      });
      label.textContent = `↩ ${edge.reason || "退回"}`;
      group.append(label);
      layer.append(group);
    }
  };
  const schedule = () => {
    if (typeof globalThis.requestAnimationFrame === "function") {
      if (frame) return;
      frame = globalThis.requestAnimationFrame(() => {
        frame = 0;
        layout();
      });
      return;
    }
    layout();
  };
  globalThis.addEventListener("resize", schedule);
  if (typeof globalThis.ResizeObserver === "function") {
    try {
      observer = new globalThis.ResizeObserver(() => {
        if (detachIfStale()) return;
        schedule();
      });
      observer.observe(track);
    } catch (error) {
      /* resize listener still repositions the layer */
    }
  }
  schedule();
  return layer;
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
    const otherSelect = other.querySelector(".stage-node-select");
    if (otherSelect) otherSelect.setAttribute("aria-pressed", "false");
    other.classList.remove("stage-node-selected");
  }
  node.setAttribute("data-state-selected", "true");
  const select = node.querySelector(".stage-node-select");
  if (select) select.setAttribute("aria-pressed", "true");
  // the visible selection face (same marker triple as the sidebar focus):
  // data-selected + class hook + an aria mark so the choice reads aloud.
  node.setAttribute("data-selected", "true");
  node.classList.add("stage-node-selected");
  // the active legibility marker (§1l.1 data-state channel): the node's
  // own data-state already names it; expose the frozen alias attribute
  // so the active mark is queryable without parsing data-state.
  for (const candidate of view.querySelectorAll(
    "[data-state='active'],[data-state='attention'],[data-state='blocked']"
  ) || []) {
    candidate.setAttribute("data-state-active", "true");
  }
  if (host) {
    // the pane rebuilds around the SELECTED stage and stays READ-ONLY:
    // node detail + that stage's actual workflow. Run-level operations
    // live in the current-card above, never under a selected node.
    host.replaceChildren(
      nodeDetail(stage, detail, events),
      stagePathPanel(detail, events, stage),
    );
  }
}

const TASK_STATE_ICONS = {
  done: "✓",
  running: "●",
  pending: "○",
  failed: "✕",
};

function taskProgressLine(task) {
  const state = task.state || "pending";
  const title = task.title ? ` ${task.title}` : "";
  let line = `${TASK_STATE_ICONS[state] || "○"} ${task.task_id}${title}`;
  if (state === "running" && task.phase) {
    line += ` · ${task.phase}`;
  } else if (state === "pending") {
    line += " · 等待";
  } else if (state === "failed") {
    line += " · 失败";
  }
  return line;
}

/** Orthogonal M-IMPL work-unit progress: batches read as rows beside the
 * phase pipeline (the state diagram keeps owning phase transitions). */
function mImplBatchProgress(progress) {
  if (!progress || !Array.isArray(progress.batches_detail)) return null;
  const box = element("div", {
    class: "m-impl-batch-progress",
    "data-testid": "m-impl-batch-progress",
  });
  const done = Number.isFinite(Number(progress.done)) ? Number(progress.done) : 0;
  const total = Number.isFinite(Number(progress.total)) ? Number(progress.total) : 0;
  const batch = progress.batch === null || progress.batch === undefined ? "-" : progress.batch;
  const batches = Number.isFinite(Number(progress.batches))
    ? Number(progress.batches)
    : 0;
  let summary = `批次 ${batch}/${batches} · 任务 ${done}/${total} 完成`;
  if (progress.current_task_id) {
    const phase = progress.current_phase ? `（${progress.current_phase}）` : "";
    summary += ` · 当前 ${progress.current_task_id}${phase}`;
  }
  box.append(element("p", {
    class: "m-impl-plan-caption",
    "data-testid": "m-impl-plan-caption",
    text: "相位流转（实际）· 批次/任务（计划与执行）",
  }));
  box.append(element("p", {
    class: "detail-line m-impl-batch-summary",
    "data-testid": "m-impl-batch-summary",
    text: summary,
  }));
  const showPlanDiagram = total > 0 && total <= PLAN_DIAGRAM_MAX_TASKS;
  if (showPlanDiagram) {
    const host = element("div", {
      class: "stage-workflow-diagram m-impl-plan-diagram",
      "data-testid": "m-impl-plan-diagram",
    });
    box.append(host);
    void renderPlanExecutionDiagram(host, progress);
    box.append(element("p", {
      class: "m-impl-plan-legend",
      "data-testid": "m-impl-plan-legend",
      text: "✓ 完成 · ● 进行中 · ○ 等待 · ✕ 失败",
    }));
  }
  const list = element("div", {
    class: showPlanDiagram
      ? "m-impl-batch-list m-impl-batch-fallback"
      : "m-impl-batch-list",
  });
  for (const group of progress.batches_detail) {
    const batchValue = group && group.batch !== null && group.batch !== undefined
      ? group.batch
      : "-";
    const row = element("div", {
      class: "m-impl-batch",
      "data-testid": `m-impl-batch-${batchValue}`,
    });
    row.append(element("p", {
      class: "m-impl-batch-title",
      text: `批次 ${batchValue}`,
    }));
    const tasks = element("ul", { class: "m-impl-batch-tasks" });
    for (const task of (group && Array.isArray(group.tasks) ? group.tasks : [])) {
      const state = task.state || "pending";
      tasks.append(element("li", {
        class: `m-impl-task task-${state}`,
        "data-testid": `m-impl-task-${task.task_id || "unknown"}`,
        text: taskProgressLine(task),
      }));
    }
    row.append(tasks);
    list.append(row);
  }
  box.append(list);
  return box;
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
    if (stage === "M-IMPL" && detail && detail.task_progress) {
      const progress = mImplBatchProgress(detail.task_progress);
      if (progress) box.append(progress);
    }
    const failure = currentFailure(window);
    // The gate stage's decision summary already lives in the current-card;
    // do not repeat it here (the detail pane stays stage-scoped).
    const isGateStage = !!(detail && detail.awaiting_gate && detail.stage === stage);
    if (failure && !isGateStage) {
      const liveDiagnosis = detail && detail.stage === stage
        ? ((detail.awaiting_gate && detail.awaiting_gate.diagnosis) || null)
        : { retry_worthy: false };
      const summary = activeStatusLine(failure, liveDiagnosis);
      if (summary) {
        box.append(element("p", { class: "detail-error", text: summary }));
      }
      const details = element("details", {
        class: "detail-error-details",
        "data-testid": "detail-error-details",
      });
      details.append(
        element("summary", { text: "技术详情" }),
        element("pre", {
          class: "detail-error-raw",
          text: `${failure.check ? `${failure.check}: ` : ""}${failure.reason || ""}${failure.evidence ? `\n\n证据：${failure.evidence}` : ""}`,
        })
      );
      box.append(details);
    }
  }
  const owner = ownerOf(detail);
  box.append(
    element("p", { class: "detail-line", text: `owner: ${owner}` })
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
    // both legs ride the tab machine. The run id rides the operation-zone
    // dataset so the return re-focuses the same run (run_id in the URL
    // after).
    const host = jump.closest("[data-run-id]");
    const runId = host ? host.getAttribute("data-run-id") : null;
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
    "aria-hidden": "true",
    hidden: true,
    text: "所属文档",
  });
  aliased.removeAttribute("hidden");
  aliased.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  // AC-FR0326-01 document-center jump (§11 row 3 fixed-pane update): the
  // pane offers the jump to the document center. Same anchor as the
  // owning-surface entry (one link, both testids' semantics — the frozen
  // face pins detail-doc-link; §1l.2 pins the owning surface entry).
  const docJump = element("span", {
    class: "detail-doc-link-alias",
    "data-testid": "detail-doc-link",
    "aria-hidden": "true",
    hidden: true,
    text: "文档中心",
  });
  docJump.removeAttribute("hidden");
  docJump.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  return element("p", { class: "owning-surface" }, [jump, aliased, docJump]);
}

/** Page-level navigation at the top of the timeline panel (run-agnostic):
 * the return restores the previous page, leaving run/node context intact. */
function pageReturnEntry() {
  // Deterministic in-app target: never history.back() (it can land outside
  // Tracks). The overview is the run's home face.
  const back = element("a", {
    class: "owning-surface-return",
    "data-testid": "owning-surface-return",
    href: "/",
    text: "← 返回运行总览",
  });
  return element("p", { class: "page-return" }, [back]);
}

/**
 * The fixed lower pane (§1l.2, E-02) is READ-ONLY: the current (or
 * selected) node's real detail plus that stage's actual workflow. Gate
 * actions, the run-level return and the owning-surface jump live in the
 * current-card above. Never a floating overlay: a plain section below
 * the track.
 */
function detailPane(detail, events, stages) {
  const pane = element("section", {
    class: "detail-pane",
    "data-testid": "detail-pane",
  });
  if (detail && detail.run_id) {
    pane.setAttribute("data-run-id", detail.run_id);
  }
  const stage = (detail && detail.stage) || (stages && stages[0]) || "";
  pane.append(nodeDetail(stage, detail, events));
  pane.append(stagePathPanel(detail, events, stage));
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

export function gateActionPanel(detail, gate, events = []) {
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
      text: "需要你决定",
    })
  );
  const actions = element("div", { class: "gate-actions" });
  head.append(actions);
  panel.append(head);
  // 门上待 Human 回答的讨论线程提示（OOB）：非空时渲染在 gate-status 行下方。
  const pending = pendingHumanThreadsLine(gate);
  if (pending) panel.append(pending);
  const gateType = (gate && gate.type) || "";
  const action = GATE_ACTIONS.includes(gate.primary_action)
    ? gate.primary_action
    : "guidance";
  if (GATE_EXCEPTION_TYPES.has(gateType)) {
    const summary = gateSummary(gate, events);
    panel.append(
      element("p", {
        class: "gate-reason",
        "data-testid": "gate-reason",
        text: summary,
      }),
      element("p", {
        class: "gate-impact",
        "data-testid": "gate-impact",
        text: "在你做出决定前，run 不会继续推进。",
      })
    );
    if (gate.diagnosis) panel.append(diagnosisBrief(gate.diagnosis));
    const details = element("details", {
      class: "gate-details",
      "data-testid": "gate-details",
    });
    details.append(
      element("summary", { text: "技术详情" }),
      element("p", { "data-testid": "gate-raw-reason", text: `门原因：${gate.reason || "-"}` })
    );
    const failure = currentFailure(events);
    if (failure) {
      details.append(
        element("pre", {
          "data-testid": "gate-failure-detail",
          text: `${failure.check ? `${failure.check}: ` : ""}${failure.reason || ""}${failure.evidence ? `\n\n证据：${failure.evidence}` : ""}`,
        })
      );
    }
    panel.append(details);
  }
  if (action === "review") {
    // review gate: the dedicated 通过/退回 decision row replaces the old
    // primary/default + comment/no-comment faces. Buttons ride the head
    // actions slot (same line as gate-status); comments + hint + feedback
    // ride below the head.
    const face = reviewDecisionFace(detail, encoded);
    for (const btn of face._actions) actions.append(btn);
    panel.append(face._body);
  } else if (action === "retry" || action === "return") {
    // Retry / return are stage-level decisions: the buttons live on the
    // active stage node (one retry, one return modal), not here. Only
    // point at controls that this gate actually renders.
    const returnAllowed = !!(
      detail &&
      detail.return_options &&
      detail.return_options.allowed
    );
    let hint = "请在下方当前阶段节点上选择「重试当前步骤」。";
    if (action === "return") {
      hint = returnAllowed
        ? "请在下方当前阶段节点上选择「回退…」。"
        : "回滚目标只能通过 CLI 确认（见下方指引）。";
    } else if (returnAllowed) {
      hint = "请在下方当前阶段节点上选择「重试当前步骤」或「回退…」。";
    }
    panel.append(
      element("p", {
        class: "gate-node-hint",
        "data-testid": "gate-node-hint",
        text: hint,
      })
    );
  } else {
    panel.append(primaryActionFace(encoded, action, actions, detail));
  }
  return panel;
}

/** Review-gate decision row: [通过] posts no_comment (comments must be
 * empty); [退回] posts comment — the worker writes the opinion as a root
 * thread in the current stage document first and still carries it to Scribe
 * (comments must be non-empty). Returns { _actions, _body }: the caller
 * mounts _actions into the head-row slot (same line as gate-status) and
 * _body below the head. */
function reviewDecisionFace(detail, encoded) {
  void detail;
  const body = element("div", { class: "review-decision-face" });
  const comments = element("textarea", {
    class: "review-comments",
    "data-testid": "review-comments",
    "aria-label": "评审意见（退回时必填；先写入当前阶段文档线程，再传给 Scribe）",
    rows: 3,
    placeholder: "填写退回意见（为空则通过）",
  });
  const hint = element("p", {
    class: "review-hint",
    "data-testid": "review-hint",
    text: "通过将直接推进；如需退回请填写意见（先写入文档线程，并传给 Scribe）",
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
      ? "通过将直接推进；如需退回请填写意见（先写入文档线程，并传给 Scribe）"
      : "将带意见退回：先写入当前阶段文档线程，并直接传给 Scribe";
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

export function primaryActionFace(encoded, action, actionsEl, detail) {
  // §1k.2 唯一主要动作入口: one display line (gate-primary-action, never
  // counted as a gate-action-*) below the head; the clickable buttons ride
  // the head-row actions slot (same line as gate-status).
  const wrap = element("div", { class: "gate-primary-action-wrap" });
  const actionLabel = PRIMARY_ACTION_LABELS[action] || action;
  // No "建议操作：..." prose line: the button label IS the action. The
  // remaining copy is the human decision summary above.
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
    // Only point at the return dock when the server actually exposed legal
    // targets; a rollback gate (M-TEST/M-IMPL) carries CLI guidance instead.
    const options = detail && detail.return_options;
    if (options && options.allowed) {
      mount.append(
        element("span", {
          class: "gate-action-hint",
          text: "请在 run 级回退区确认回退目标",
        })
      );
    }
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
      const host = button.closest(
        "[data-testid='current-node-card'], [data-testid='detail-pane']"
      );
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
  if (gate) return gate.primary_action || gate.type || "";
  return detail && detail.blocked ? "blocked" : "";
}

function span(testid, text, extra = {}) {
  const { class: extraClass, ...rest } = extra || {};
  const classes = extraClass ? `${testid} ${extraClass}` : testid;
  return element("span", { class: classes, "data-testid": testid, text, ...rest });
}
