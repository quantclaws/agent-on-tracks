/**
 * Create wizard modal (Releases window "＋" entry).
 *
 * Three pages: page 1 picks feature vs hotfix; page 2 collects the payload;
 * page 3 (wizard-page-eval) follows a feature submit inside the modal —
 * live Scribe activity while submitted/evaluating, then the human-readable
 * result with recommended/confirm decision faces. The feature face reuses
 * the releases Scribe panel contract (the caller supplies onDone; releases.js
 * renders its pollIntakeReport face). The
 * hotfix face reuses the create_run closed shape (§2b #8). All controls
 * carry data-testid faces; native ES module, no framework.
 */

import { requestJson } from "./api.js";
import { element } from "./dom.js";

const HOTFIX_SCENARIOS = ["post-release", "dev"];

// -- eval page shared contract (OOB wizard third page) ------------------------
// Activity read face (collaborator implements in parallel; catch-tolerant):
//   GET /api/projects/{pid}/intakes/{intake_id}/activity ->
//   {intake_id, sessions:[...], items:[{ts, kind, tool, label, text}]}
// Intake read face (#38):
//   GET /api/projects/{pid}/intakes/{id} -> {status, source, report, ...}
// Decision write face (#40):
//   POST /api/intakes/{id}/decisions {action}
// action closed set: backlog | archive | develop.

const EVAL_POLL_MS = 1500;
const EVAL_MAX_ROUNDS = 360; // 9min budget; then a timeout hint keeps "继续" viable
const PENDING_STATUSES = new Set(["submitted", "evaluating"]);

// Decision button faces: label copy + short name (short drives the
// panel-action-<short> testid kept for the later test phase).
const ACTION_LABELS = {
  develop: "进入开发",
  backlog: "进入 backlog",
  archive: "归档（不开发）",
};
const ACTION_SHORT = {
  backlog: "backlog",
  archive: "archive",
  develop: "develop",
};

/** The #38 report out of an intake payload (report dict preferred). */
function parseReport(intake) {
  if (!intake) return {};
  if (intake.report && typeof intake.report === "object") return intake.report;
  try {
    return JSON.parse(intake.report_json || "{}") || {};
  } catch (error) {
    return {};
  }
}

/**
 * Client-side recommendation (hard mapping + reasons).
 * - feasibility.verdict === "blocked" -> backlog (blocked items linger).
 * - else any duplicates item with suggestion === "no_go" -> archive.
 * - else -> develop.
 * Judgment semantics: enhance = an existing basis exists, advance as an
 * enhancement (NOT already implemented); no_go = duplicate construction
 * is discouraged.
 */
function deriveRecommendation(report) {
  const feasibility = (report && report.feasibility) || {};
  if (feasibility.verdict === "blocked") {
    return { action: "backlog", reason: "存在阻塞项，先记录待议" };
  }
  const duplicates = (report && report.duplicates) || {};
  const items = Array.isArray(duplicates.items) ? duplicates.items : [];
  if (items.some((item) => item && item.suggestion === "no_go")) {
    return { action: "archive", reason: "与既有需求重复，不建议重复建设" };
  }
  return { action: "develop", reason: "评估可行、无重复" };
}

function conflictRefs(report) {
  const conflicts = (report && report.conflicts) || {};
  const pairs = Array.isArray(conflicts.pairs) ? conflicts.pairs : [];
  const refs = [];
  for (const pair of pairs) {
    const ref = pair && (pair.existing_ref || pair.existing || pair.ref);
    if (ref) refs.push(String(ref));
  }
  return refs;
}

function hasConflicts(report) {
  const conflicts = (report && report.conflicts) || {};
  if (conflicts.found) return true;
  return conflictRefs(report).length > 0;
}

async function fetchIntake(pid, intakeId) {
  return requestJson(
    `/api/projects/${encodeURIComponent(pid)}/intakes/${encodeURIComponent(intakeId)}`
  );
}

/** Activity feed read: null when unready/failing (silent downgrade). */
async function fetchActivity(pid, intakeId) {
  try {
    return await requestJson(
      `/api/projects/${encodeURIComponent(pid)}/intakes/${encodeURIComponent(intakeId)}/activity`
    );
  } catch (error) {
    return null;
  }
}

