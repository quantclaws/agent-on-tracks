/**
 * Unified intake suggestion panel (IF-INTAKE-001 §1e.2/§1f; interfaces §2b
 * #38-40).
 *
 * One composer per project on the releases window: a raw text input goes
 * through the #39 submit endpoint, the panel polls the #38 read face while
 * the Scribe evaluation runs, renders the report's fixed columns (the
 * duplicate/conflict detection §1o carries explicit 「无」 rows when empty)
 * and exposes the four decisions of the #40 face — backlog / archive /
 * remove_backlog (backlog-sourced only) / develop. A failed submit keeps
 * the typed text in place (retryable, never silently dropped).
 */

import { postJson, requestJson } from "./api.js";
import { element } from "./dom.js";

export const INTAKE_POLL_MS = 2000;
export const INTAKE_POLL_ROUNDS = 90;

const DECISIONS = [
  { action: "backlog", label: "记入 backlog" },
  { action: "archive", label: "归档" },
  { action: "develop", label: "立项开发" },
];

export function createIntakePanel({ projectId }) {
  const input = element("textarea", {
    class: "intake-content",
    "data-testid": "intake-content",
    "aria-label": "统一入口输入",
    rows: 3,
    placeholder: "粘贴一段新需求描述…",
  });
  const submitButton = element("button", {
    type: "button",
    class: "intake-submit",
    "data-testid": "intake-submit",
    text: "提交评估",
  });
  const errorLine = element("p", {
    class: "intake-error",
    "data-testid": "intake-error",
    role: "alert",
    hidden: "",
  });
  const statusLine = element("p", {
    class: "intake-status",
    "data-testid": "intake-status",
    text: "等待输入",
  });
  const reportBox = element("div", {
    class: "intake-report",
    "data-testid": "intake-report",
    hidden: "",
  });
  const decisionBar = element("div", {
    class: "intake-decisions",
    "data-testid": "intake-decisions",
    hidden: "",
  });
  const root = element(
    "section",
    { class: "intake-panel", "data-testid": "intake-panel", "aria-label": "统一入口建议面板" },
    [input, submitButton, errorLine, statusLine, reportBox, decisionBar]
  );

  let intakeId = null;
  let pollTimer = null;
  let pollRounds = 0;

  submitButton.addEventListener("click", () => submit());
  root.addEventListener("DOMNodeRemoved", stopPolling);

  async function submit() {
    const content = input.value.trim();
    errorLine.hidden = true;
    if (!content) return;
    submitButton.disabled = true;
    try {
      const receipt = await postJson(
        `/api/projects/${encodeURIComponent(projectId)}/intakes`,
        { source: "text", content }
      );
      intakeId = receipt && receipt.intake_id ? receipt.intake_id : null;
      statusLine.textContent = intakeId
        ? `已受理 ${intakeId}——评估中`
        : "已受理（无 intake_id 回执）";
      if (intakeId) startPolling();
    } catch (error) {
      errorLine.textContent = error && error.message ? error.message : String(error);
      errorLine.hidden = false;
    } finally {
      submitButton.disabled = false;
    }
  }

  function startPolling() {
    stopPolling();
    pollRounds = 0;
    pollTimer = setInterval(pollOnce, INTAKE_POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  async function pollOnce() {
    pollRounds += 1;
    if (pollRounds > INTAKE_POLL_ROUNDS) {
      stopPolling();
      statusLine.textContent = "评估超时——稍后重开面板查看";
      return;
    }
    let intake = null;
    try {
      intake = await requestJson(
        `/api/projects/${encodeURIComponent(projectId)}/intakes/${encodeURIComponent(intakeId)}`
      );
    } catch (error) {
      return; // a transient read failure keeps the poll running
    }
    if (!intake) return;
    if (intake.status === "evaluating") return;
    stopPolling();
    renderReport(intake);
  }

  function renderReport(intake) {
    let report = {};
    try {
      report = JSON.parse(intake.report_json || "{}") || {};
    } catch (error) {
      report = {};
    }
    statusLine.textContent = `评估完成（${intake.status}）`;
    reportBox.replaceChildren(
      element("p", {
        class: "intake-report-title",
        "data-testid": "intake-report-title",
        text: `建议：${report.suggestion || "—"}` || "建议：—",
      }),
      column("重复检测", report.duplicate),
      column("冲突检测", report.conflict)
    );
    reportBox.hidden = false;
    if (intake.status !== "evaluated") return;
    decisionBar.replaceChildren();
    for (const decision of DECISIONS) {
      decisionBar.append(decisionButton(decision));
    }
    if (String(intake.source || "") === "backlog") {
      decisionBar.append(decisionButton({ action: "remove_backlog", label: "移除 backlog 项" }));
    }
    decisionBar.hidden = false;
  }

  function column(label, value) {
    const text = value && String(value).trim() ? String(value).trim() : "无";
    return element("p", {
      class: "intake-report-column",
      "data-testid": `intake-report-${label === "重复检测" ? "duplicate" : "conflict"}`,
      text: `${label}：${text}`,
    });
  }

  function decisionButton({ action, label }) {
    const button = element("button", {
      type: "button",
      class: `intake-decision intake-decision-${action}`,
      "data-testid": `intake-decision-${action}`,
      text: label,
    });
    button.addEventListener("click", async () => {
      button.disabled = true;
      errorLine.hidden = true;
      try {
        const outcome = await postJson(
          `/api/intakes/${encodeURIComponent(intakeId)}/decisions`,
          { action }
        );
        statusLine.textContent =
          outcome && outcome.outcome ? `已决策：${outcome.outcome}` : "已决策";
        decisionBar.hidden = true;
      } catch (error) {
        errorLine.textContent = error && error.message ? error.message : String(error);
        errorLine.hidden = false;
        button.disabled = false;
      }
    });
    return button;
  }

  return { root, stopPolling };
}
