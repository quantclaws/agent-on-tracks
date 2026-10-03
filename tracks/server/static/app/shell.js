/**
 * Workbench shell bootstrap (interfaces §1l.1/§1l.2).
 *
 * The server renders one shared shell carrying the ``data-route`` deep link;
 * this entry restores the initial tab/sidebar, renders the active functional
 * area, and — on run-scoped entries — subscribes to the merged event stream
 * from the snapshot's ``event_cursor``. Unknown routes degrade explicitly.
 */

import { requestJson, setCsrfToken } from "./api.js";
import { element } from "./dom.js";
import { featureForRoute, readRoute } from "./router.js";
import { renderSidebar } from "./sidebar.js";
import { connectEventStream } from "./sse.js";
import { openTab } from "./tabs.js";
import { renderView } from "./views.js";

const TITLES = {
  projects: "Projects",
  runs: "Runs",
  docs: "Documents",
  review: "Review",
  todos: "Todos",
  settings: "Settings",
  account: "Account",
};

export function bootstrap(root = document) {
  const loginForm = root.querySelector('[data-testid="login-form"]');
  if (loginForm) {
    wireLoginShell(root, loginForm);
    return null;
  }
  seedSessionCredential();
  const shell = root.querySelector("#workbench") || root;
  const deepLink = readRoute(root);
  const feature = featureForRoute(deepLink.route);
  if (!feature) {
    renderSidebar({ title: "Unknown", nodes: [] }, shell);
    renderView(null, shell.querySelector('[data-testid="main-area"]'), {
      ...deepLink,
      title: "Unknown",
    });
    return null;
  }
  const tab = openTab(feature, {
    ...deepLink,
    feature,
    title: TITLES[feature] || feature,
  });
  if (deepLink.runId) {
    subscribeRun(deepLink.runId, shell);
  }
  return tab;
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
    for (let attempt = 0; attempt < 4; attempt += 1) {
      try {
        await requestJson("/api/auth/name", { method: "POST", body: payload });
        globalThis.location.assign("/");
        return;
      } catch (error) {
        const transient = error && error.status === 401;
        if (!transient || attempt === 3) {
          showError(errorBox, error);
          return;
        }
        await new Promise((resolve) => globalThis.setTimeout(resolve, 350));
      }
    }
  });
}

async function revealForPendingName(root) {
  // On a session-bearing document: expand the name step while it is still
  // uncollected; a complete profile goes straight to the workbench. A 401
  // here is usually the transient cookie-attachment flap around document
  // transitions — retry briefly before concluding there is no session.
  for (let attempt = 0; attempt < 6; attempt += 1) {
    try {
      const profile = await requestJson("/api/auth/profile");
      if (profile && profile.csrf_token) {
        setCsrfToken(profile.csrf_token);
      }
      if (profile && profile.name_set) {
        globalThis.location.assign("/");
        return;
      }
      revealNameStep(root);
      return;
    } catch (error) {
      if (attempt === 5) {
        return; /* no session: the plain password form stays */
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, 400));
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

async function subscribeRun(runId, shell) {
  const status = ensureStatus(shell);
  let cursor = null;
  try {
    const detail = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
    cursor = detail.event_cursor || null;
  } catch (error) {
    status.textContent = "stream unavailable";
    return null;
  }
  return connectEventStream({
    runId,
    event_cursor: cursor,
    onStatus: (state) => {
      status.textContent = state;
    },
  });
}

function ensureStatus(shell) {
  let node = shell.querySelector('[data-testid="stream-status"]');
  if (!node) {
    node = element("span", {
      class: "stream-status",
      "data-testid": "stream-status",
      text: "connecting",
    });
    shell.append(node);
  }
  return node;
}

if (typeof document !== "undefined") {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bootstrap());
  } else {
    bootstrap();
  }
}