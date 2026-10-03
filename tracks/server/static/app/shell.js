/**
 * Workbench shell bootstrap (interfaces §1l.1/§1l.2).
 *
 * The server renders one shared shell carrying the ``data-route`` deep link;
 * this entry restores the initial tab/sidebar, renders the active functional
 * area, and wires the tab bar's icon items to in-page tab switches (never a
 * full-page navigation — FR-0316). Unknown routes degrade explicitly.
 */

import { requestJson, setCsrfToken } from "./api.js";
import { featureForRoute, readRoute } from "./router.js";
import { renderSidebar } from "./sidebar.js";
import { openTab } from "./tabs.js";
import { renderView } from "./views.js";

const TITLES = {
  projects: "projects",
  runs: "runs",
  docs: "docs centre",
  review: "review",
  todos: "todos",
  settings: "settings",
  account: "account",
};

export function bootstrap(root = document) {
  const loginForm = root.querySelector('[data-testid="login-form"]');
  if (loginForm) {
    wireLoginShell(root, loginForm);
    return null;
  }
  seedSessionCredential();
  const shell = root.querySelector("#workbench") || root;
  wireTabbar(shell);
  const deepLink = readRoute(root);
  const feature = featureForRoute(deepLink.route);
  if (!feature) {
    renderSidebar({ title: "unknown", nodes: [] }, shell);
    renderView(null, shell.querySelector('[data-testid="main-area"]'), {
      ...deepLink,
      title: "unknown",
    });
    return null;
  }
  if ((deepLink.route === "run_detail" || deepLink.route === "release") && deepLink.runId) {
    // A run-scoped deep link restores the run's own tab (implicit: the
    // restored face stays out of the open-tab strip until the user opens it).
    return openTab(deepLink.runId, {
      feature: "runs",
      view: "run_detail",
      runId: deepLink.runId,
      implicit: true,
      title: deepLink.runId,
    });
  }
  const tab = openTab(feature, {
    feature,
    implicit: true,
    route: deepLink.route,
    projectId: deepLink.projectId,
    title: TITLES[feature] || feature,
  });
  return tab;
}

// -- tab bar wiring (FR-0316/FR-0318): one delegated handler turns the seven
// server-rendered icon items into in-page tab switches. A click never follows
// the <a href> — the same document instance continues — and marks the live
// document so a full reload is distinguishable from an in-page switch (the
// frozen ui contract's same-document probe).

function wireTabbar(shell) {
  if (shell.__tabbarWired) return;
  shell.__tabbarWired = true;
  shell.addEventListener("click", (event) => {
    const item = event.target.closest
      ? event.target.closest('[data-testid^="tabbar-item-"]')
      : null;
    if (!item || !shell.contains(item)) return;
    event.preventDefault();
    event.stopPropagation();
    const feature = (item.getAttribute("data-testid") || "").replace(
      "tabbar-item-",
      ""
    );
    globalThis.__tracks_document = "instance-1";
    openTab(feature, {
      feature,
      title: TITLES[feature] || feature,
      // the gear opens its tab without changing the sidebar (FR-0318-03)
      keepSidebar: feature === "settings",
    });
  });
}

// -- login shell (§1m.1/§1m.2): fetch auth flow, inline error, in-place
// name step — the native form POST never navigates to a bare JSON body.
//
// Live 2026-10-03 (Chromium/serve stack): a document loaded BEFORE the
// session cookie exists never attaches it to subresource requests — the
// password step therefore reloads the login page on success, and the
// reloaded document (cookie-bearing) probes the profile face to expand
// the name step in place and carries the credential for the name bind.

function wireLoginShell(root, form) {
  const errorBox = root.querySelector('[data-testid="login-error"]');
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const field = form.querySelector('[data-testid="login-password"]');
    hideError(errorBox);
    try {
      const session = await requestJson("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password: (field && field.value) || "" }),
      });
      setCsrfToken(session.csrf_token);
      globalThis.location.assign("/login");
    } catch (error) {
      showError(errorBox, error);
    }
  });
  wireNameStep(root, errorBox);
  revealForPendingName(root);
}

function wireNameStep(root, errorBox) {
  const nameSubmit = root.querySelector('[data-testid="name-submit"]');
  if (!nameSubmit) {
    return;
  }
  nameSubmit.addEventListener("click", async () => {
    const input = root.querySelector('[data-testid="name-input"]');
    hideError(errorBox);
    const payload = JSON.stringify({ name: (input && input.value) || "" });
    // Live 2026-10-03: the serve stack's session cookie attachment to
    // subresource fetches flaps for ~1s around document transitions — a
    // 401 here is transient, so the bind retries briefly before failing.
    for (let attempt = 0; attempt < 10; attempt += 1) {
      try {
        await requestJson("/api/auth/name", { method: "POST", body: payload });
        globalThis.location.assign("/");
        return;
      } catch (error) {
        const transient = error && error.status === 401;
        if (!transient || attempt === 9) {
          showError(errorBox, error);
          return;
        }
        await new Promise((resolve) => globalThis.setTimeout(resolve, 700));
      }
    }
  });
}

async function revealForPendingName(root) {
  // On a session-bearing document, seed the CSRF credential and expand
  // the name step while it is still uncollected; a 401 is the plain
  // no-session login form. Never navigates on its own.
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      const profile = await requestJson("/api/auth/profile");
      if (profile && profile.csrf_token) {
        setCsrfToken(profile.csrf_token);
      }
      if (!profile.name_set) {
        revealNameStep(root);
      }
      return;
    } catch (error) {
      if (attempt === 9) {
        return;
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 700));
    }
  }
}

function revealNameStep(root) {
  const nameForm = root.querySelector('[data-testid="login-name-form"]');
  if (nameForm) {
    nameForm.removeAttribute("hidden");
  }
  const input = root.querySelector('[data-testid="name-input"]');
  if (input) {
    input.focus();
  }
}

function showError(errorBox, error) {
  if (!errorBox) {
    return;
  }
  const payload = error && error.payload;
  const reported =
    (payload && payload.error && (payload.error.detail || payload.error.reason)) ||
    (error && error.message) ||
    "sign-in failed";
  errorBox.textContent = reported;
  errorBox.removeAttribute("hidden");
}

function hideError(errorBox) {
  if (errorBox) {
    errorBox.setAttribute("hidden", "");
    errorBox.textContent = "";
  }
}

// Each workbench page boot re-seeds the in-memory CSRF credential from the
// auth face (§1l.6: memory only, never persisted client-side).

async function seedSessionCredential() {
  try {
    const profile = await requestJson("/api/auth/profile");
    if (profile && profile.csrf_token) {
      setCsrfToken(profile.csrf_token);
    }
  } catch (error) {
    /* the 401 gate face owns the redirect; the shell stays inert */
  }
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bootstrap());
  } else {
    bootstrap();
  }
}
