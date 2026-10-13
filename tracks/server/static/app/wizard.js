/**
 * Create wizard modal (Releases window "＋" entry).
 *
 * Two pages: page 1 picks feature vs hotfix; page 2 collects the payload.
 * The feature face reuses the releases Scribe panel contract (the caller
 * supplies onDone; releases.js renders its pollIntakeReport face). The
 * hotfix face reuses the create_run closed shape (§2b #8). All controls
 * carry data-testid faces; native ES module, no framework.
 */

import { requestJson } from "./api.js";
import { element } from "./dom.js";

const HOTFIX_SCENARIOS = ["post-release", "dev"];

/** Open the two-page create modal. Returns a close function. */
export function openCreateWizard({ projectId, onDone } = {}) {
  const pid = projectId || "";
  let closed = false;
  let busy = false;

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
    ]),
    wizardBar([cancelButton(close, () => busy)])
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
      cancelButton(close, () => busy),
      createRelease,
    ])
  );
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
      busy = false;
      closed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
      if (typeof onDone === "function") {
        onDone({ kind: "feature", intakeId });
      }
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
      cancelButton(close, () => busy),
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
    // Single-page mount: only the active page lives in the dialog, so no
    // testid (wizard-cancel / wizard-error / wizard-back) ever duplicates.
    dialog.replaceChildren(page);
    for (const candidate of [page1, pageFeature, pageHotfix]) {
      if (candidate === page) candidate.removeAttribute("hidden");
      else candidate.setAttribute("hidden", "");
    }
  }

  dialog.append(page1);
  document.body.append(overlay);
  return close;
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

function cancelButton(close, isBusy) {
  const btn = element("button", {
    type: "button",
    class: "wizard-cancel",
    "data-testid": "wizard-cancel",
    text: "取消",
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