function formatElapsed(ms) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${seconds % 60} 秒`;
}

/** One activity item node (kind drives the face). */
function activityItemNode(item) {
  const kind = (item && item.kind) || "text";
  const label = (item && item.label) || "";
  const text = (item && item.text) || "";
  const node = element("div", {
    class: `activity-item activity-kind-${kind}`,
    "data-testid": "activity-item",
    "data-kind": kind,
  });
  if (kind === "tool") {
    node.append(
      element("span", {
        class: "activity-tool",
        text: `[${(item && item.tool) || "tool"}]${label ? ` ${label}` : ""}`,
      }),
      element("span", { class: "activity-text", text: text ? ` ${text}` : "" })
    );
  } else if (kind === "step") {
    node.append(element("span", { class: "activity-step", text: label || text || "—" }));
  } else {
    if (label) node.append(element("span", { class: "activity-label", text: `${label} ` }));
    node.append(element("span", { class: "activity-text", text }));
  }
  return node;
}

function inputFormLabel(inputForm) {
  if (inputForm === "complete") return "完整需求文档";
  if (inputForm === "raw") return "原始描述";
  return String(inputForm || "—");
}

function duplicateSuggestionLabel(suggestion) {
  // enhance = 有既有基础、按增强推进（不是已实现）；
  // no_go = 不建议重复建设。
  if (suggestion === "enhance") return "作为增强项推进";
  if (suggestion === "no_go") return "不建议重复建设（NO-GO）";
  return String(suggestion || "—");
}

/**
 * Human-readable result view (reused by the create flow and the
 * continue-decision flow). Inline confirms only — never window.confirm.
 * onExecute(action) performs the decision POST; the view awaits it and
 * surfaces a rejection inline.
 */
function buildEvalResult({ intake, report, onExecute }) {
  const safe = report || {};
  const feasibility = safe.feasibility || {};
  const duplicates = safe.duplicates || {};
  const conflicts = safe.conflicts || {};
  const recommendation = deriveRecommendation(safe);
  const refs = conflictRefs(safe);

  const root = element("section", {
    class: "eval-result",
    "data-testid": "eval-result",
  });
  // Contracted alias: the frozen face pins scribe-panel for the same
  // suggestion surface — one hidden alias keeps the later test phase
  // able to locate it without a second visible face.
  const alias = element("span", {
    class: "scribe-panel-alias",
    "data-testid": "scribe-panel",
    hidden: true,
    text: "scribe panel",
  });
  alias.removeAttribute("hidden");
  alias.setAttribute("style", "position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);");
  root.append(alias);

  root.append(
    element("h3", {
      class: "eval-title",
      "data-testid": "eval-result-title",
      text: safe.title || "评估结果",
    })
  );
  if (safe.summary) {
    root.append(
      element("p", {
        class: "eval-summary",
        "data-testid": "eval-result-summary",
        text: safe.summary,
      })
    );
  }
  root.append(
    element("p", {
      class: "eval-meta",
      "data-testid": "eval-result-input-form",
      text: `输入形态：${inputFormLabel(safe.input_form)}`,
    })
  );

  // -- feasibility --
  const feasBox = element("div", { class: "eval-card" });
  feasBox.append(element("h4", { class: "eval-card-title", text: "可行性" }));
  if (feasibility.verdict === "blocked") {
    feasBox.append(element("p", { class: "eval-line", text: "⛔ 暂不可行" }));
    const blockers = Array.isArray(feasibility.blockers) ? feasibility.blockers : [];
    if (!blockers.length) {
      feasBox.append(element("p", { class: "eval-line", text: "存在阻塞（未列明细）" }));
    } else {
      for (const blocker of blockers) {
        feasBox.append(
          element("p", {
            class: "eval-line",
            text: typeof blocker === "string" ? blocker : JSON.stringify(blocker),
          })
        );
      }
    }
  } else {
    feasBox.append(element("p", { class: "eval-line", text: "✅ 可行：未发现阻塞项" }));
  }
  root.append(feasBox);

  // -- duplicates --
  const dupBox = element("div", { class: "eval-card" });
  dupBox.append(element("h4", { class: "eval-card-title", text: "重复" }));
  const dupItems = Array.isArray(duplicates.items) ? duplicates.items : [];
  if (!dupItems.length) {
    dupBox.append(element("p", { class: "eval-line", text: "无重复" }));
  } else {
    for (const item of dupItems) {
      const ref = (item && item.ref) || "—";
      const summary = (item && item.summary) || "";
      dupBox.append(
        element("p", {
          class: "eval-line",
          text: `与 ${ref} 相关：${summary} — 建议：${duplicateSuggestionLabel(item && item.suggestion)}`,
        })
      );
    }
  }
  root.append(dupBox);

  // -- conflicts --
  const confBox = element("div", { class: "eval-card" });
  confBox.append(element("h4", { class: "eval-card-title", text: "冲突" }));
  const pairs = Array.isArray(conflicts.pairs) ? conflicts.pairs : [];
  if (!pairs.length) {
    confBox.append(element("p", { class: "eval-line", text: "无冲突" }));
  } else {
    for (const pair of pairs) {
      const fresh = (pair && pair.new_item) || "—";
      const existing = (pair && (pair.existing_ref || pair.existing || pair.ref)) || "—";
      confBox.append(
        element("p", { class: "eval-line", text: `${fresh} ↔ ${existing}` })
      );
    }
    confBox.append(
      element("p", { class: "eval-note", text: "（进入开发需确认已知晓冲突）" })
    );
  }
  root.append(confBox);

  // Raw JSON stays inspectable but out of the reading path.
  const details = element("details", { class: "eval-raw" });
  details.append(
    element("summary", { text: "原始 JSON" }),
    element("pre", { class: "eval-raw-body", text: JSON.stringify(safe, null, 2) })
  );
  root.append(details);

  const errorLine = element("p", {
    class: "wizard-error",
    "data-testid": "wizard-error",
    role: "alert",
    hidden: true,
  });
  const hintLine = element("p", {
    class: "decision-hint",
    "data-testid": "decision-hint",
    role: "status",
    hidden: true,
  });
  const bar = element("div", {
    class: "panel-actions eval-actions",
    "data-testid": "panel-actions",
  });
  const buttons = [];
  const order = ["develop", "backlog", "archive"];
  for (const action of order) {
    const short = ACTION_SHORT[action];
    const btn = element("button", {
      type: "button",
      class: `panel-action panel-action-${short} decision-btn`,
      "data-testid": `panel-action-${short}`,
      text: ACTION_LABELS[action],
    });
    if (action === recommendation.action) {
      btn.classList.add("recommended");
      btn.setAttribute("data-recommended", "true");
      btn.append(element("span", {
        class: "rec-badge",
        "data-testid": "rec-badge",
        text: "建议",
      }));
    }
    btn.addEventListener("click", () => onPick(action));
    buttons.push({ action, btn });
    bar.append(btn);
  }
  root.append(errorLine, bar, hintLine);

  let executing = false;
  let pendingConfirmAction = null;
  function setExecuting(value) {
    executing = value;
    for (const { btn } of buttons) {
      btn.disabled = value;
    }
  }

  async function runExecute(action) {
    if (executing) return;
    setExecuting(true);
    errorLine.hidden = true;
    try {
      await onExecute(action);
    } catch (error) {
      setExecuting(false);
      errorLine.textContent = `决策失败：${error && error.message ? error.message : error}，可重试。`;
      errorLine.hidden = false;
    }
  }

  function needsConfirm(action) {
    if (action !== recommendation.action) return true;
    if (action === "develop" && hasConflicts(safe)) return true;
    return false;
  }

  function hintTextFor(action) {
    const parts = [];
    if (action !== recommendation.action) {
      parts.push(
        `Scribe 建议「${ACTION_LABELS[recommendation.action]}」：${recommendation.reason}。若你仍要「${ACTION_LABELS[action]}」，请再点击一次该按钮。`
      );
    }
    if (action === "develop" && hasConflicts(safe)) {
      const refText = refs.length ? refs.join("、") : "既有内容";
      parts.push(
        `与 ${refText} 存在冲突/重叠；进入开发需确认已知晓。若仍要进入开发，请再点击一次「进入开发」。`
      );
    }
    return parts.join("");
  }

  function paintPending() {
    for (const { action, btn } of buttons) {
      if (action === pendingConfirmAction) btn.setAttribute("data-confirm-pending", "true");
      else btn.removeAttribute("data-confirm-pending");
    }
  }

  function clearHint() {
    pendingConfirmAction = null;
    hintLine.textContent = "";
    hintLine.hidden = true;
    paintPending();
  }

  function showHint(action) {
    pendingConfirmAction = action;
    hintLine.textContent = hintTextFor(action);
    hintLine.hidden = false;
    paintPending();
  }

  function onPick(action) {
    if (executing) return;
    if (!needsConfirm(action)) {
      clearHint();
      void runExecute(action);
      return;
    }
    if (pendingConfirmAction === action) {
      clearHint();
      void runExecute(action);
      return;
    }
    showHint(action);
  }

  return root;
}

/**
 * Mount the eval flow into a wizard-page-eval host. Shared by the create
 * flow and openDecisionModal: live activity while submitted/evaluating,
 * the human-readable result when evaluated, the retryable failed face,
 * and the decided notice. Returns a stop function for modal teardown.
 */
function mountEvalFlow(page, { pid, intakeId, initial, onDone, closeModal, goBackToSeed }) {
  let stopped = false;
  let pollTimer = null;
  let elapsedTimer = null;
  let rounds = 0;
  let seenItems = 0;
  const startedAt = Date.now();

  page.replaceChildren();
  const statusLine = element("p", {
    class: "wizard-eval-status",
    "data-testid": "wizard-eval-status",
    text: "Scribe 正在评估…",
  });
  const elapsedLine = element("p", {
    class: "wizard-elapsed",
    "data-testid": "wizard-elapsed",
    text: "已用时 0 秒",
  });
  const spinner = element("span", {
    class: "wizard-spinner",
    "data-testid": "wizard-spinner",
    "aria-hidden": "true",
  });
  const headRow = element("div", { class: "wizard-eval-head" }, [spinner, statusLine]);
  const feed = element("div", {
    class: "activity-feed",
    "data-testid": "activity-feed",
    "aria-live": "polite",
  });
  let userScrolledUp = false;
  feed.addEventListener("scroll", () => {
    const gap = feed.scrollHeight - (feed.scrollTop + feed.clientHeight);
    userScrolledUp = gap > 24;
  });
  page.append(
    element("h2", { class: "wizard-title", text: "Scribe 评估" }),
    headRow,
    elapsedLine,
    feed
  );

  function stop() {
    stopped = true;
    if (pollTimer !== null) globalThis.clearInterval(pollTimer);
    if (elapsedTimer !== null) globalThis.clearInterval(elapsedTimer);
    pollTimer = null;
    elapsedTimer = null;
  }

  elapsedTimer = globalThis.setInterval(() => {
    if (stopped) return;
    elapsedLine.textContent = `已用时 ${formatElapsed(Date.now() - startedAt)}`;
  }, 1000);

  function appendActivity(items) {
    const rows = Array.isArray(items) ? items : [];
    if (rows.length < seenItems) {
      feed.replaceChildren();
      seenItems = 0;
    }
    const fresh = rows.slice(seenItems);
    for (const item of fresh) feed.append(activityItemNode(item));
    seenItems = rows.length;
    if (fresh.length && !userScrolledUp) {
      try {
        feed.scrollTop = feed.scrollHeight;
      } catch (error) {
        /* non-fatal */
      }
    }
  }

  function showFailed(message) {
    stop();
    page.replaceChildren();
    page.append(
      element("h2", { class: "wizard-title", text: "评估失败" }),
      element("p", {
        class: "wizard-error",
        "data-testid": "wizard-error",
        role: "alert",
        text: message || "评估失败",
      })
    );
    // Resubmit returns to page 2 with the seed text kept — only the
    // create flow owns a seed page. The continue-decision flow passes
    // goBackToSeed: null and gets a lone close button.
    const barChildren = [];
    if (typeof goBackToSeed === "function") {
      barChildren.push(backButton(() => goBackToSeed()));
    }
    if (barChildren.length) page.append(wizardBar(barChildren));
    // Resubmit keeps the typed seed: relabel the back affordance.
    const back = page.querySelector('[data-testid="wizard-back"]');
    if (back) back.textContent = "重新提交";
  }

  function showDecided(intake) {
    stop();
    page.replaceChildren();
    page.append(
      element("h2", { class: "wizard-title", text: "已决策" }),
      element("p", {
        class: "wizard-decided",
        "data-testid": "wizard-decided",
        text: `该 intake 已决策（${(intake && intake.decision) || (intake && intake.status) || "decided"}），无需重复决策。`,
      })
    );
  }

  function showResult(intake) {
    stop();
    const report = parseReport(intake);
    page.replaceChildren();
    page.append(element("h2", { class: "wizard-title", text: "评估结果" }));
    page.append(buildEvalResult({
      intake,
      report,
      onExecute: async (action) => {
        await requestJson(`/api/intakes/${encodeURIComponent(intakeId)}/decisions`, {
          method: "POST",
          body: JSON.stringify({ action }),
        });
        const done = onDone;
        closeModal();
        if (typeof done === "function") {
          done({ kind: "feature", intakeId, action });
        }
      },
    }));
  }

  async function pollOnce() {
    if (stopped) return;
    rounds += 1;
    if (rounds > EVAL_MAX_ROUNDS) {
      if (pollTimer !== null) globalThis.clearInterval(pollTimer);
      pollTimer = null;
      statusLine.textContent = "评估仍未完成，可关闭后通过「继续」稍后查看";
      return;
    }
    // Activity first (silent downgrade on miss), then the #38 status.
    try {
      const activity = await fetchActivity(pid, intakeId);
      if (stopped) return;
      const items = activity && Array.isArray(activity.items) ? activity.items : null;
      if (items) appendActivity(items);
    } catch (error) {
      /* silent downgrade: status line stays */
    }
    let intake = null;
    try {
      intake = await fetchIntake(pid, intakeId);
    } catch (error) {
      return; // transient read keeps polling
    }
    if (stopped || !intake) return;
    elapsedLine.textContent = `已用时 ${formatElapsed(Date.now() - startedAt)}`;
    const status = intake.status;
    if (status === "evaluated") {
      showResult(intake);
    } else if (status === "failed") {
      const report = parseReport(intake);
      const reason = report && report.evaluation_error
        ? String(report.evaluation_error)
        : "评估失败";
      showFailed(reason);
    } else if (status === "decided") {
      showDecided(intake);
    } else if (!PENDING_STATUSES.has(status) && status !== "evaluated") {
      // Unknown terminal-ish state: keep polling briefly; the budget
      // guard above ends it with a resumable hint.
      statusLine.textContent = "Scribe 正在评估…";
    }
  }

  pollTimer = globalThis.setInterval(() => {
    void pollOnce();
  }, EVAL_POLL_MS);
  // A pre-read terminal intake lands directly (no spinner flash); every
  // other pre-read state still enters the live poll, which re-reads #38
  // and self-corrects any staleness.
  if (initial && initial.status === "evaluated") {
    showResult(initial);
  } else if (initial && initial.status === "failed") {
    const report = parseReport(initial);
    showFailed(report && report.evaluation_error
      ? String(report.evaluation_error)
      : "评估失败");
  } else if (initial && initial.status === "decided") {
    showDecided(initial);
  } else {
    void pollOnce();
  }
  return stop;
}

/** Open the three-page create modal. Returns a close function. */
export function openCreateWizard({ projectId, onDone } = {}) {
  const pid = projectId || "";
  let closed = false;
  let busy = false;
  let evalStop = null;

  const overlay = element("div", {
    class: "wizard-overlay",
    "data-testid": "wizard-modal",
  });
  const dialog = element("div", {
    class: "wizard-dialog",
    role: "dialog",
    "aria-modal": "true",
    "aria-label": "create",
  });
  overlay.append(dialog);

  function close() {
    if (busy || closed) return;
    closed = true;
    if (evalStop) {
      try {
        evalStop();
      } catch (error) {
        /* non-fatal */
      }
      evalStop = null;
    }
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
  }

  // Force close: decision success and eval teardown bypass the submit
  // busy gate (polling never blocks dismissal).
  function forceClose() {
    if (closed) return;
    closed = true;
    if (evalStop) {
      try {
        evalStop();
      } catch (error) {
        /* non-fatal */
      }
      evalStop = null;
    }
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
  }

  function onKey(event) {
    if (event && event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) close();
  });

  // -- page 1: pick the journey -------------------------------------------
  const page1 = element("div", {
    class: "wizard-page",
    "data-testid": "wizard-page-1",
  });
  page1.append(
    element("h2", { class: "wizard-title", text: "新建开发" }),
    element("div", { class: "wizard-options" }, [
      optionButton(
        "wizard-option-feature",
        "发布新功能",
        "从需求到发布的完整开发流程（runtime 驱动，关键节点等待人工确认）。"
      ),
      optionButton(
        "wizard-option-hotfix",
        "Hotfix",
        "针对线上已发布产品或开发中版本的问题快速修复（从 GitHub issue 进入）。"
      ),
    ])
  );

  // -- page 2a: feature seed ----------------------------------------------
  const seedInput = element("textarea", {
    class: "wizard-seed",
    "data-testid": "wizard-seed-input",
    placeholder: "给发布清单页加一个导出按钮，导出为 CSV。",
    rows: 5,
    "aria-label": "需求描述",
  });
  const seedError = element("p", {
    class: "wizard-error",
    "data-testid": "wizard-error",
    role: "alert",
    hidden: true,
  });
  const createRelease = element("button", {
    type: "button",
    class: "wizard-primary",
    "data-testid": "wizard-create-release",
    text: "创建",
  });
  const pageFeature = element("div", {
    class: "wizard-page",
    "data-testid": "wizard-page-feature",
    hidden: true,
  });
  pageFeature.append(
    element("h2", { class: "wizard-title", text: "发布新功能" }),
    seedInput,
    seedError,
    wizardBar([
      backButton(() => show(page1)),
      createRelease,
    ])
  );
  // -- page 3: eval (live activity → human-readable result) -----------------
  const pageEval = element("div", {
    class: "wizard-page wizard-page-eval",
    "data-testid": "wizard-page-eval",
    hidden: true,
  });

  function goBackToSeed() {
    busy = false;
    createRelease.disabled = false;
    show(pageFeature);
  }

  function enterEval(intakeId) {
    if (evalStop) {
      try {
        evalStop();
      } catch (error) {
        /* non-fatal */
      }
      evalStop = null;
    }
    show(pageEval);
    evalStop = mountEvalFlow(pageEval, {
      pid,
      intakeId,
      initial: null,
      onDone,
      closeModal: forceClose,
      goBackToSeed,
    });
  }
  createRelease.addEventListener("click", async () => {
    const content = (seedInput.value || "").trim();
    if (!content || busy) return;
    busy = true;
    createRelease.disabled = true;
    seedError.hidden = true;
    try {
      const receipt = await requestJson(
        `/api/projects/${encodeURIComponent(pid)}/intakes`,
        { method: "POST", body: JSON.stringify({ source: "text", content }) }
      );
      const intakeId = receipt && receipt.intake_id;
      if (!intakeId) {
        throw new Error("intake submitted without receipt");
      }
      // Stay in the modal: the third page carries the live eval.
      busy = false;
      createRelease.disabled = false;
      enterEval(intakeId);
    } catch (error) {
      busy = false;
      createRelease.disabled = false;
      seedError.textContent = `提交失败：${error && error.message ? error.message : error}，已保留输入，可重试。`;
      seedError.hidden = false;
    }
  });

  // -- page 2b: hotfix ------------------------------------------------------
  const issueInput = element("input", {
    class: "wizard-issue",
    "data-testid": "wizard-issue-input",
    type: "text",
    inputmode: "numeric",
    placeholder: "GitHub issue 号，例如 123",
    "aria-label": "GitHub issue 号",
  });
  let scenario = "";
  const scenarioGroup = element("div", { class: "wizard-scenarios" });
  const scenarioButtons = {};
  for (const name of HOTFIX_SCENARIOS) {
    const btn = element("button", {
      type: "button",
      class: "wizard-scenario-option",
      "data-testid": `wizard-scenario-${name === "post-release" ? "post-release" : "dev"}`,
      text: name === "post-release" ? "已发布产品" : "开发中版本",
    });
    btn.addEventListener("click", () => {
      scenario = name;
      paintScenarios();
      schedulePreview();
    });
    scenarioButtons[name] = btn;
    scenarioGroup.append(btn);
  }
  function paintScenarios() {
    for (const name of HOTFIX_SCENARIOS) {
      const btn = scenarioButtons[name];
      if (scenario === name) btn.setAttribute("aria-pressed", "true");
      else btn.removeAttribute("aria-pressed");
    }
  }
  paintScenarios();
  const validation = element("div", {
    class: "wizard-validation",
    "data-testid": "wizard-issue-validation",
    text: "输入 issue 号并选择场景后自动校验。",
  });
  const hotfixError = element("p", {
    class: "wizard-error",
    "data-testid": "wizard-error",
    role: "alert",
    hidden: true,
  });
  const createHotfix = element("button", {
    type: "button",
    class: "wizard-primary",
    "data-testid": "wizard-create-hotfix",
    text: "创建 Hotfix",
  });
  createHotfix.disabled = true;
  const pageHotfix = element("div", {
    class: "wizard-page",
    "data-testid": "wizard-page-hotfix",
    hidden: true,
  });
  pageHotfix.append(
    element("h2", { class: "wizard-title", text: "Hotfix" }),
    issueInput,
    scenarioGroup,
    validation,
    hotfixError,
    wizardBar([
      backButton(() => show(page1)),
      createHotfix,
    ])
  );

  let previewTimer = null;
  let lastPreview = null; // { issue, scenario, payload }
  let previewSeq = 0;

  function currentIssue() {
    return (issueInput.value || "").trim();
  }
  function schedulePreview() {
    if (previewTimer !== null) globalThis.clearTimeout(previewTimer);
    previewTimer = globalThis.setTimeout(() => {
      previewTimer = null;
      void runPreview();
    }, 350);
  }
  async function runPreview() {
    const number = currentIssue();
    hotfixError.hidden = true;
    if (!number || !HOTFIX_SCENARIOS.includes(scenario)) {
      lastPreview = null;
      validation.textContent = "输入 issue 号并选择场景后自动校验。";
      updateHotfixEnabled();
      return;
    }
    const seq = ++previewSeq;
    validation.textContent = "校验中…";
    updateHotfixEnabled();
    try {
      const payload = await requestJson(
        `/api/projects/${encodeURIComponent(pid)}/hotfix-preview` +
          `?issue=${encodeURIComponent(number)}&scenario=${encodeURIComponent(scenario)}`
      );
      if (seq !== previewSeq) return; // stale response
      lastPreview = { issue: number, scenario, payload };
      renderHotfixValidation(validation, payload);
    } catch (error) {
      if (seq !== previewSeq) return;
      lastPreview = null;
      validation.textContent =
        `校验失败：${error && error.message ? error.message : error}`;
    }
    updateHotfixEnabled();
  }
  function updateHotfixEnabled() {
    const number = currentIssue();
    const anchored =
      lastPreview &&
      lastPreview.issue === number &&
      lastPreview.scenario === scenario &&
      lastPreview.payload &&
      lastPreview.payload.anchoring &&
      lastPreview.payload.anchoring.status === "anchored" &&
      lastPreview.payload.precheck &&
      lastPreview.payload.precheck.status !== "rejected";
    createHotfix.disabled = !anchored;
    if (anchored) createHotfix.removeAttribute("aria-disabled");
    else createHotfix.setAttribute("aria-disabled", "true");
  }
  issueInput.addEventListener("input", () => {
    updateHotfixEnabled();
    schedulePreview();
  });
  createHotfix.addEventListener("click", async () => {
    const number = currentIssue();
    const preview = lastPreview;
    const anchored =
      preview &&
      preview.issue === number &&
      preview.scenario === scenario &&
      preview.payload &&
      preview.payload.anchoring &&
      preview.payload.anchoring.status === "anchored";
    if (!anchored || busy) return;
    busy = true;
    createHotfix.disabled = true;
    hotfixError.hidden = true;
    const payload = preview.payload;
    const anchoring = payload.anchoring || {};
    const issue = payload.issue || {};
    const issueNumber = Number(issue.number ?? number);
    try {
      const receipt = await requestJson(
        `/api/projects/${encodeURIComponent(pid)}/runs`,
        {
          method: "POST",
          body: JSON.stringify({
            journey: preview.scenario === "dev" ? "hotfix_dev" : "hotfix_post",
            version: anchoring.target_version || "v0.9",
            story: null,
            issue: Number.isFinite(issueNumber) ? issueNumber : number,
            target: null,
            preempt: false,
          }),
        }
      );
      const runId = receipt && receipt.run_id;
      busy = false;
      closed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
      if (typeof onDone === "function") {
        onDone({ kind: "hotfix", runId });
      }
    } catch (error) {
      busy = false;
      updateHotfixEnabled();
      hotfixError.textContent = `创建失败：${error && error.message ? error.message : error}，可重试。`;
      hotfixError.hidden = false;
    }
  });

  // -- wire page 1 ------------------------------------------------------------
  page1
    .querySelector('[data-testid="wizard-option-feature"]')
    .addEventListener("click", () => show(pageFeature));
  page1
    .querySelector('[data-testid="wizard-option-hotfix"]')
    .addEventListener("click", () => show(pageHotfix));

  function show(page) {
    // Single-page mount: only the active page lives in the dialog (plus the
    // persistent dialog-level ×), so no testid (wizard-error / wizard-back)
    // ever duplicates.
    dialog.replaceChildren(dialogClose, page);
    for (const candidate of [page1, pageFeature, pageHotfix, pageEval]) {
      if (candidate === page) candidate.removeAttribute("hidden");
      else candidate.setAttribute("hidden", "");
    }
  }

  const dialogClose = closeButton(close, () => busy);
  dialog.append(dialogClose, page1);
  document.body.append(overlay);
  return close;
}

/**
 * Open the decision modal for any intake (the releases "继续" entry).
 * submitted/evaluating -> live third-page view; evaluated -> the
 * human-readable result; decided/others -> a decided notice. Decision
 * success closes the modal and calls onDone.
 */
export function openDecisionModal({ projectId, intakeId, onDone } = {}) {
  const pid = projectId || "";
  const id = intakeId || "";
  let closed = false;
  let evalStop = null;

  const overlay = element("div", {
    class: "wizard-overlay",
    "data-testid": "wizard-modal",
  });
  const dialog = element("div", {
    class: "wizard-dialog",
    role: "dialog",
    "aria-modal": "true",
    "aria-label": "intake decision",
  });
  overlay.append(dialog);
  const dialogClose = closeButton(() => forceClose(), () => false);
  const page = element("div", {
    class: "wizard-page wizard-page-eval",
    "data-testid": "wizard-page-eval",
  });
  dialog.append(dialogClose, page);
  document.body.append(overlay);

  function forceClose() {
    if (closed) return;
    closed = true;
    if (evalStop) {
      try {
        evalStop();
      } catch (error) {
        /* non-fatal */
      }
      evalStop = null;
    }
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
  }
  function onKey(event) {
    if (event && event.key === "Escape") {
      event.preventDefault();
      forceClose();
    }
  }
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) forceClose();
  });

  // Pre-read the status so an evaluated intake lands on the result
  // directly (mountEvalFlow's first poll would do the same; this only
  // skips the transient spinner for the common continue case). Any read
  // failure falls back to the live view, which degrades readably.
  fetchIntake(pid, id)
    .then((intake) => {
      if (closed) return;
      if (intake && intake.status === "decided") {
        page.replaceChildren();
        page.append(
          element("h2", { class: "wizard-title", text: "已决策" }),
          element("p", {
            class: "wizard-decided",
            "data-testid": "wizard-decided",
            text: `该 intake 已决策（${intake.decision || "decided"}），无需重复决策。`,
          })
        );
        return;
      }
      evalStop = mountEvalFlow(page, {
        pid,
        intakeId: id,
        initial: intake,
        onDone,
        closeModal: forceClose,
        goBackToSeed: null,
      });
    })
    .catch(() => {
      if (closed) return;
      evalStop = mountEvalFlow(page, {
        pid,
        intakeId: id,
        initial: null,
        onDone,
        closeModal: forceClose,
        goBackToSeed: null,
      });
    });
  return forceClose;
}

function renderHotfixValidation(host, payload) {
  const precheck = (payload && payload.precheck) || {};
  const anchoring = (payload && payload.anchoring) || {};
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
    host.append(
      element("p", {
        class: "hotfix-exit-guidance",
        "data-testid": "hotfix-exit-guidance",
        text: "hotfix exit: route this input through the backlog entry or start a new feature",
      })
    );
  }
}

function optionButton(testid, title, description) {
  return element("button", { type: "button", class: "wizard-option", "data-testid": testid }, [
    element("span", { class: "wizard-option-title", text: title }),
    element("span", { class: "wizard-option-desc", text: description }),
  ]);
}

function wizardBar(children) {
  return element("div", { class: "wizard-bar" }, children);
}

function closeButton(close, isBusy) {
  const btn = element("button", {
    type: "button",
    class: "wizard-close",
    "data-testid": "wizard-close",
    text: "×",
    "aria-label": "关闭",
  });
  btn.addEventListener("click", () => {
    if (isBusy && isBusy()) return;
    close();
  });
  return btn;
}

function backButton(onBack) {
  const btn = element("button", {
    type: "button",
    class: "wizard-back",
    "data-testid": "wizard-back",
    text: "返回",
  });
  btn.addEventListener("click", onBack);
  return btn;
}
