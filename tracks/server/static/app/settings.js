/**
 * Settings window: network panel (proxy configuration).
 *
 * The view owns its sidebar menu (the settings tab is no longer the
 * sidebar-preserving exception — FR-0318-03 updated by the network settings
 * requirement). GET /api/settings fills the form; POST /api/settings/network
 * saves it through the shared CSRF/idempotency JSON client. Validation
 * failures and transport errors land on the panel's status line instead of
 * being reported as success.
 */

import { requestJson } from "./api.js";
import { element } from "./dom.js";
import { renderSidebar } from "./sidebar.js";

const PROXY_TYPES = ["http", "https", "socks5"];

export function renderSettingsView(body) {
  renderSidebar({
    title: "settings",
    window: "settings",
    nodes: [
      {
        label: "网络",
        testid: "settings-menu-network",
        href: "#",
        selected: true,
      },
    ],
  });

  const typeInput = element("select", {
    id: "settings-network-proxy-type",
    "data-testid": "settings-network-proxy-type",
  });
  for (const type of PROXY_TYPES) {
    typeInput.append(element("option", { value: type, text: type }));
  }

  const hostInput = element("input", {
    id: "settings-network-proxy-host",
    "data-testid": "settings-network-proxy-host",
    type: "text",
    placeholder: "192.168.0.102",
    autocomplete: "off",
    spellcheck: "false",
  });

  const portInput = element("input", {
    id: "settings-network-proxy-port",
    "data-testid": "settings-network-proxy-port",
    type: "number",
    min: "1",
    max: "65535",
    step: "1",
    placeholder: "8080",
    inputmode: "numeric",
  });

  const fallbackInput = element("input", {
    id: "settings-network-github-fallback",
    "data-testid": "settings-network-github-fallback",
    type: "checkbox",
  });

  const saveButton = element("button", {
    type: "submit",
    class: "settings-save",
    "data-testid": "settings-network-save",
    text: "保存",
  });

  const status = element("p", {
    class: "settings-status",
    "data-testid": "settings-network-status",
    role: "status",
  });

  const form = element("form", {
    class: "settings-form",
    "data-testid": "settings-network-form",
  });
  form.append(
    field("settings-network-proxy-type", "类型", typeInput),
    field("settings-network-proxy-host", "主机", hostInput),
    field("settings-network-proxy-port", "端口", portInput),
    element(
      "label",
      {
        class: "settings-checkbox",
        for: "settings-network-github-fallback",
      },
      [
        fallbackInput,
        element("span", {
          text: "允许 GitHub 操作在本机网络不可用时使用此代理兜底",
        }),
      ]
    ),
    element("div", { class: "settings-actions" }, [saveButton]),
    status
  );

  const section = element("section", {
    class: "settings-panel",
    "data-testid": "settings-network",
    "aria-label": "网络",
  });
  section.append(
    element("h2", { class: "settings-title", text: "网络" }),
    element("p", {
      class: "settings-hint",
      text: "GitHub 操作始终优先直连本地网络；仅当直连失败且已勾选兜底时使用此代理。",
    }),
    form
  );
  body.append(section);

  hostInput.addEventListener("input", syncFallback);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    saveButton.disabled = true;
    setStatus("保存中…");
    try {
      const saved = await requestJson("/api/settings/network", {
        method: "POST",
        body: JSON.stringify(proxyPayload()),
      });
      applyProxy(saved && saved.proxy);
      setStatus("已保存", "ok");
    } catch (error) {
      setStatus(`保存失败：${describeError(error)}`, "error");
    } finally {
      saveButton.disabled = false;
    }
  });

  syncFallback();
  void loadSettings();

  function proxyPayload() {
    const host = hostInput.value.trim();
    const port = Number.parseInt(portInput.value, 10);
    return {
      proxy: {
        type: typeInput.value,
        host,
        port: Number.isFinite(port) ? port : 0,
        github_fallback: host ? fallbackInput.checked : false,
      },
    };
  }

  async function loadSettings() {
    try {
      const settings = await requestJson("/api/settings");
      applyProxy(settings && settings.network && settings.network.proxy);
    } catch (error) {
      setStatus(`加载设置失败：${describeError(error)}`, "error");
    }
  }

  function applyProxy(proxy) {
    const value = proxy && typeof proxy === "object" ? proxy : {};
    typeInput.value = PROXY_TYPES.includes(value.type) ? value.type : "http";
    hostInput.value = typeof value.host === "string" ? value.host : "";
    const port = Number(value.port);
    portInput.value = Number.isInteger(port) && port > 0 ? String(port) : "";
    fallbackInput.checked = Boolean(value.github_fallback);
    syncFallback();
  }

  function syncFallback() {
    const hasHost = hostInput.value.trim() !== "";
    if (!hasHost) {
      fallbackInput.checked = false;
      fallbackInput.disabled = true;
    } else {
      fallbackInput.disabled = false;
    }
  }

  function setStatus(text, state = "") {
    status.textContent = text;
    status.dataset.state = state;
    status.classList.toggle("settings-status-ok", state === "ok");
    status.classList.toggle("settings-status-error", state === "error");
  }
}

function field(id, label, control) {
  return element("div", { class: "settings-field" }, [
    element("label", { for: id, text: label }),
    control,
  ]);
}

function describeError(error) {
  const payload = error && error.payload;
  if (payload && payload.detail) return stringify(payload.detail);
  if (payload && typeof payload.error === "string") return payload.error;
  if (payload && payload.error) {
    const nested = payload.error.detail || payload.error.reason;
    if (nested) return stringify(nested);
  }
  return error && error.message ? error.message : String(error);
}

function stringify(value) {
  return typeof value === "string" ? value : JSON.stringify(value);
}
