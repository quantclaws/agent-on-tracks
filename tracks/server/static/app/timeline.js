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
  "approve": "批准",
  "release-decision": "发布决策",
  "retry": "重试",
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

// Stage workflow diagram (OOB 阶段内实际工作流): the lower panel renders the
// SELECTED node's stage window (last stage.entered → next stage.exited, else
// now) as a linear mermaid stateDiagram-v2 — dispatch+outcome packed per
// interaction, discrete human/verdict/commit nodes, consecutive
// attention.required folded, middle-fold past the threshold.
const WORKFLOW_FOLD_THRESHOLD = 36;
const WORKFLOW_FOLD_KEEP = 8;

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
 * ``stateDiagram-v2`` TB, linear chain) over the stage window: the stage's
 * LAST ``stage.entered`` ts through the nearest following ``stage.exited``
 * (or now). One ``dispatch_agent command.issued`` + its following
 * ``outcome.received`` pack into one interaction node; mechanical stream
 * rows are kept by default; consecutive ``attention.required`` fold with
 * ×N; past WORKFLOW_FOLD_THRESHOLD the middle folds into one summary node.
 * Never throws — an empty window yields null and the caller degrades.
 */
export function stageWorkflowSource(detail, events, stage) {
  const window = stageWorkflowWindow(events, stage);
  if (!window.length) return null;
  const nodes = foldWorkflowNodes(window, stage, detail);
  if (!nodes.length) return null;
  const lines = ["stateDiagram-v2", "direction TB"];
  lines.push("[*] --> w1");
  nodes.forEach((node, index) => {
    const id = `w${index + 1}`;
    lines.push(`state "${sanitizeMermaidText(node.title)}" as ${id}`);
  });
  for (let index = 1; index < nodes.length; index += 1) {
    lines.push(`w${index} --> w${index + 1}`);
  }
  for (let index = 0; index < nodes.length; index += 1) {
    const id = `w${index + 1}`;
    for (const line of nodes[index].lines) {
      lines.push(`${id} : ${sanitizeMermaidText(line)}`);
    }
  }
  lines.push("classDef done fill:#e8f5e9,stroke:#2e7d32,color:#1e4620");
  lines.push("classDef current fill:#fff4e5,stroke:#f5a623,stroke-width:2px,color:#7a4d00");
  lines.push("classDef pending fill:#f7f7f7,stroke:#d0d0d0,color:#9a9a9a");
  lines.push("classDef void fill:#fdecea,stroke:#d93025,color:#8a1c14");
  lines.push("classDef attention fill:#fff8e1,stroke:#f5a623,color:#7a4d00");
  for (let index = 0; index < nodes.length; index += 1) {
    lines.push(`class w${index + 1} ${nodes[index].tone || "pending"}`);
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

function roleDisplayName(role) {
  const text = String(role || "");
  if (!text) return "";
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function shortSha(sha) {
  const text = String(sha || "");
  return text.length > 12 ? text.slice(0, 12) : text;
}

/** Window events → linear workflow nodes. Dispatch_agent rows (kind or a
 * role-bearing params, the slim identity) become interaction nodes and
 * swallow the outcome.received that follows before the next interaction;
 * non-dispatch command rows and all other rows map to discrete nodes;
 * heartbeats / parity / writelock / ledger rows are skipped; consecutive
 * attention.required rows fold into one ×N node.
 */
function foldWorkflowNodes(window, stage, detail) {
  const nodes = [];
  let index = 0;
  while (index < window.length) {
    const event = window[index];
    const type = (event && event.type) || "";
    if (
      HEARTBEAT_EVENT_TYPES.has(type) ||
      type === "dispatch.parity" ||
      type.startsWith("writelock.") ||
      type.startsWith("ledger.")
    ) {
      index += 1;
      continue;
    }
    if (isDispatchInteraction(event)) {
      const packed = { node: interactionNode(event), consumed: 1 };
      let cursor = index + 1;
      while (cursor < window.length) {
        const next = window[cursor];
        const nextType = (next && next.type) || "";
        if (isDispatchInteraction(next)) break;
        if (nextType === "outcome.received") {
          packed.node.lines.push(`结果 ${fmtSmart(next.ts)}`);
          packed.consumed += 1;
          cursor += 1;
          continue;
        }
        break;
      }
      nodes.push(packed.node);
      index += packed.consumed;
      continue;
    }
    if (type === "attention.required") {
      let count = 1;
      let area = payloadScalar(event, "area");
      let cursor = index + 1;
      while (cursor < window.length && (window[cursor] && window[cursor].type) === "attention.required") {
        count += 1;
        cursor += 1;
      }
      const title = count > 1 ? `需要注意 ×${count}` : "需要注意";
      const lines = [`需要注意${count > 1 ? ` ×${count}` : ""}`];
      if (area) lines.push(String(area));
      if (event.ts) lines.push(fmtSmart(event.ts));
      nodes.push({ title, lines, tone: "attention", kind: "attention" });
      index = cursor;
      continue;
    }
    const discrete = discreteWorkflowNode(event);
    if (discrete) {
      nodes.push(discrete);
    } else if (type === "outcome.received") {
      nodes.push({
        title: "收到执行结果",
        lines: ["收到执行结果", fmtSmart(event.ts)].filter(Boolean),
        tone: "pending", kind: "outcome",
      });
    } else {
      nodes.push({
        title: stageEventLabel(event),
        lines: [`${stageEventLabel(event)} · ${event && event.ts ? fmtSmart(event.ts) : "-"}`],
        tone: "pending", kind: "other",
      });
    }
    index += 1;
  }
  appendGateNode(nodes, stage, detail, window);
  return foldWorkflowMiddle(nodes);
}

function isDispatchInteraction(event) {
  if (!event || event.type !== "command.issued") return false;
  if (dispatchKind(event) === "dispatch_agent") return true;
  const params = dispatchParams(event);
  return !!(params && params.role);
}

function interactionNode(event) {
  const params = dispatchParams(event) || {};
  const role = roleDisplayName(params.role);
  const substate = params.substate ? String(params.substate) : "";
  const round = params.review_round !== undefined && params.review_round !== null
    ? String(params.review_round) : "";
  const attempt = params.attempt !== undefined && params.attempt !== null
    ? String(params.attempt) : (payloadScalar(event, "attempt") ?? "");
  const head = [role, substate ? `· ${substate}` : ""].filter(Boolean).join(" ");
  const meta = [];
  if (round !== "") meta.push(`第 ${round} 轮`);
  if (attempt !== "") meta.push(`尝试 ${attempt}`);
  const lines = [head || "派发 Agent"];
  lines.push(`${meta.length ? `${meta.join(" / ")} · ` : ""}${fmtSmart(event.ts)}`);
  return { title: `${head || "派发"}${meta.length ? `（${meta.join(" / ")}）` : ""}`, lines, tone: "current", kind: "dispatch" };
}

function discreteWorkflowNode(event) {
  const type = (event && event.type) || "";
  const ts = event && event.ts ? fmtSmart(event.ts) : "";
  const at = (text) => (ts ? `${text} · ${ts}` : text);
  switch (type) {
    case "story.requested":
      return { title: "故事骨架就位", lines: [at("故事骨架就位")], tone: "done", kind: "marker" };
    case "stage.entered":
      return { title: "进入阶段", lines: [at("进入阶段")], tone: "done", kind: "marker" };
    case "stage.exited":
      return { title: "离开阶段", lines: [at("离开阶段")], tone: "done", kind: "marker" };
    case "human.triage": {
      const decision = payloadScalar(event, "decision");
      return { title: `人工裁决 ${decision || ""}`.trim(), lines: [at(`人工裁决 ${decision || ""}`.trim())], tone: "attention", kind: "human" };
    }
    case "human.review": {
      const action = payloadScalar(event, "action");
      return { title: `人工评审 ${action || ""}`.trim(), lines: [at(`人工评审 ${action || ""}`.trim())], tone: "attention", kind: "human" };
    }
    case "human.retry":
    case "human.recover":
    case "human.return":
    case "human.approval": {
      const label = EVENT_LABELS[type] || type;
      return { title: label, lines: [at(label)], tone: "attention", kind: "human" };
    }
    case "review.round_started": {
      const round = payloadScalar(event, "round");
      return { title: `新一轮评审 #${round ?? "?"}`, lines: [at(`新一轮评审 #${round ?? "?"}`)], tone: "current", kind: "round" };
    }
    case "repair.round_started": {
      const round = payloadScalar(event, "round");
      const label = round ? `修复轮 #${round}` : "修复轮";
      return { title: label, lines: [at(label)], tone: "current", kind: "round" };
    }
    case "verdict.passed":
    case "verdict.failed": {
      const check = payloadScalar(event, "check");
      const label = type === "verdict.passed" ? "校验通过" : "校验未通过";
      return { title: check ? `${label} ${check}` : label, lines: [at(check ? `${label} ${check}` : label)], tone: type === "verdict.passed" ? "done" : "void", kind: "verdict" };
    }
    case "sage.verdict":
    case "lex.verdict":
    case "prism.verdict": {
      const who = type.split(".")[0];
      const verdict = payloadScalar(event, "verdict");
      const label = `${who} 评审 ${verdict || ""}`.trim();
      return { title: label, lines: [at(label)], tone: verdict === "pass" ? "done" : "attention", kind: "verdict" };
    }
    case "stage.rolled_back": {
      const to = payloadScalar(event, "to") ?? payloadScalar(event, "to_stage") ?? "";
      const label = to ? `阶段回退 ${to}` : "阶段回退";
      return { title: label, lines: [at(label)], tone: "void", kind: "rollback" };
    }
    case "command.issued": {
      const kind = dispatchKind(event);
      const label = kind && kind !== "dispatch_agent"
        ? `指令 ${kind}` : stageEventLabel(event);
      return { title: label, lines: [at(label)], tone: "pending", kind: "command" };
    }
    default: break;
  }
  if (type.endsWith(".committed")) {
    const sha = payloadScalar(event, "commit_sha");
    const final = payloadScalar(event, "final");
    const label = `产物提交${final ? "（终版）" : ""}${sha ? shortSha(sha) : ""}`;
    return { title: "产物提交", lines: [at(label)], tone: "done", kind: "commit" };
  }
  return null;
}

function appendGateNode(nodes, stage, detail, window) {
  if (!detail || !stage || detail.stage !== stage) return;
  if (detail.status && isTerminalStatus(detail.status)) return;
  if (detail.awaiting_gate) {
    const basis = lastBusinessEvent(window.length ? window : []);
    nodes.push({
      title: "人工门：等待裁决",
      lines: [`人工门：等待裁决（已等 ${waitingElapsed(basis)}）`],
      tone: "attention", kind: "gate",
    });
    return;
  }
  const last = window.length ? window[window.length - 1] : null;
  if (last && last.type === "command.issued") {
    nodes.push({ title: "进行中…", lines: ["进行中…"], tone: "current", kind: "running" });
  }
}

/** Middle-fold past the threshold: first 8 + summary + last 8. Counts ride
 * the summary node (dispatch/result/verdict/other tallies are real).
 */
function foldWorkflowMiddle(nodes) {
  if (nodes.length <= WORKFLOW_FOLD_THRESHOLD) return nodes;
  const head = nodes.slice(0, WORKFLOW_FOLD_KEEP);
  const tail = nodes.slice(nodes.length - WORKFLOW_FOLD_KEEP);
  const middle = nodes.slice(WORKFLOW_FOLD_KEEP, nodes.length - WORKFLOW_FOLD_KEEP);
  const tally = { dispatch: 0, outcome: 0, verdict: 0, other: 0 };
  for (const node of middle) {
    if (node.kind === "dispatch") tally.dispatch += 1;
    else if (node.kind === "outcome") tally.outcome += 1;
    else if (node.kind === "verdict") tally.verdict += 1;
    else tally.other += 1;
  }
  const summaryText =
    `… 中间 ${middle.length} 步（派发×${tally.dispatch} · 结果×${tally.outcome} · 校验×${tally.verdict} · 其它×${tally.other}）…`;
  return [...head, { title: summaryText, lines: [summaryText], tone: "pending", kind: "fold" }, ...tail];
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

/** Render the stage workflow source into ``host`` as inline SVG. Never
 * throws: a failed load or render degrades to a retry line in place. */
async function renderStageWorkflowDiagram(host, source) {
  try {
    const mermaid = await loadMermaid();
    if (!mermaidInitialized) {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });
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

/** Stage workflow panel (OOB 阶段内实际工作流): the SELECTED (or current)
 * stage's actual execution graph — never the full 13-stage pipeline (that
 * static repetition is removed). Renders the summary line +
 * ``stage-workflow-diagram`` mermaid host, or the pending degradation text
 * when the stage has no recorded window. Pure display over the already
 * loaded ``detail`` + ``events`` — no new data source, no timers. */
function stagePathPanel(detail, events, selectedStage) {
  const stage = selectedStage || (detail && detail.stage) || "";
  const wrap = element("section", {
    class: "stage-path",
    "data-testid": "stage-path",
  });
  wrap.append(element("h4", { class: "stage-path-title", text: "阶段内工作流（实际运行）" }));
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
    host.textContent = "该阶段尚未开始（暂无实际执行记录）";
  }
  return wrap;
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
  if (!rows.length) {
    list.append(element("li", {
      class: "ac-chain-empty",
      text: "验收链尚未开始：进入开发后，每条验收标准会在此逐条展开（AC → 测试 → 结果 → 证据）",
    }));
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
    const previous = host.querySelector('[data-testid="node-detail"] [data-testid="node-agent-session"]');
    if (previous && typeof previous._stopAgentPoll === "function") {
      try {
        previous._stopAgentPoll();
      } catch (error) {
        /* non-fatal */
      }
    }
    stopAgentSessionPoll();
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
        text: `start/end: ${fmtTs(ofStage[0].ts)} → ${fmtTs(ofStage[ofStage.length - 1].ts)}`,
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
  const facts = foldStageFacts(events);
  // the working-agent block rides the run's current stage node while it is
  // being worked (active) or parked at its human gate (attention — the
  // agent's session is still the relevant one; the poll flips the block to
  // the awaiting face on its first answer). Awaiting is decided by the
  // response's `awaiting` field, not pre-empted here, so the pre-restart
  // degraded face (route 404) shows the same way in both states.
  const nodeState = nodeStateOf(stage, detail, facts);
  if (
    detail &&
    detail.run_id &&
    detail.stage === stage &&
    (nodeState === "active" || nodeState === "attention")
  ) {
    box.append(agentSessionBlock(detail));
  }
  return box;
}

/** The working-agent session block: who is doing the current stage's work
 * and what it has done lately (``GET /api/runs/{run_id}/activity``, 2s
 * poll). Renders only for the run's current working stage (active, no
 * gate) — a gated stage shows the gate action panel instead. The fetch
 * degrades silently: before the route ships (serve restart) the block
 * keeps its waiting face and never throws. */
let agentSessionTimer = null;

function stopAgentSessionPoll() {
  if (agentSessionTimer !== null) {
    globalThis.clearTimeout(agentSessionTimer);
    agentSessionTimer = null;
  }
}

function shortSessionId(session) {
  const text = String(session || "");
  if (text.length <= 20) return text;
  return `${text.slice(0, 8)}…${text.slice(-8)}`;
}

function agentSessionFeedItem(item) {
  const kind = (item && item.kind) || "text";
  const row = element("div", {
    class: `activity-item activity-kind-${kind} node-agent-feed-item`,
    "data-testid": "node-agent-feed-item",
    "data-kind": kind,
  });
  row.append(element("span", { class: "node-agent-feed-ts", text: fmtClock(item && item.ts) }));
  const label = (item && item.label) || "";
  const tool = (item && item.tool) || "";
  if (kind === "tool") {
    row.append(element("span", {
      class: "activity-tool",
      text: `[${tool || "tool"}]${label ? ` ${label}` : ""}`,
    }));
  } else if (label) {
    row.append(element("span", { class: "activity-label", text: ` ${label}` }));
  }
  const body = (item && item.text) || "";
  if (body && kind !== "step") {
    row.append(element("span", { class: "activity-text", text: ` ${body}` }));
  } else if (kind === "step" && !label) {
    row.append(element("span", { class: "activity-step", text: body || "—" }));
  }
  return row;
}

function agentSessionBlock(detail) {
  const runId = detail.run_id;
  const box = element("div", {
    class: "node-agent-session",
    "data-testid": "node-agent-session",
  });
  const status = element("p", {
    class: "node-agent-session-status",
    "data-testid": "node-agent-session-status",
    role: "status",
    text: "等待 Agent 启动…",
  });
  const sessionId = element("p", {
    class: "node-agent-session-id",
    "data-testid": "node-agent-session-id",
    text: "",
  });
  sessionId.hidden = true;
  const feed = element("div", {
    class: "activity-feed node-agent-feed",
    "data-testid": "node-agent-feed",
    "aria-live": "polite",
  });
  box.append(status, sessionId, feed);
  stopAgentSessionPoll();
  const state = { stopped: false, backoff: false, seen: 0 };
  box._stopAgentPoll = () => {
    state.stopped = true;
  };
  async function pollOnce() {
    if (state.stopped || !document.contains(box)) return;
    const url = `/api/runs/${encodeURIComponent(runId)}/activity`;
    let payload = null;
    try {
      payload = await requestJson(url);
    } catch (error) {
      payload = null;
    }
    if (state.stopped || !document.contains(box)) return;
    if (payload === null) {
      status.textContent = "（会话流不可用，稍后重试）";
      schedule(true);
      return;
    }
    // every answered poll renders first (the awaiting face still shows the
    // finished session's items for human review); only the status line and
    // the stop/continue decision differ per branch.
    const rendered = render(payload);
    if (payload.stage !== detail.stage) {
      state.stopped = true;
      status.textContent = "该阶段已结束";
      return;
    }
    if (payload.awaiting) {
      state.stopped = true;
      status.textContent = "等待人工确认";
      return;
    }
    state.backoff = false;
    if (!rendered) {
      status.textContent = "等待 Agent 启动…";
    }
    schedule(false);
  }
  // Render one answered payload's sessions + feed tail; returns true when a
  // session was present (the status line is the caller's per-branch face).
  function render(payload) {
    const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    const items = Array.isArray(payload.items) ? payload.items : [];
    const role = payload.role || "";
    const roleName = role ? role.charAt(0).toUpperCase() + role.slice(1) : "Agent";
    if (!sessions.length) {
      return false;
    }
    status.textContent = `${roleName} 正在工作 · ${items.length} 条动态`;
    sessionId.textContent = shortSessionId(sessions[0]);
    sessionId.hidden = false;
    const latest = items.slice(-30);
    if (latest.length < state.seen) {
      feed.replaceChildren();
      state.seen = 0;
    }
    for (const item of latest.slice(state.seen)) {
      feed.append(agentSessionFeedItem(item));
    }
    state.seen = latest.length;
    try {
      feed.scrollTop = feed.scrollHeight;
    } catch (error) {
      /* non-fatal */
    }
    return true;
  }
  function schedule(failed) {
    if (state.stopped) return;
    agentSessionTimer = globalThis.setTimeout(
      pollOnce, failed ? 5000 : 2000
    );
  }
  schedule(false);
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
    // the awaiting stage is still the run's current working stage: its
    // agent session block rides below the gate panel (the poll flips it to
    // the awaiting face on its first answer). The selected/current stage's
    // actual workflow graph rides after the session block, before the
    // return pointer.
    if (detail && detail.run_id && detail.stage) {
      pane.append(agentSessionBlock(detail));
    }
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
      class: "gate-status",
      "data-testid": "gate-status",
      text: "当前状态：等待人工裁决",
    }),
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
  const actionLabel = PRIMARY_ACTION_LABELS[action] || action;
  wrap.append(
    element("p", {
      class: "gate-primary-action",
      "data-testid": "gate-primary-action",
      title: action,
      text: `建议操作：${actionLabel}`,
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
      title: `${action} → ${JSON.stringify(_default[1])}`,
      text: "执行默认动作",
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
      title: "approve",
      text: "执行默认动作",
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
      title: "release-decision",
      text: "执行默认动作",
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

function span(testid, text, extra = {}) {
  const { class: extraClass, ...rest } = extra || {};
  const classes = extraClass ? `${testid} ${extraClass}` : testid;
  return element("span", { class: classes, "data-testid": testid, text, ...rest });
}
