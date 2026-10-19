/**
 * SM-04 window state machine (interfaces §1l.4, revised 2026-10-05 by
 * operator directive: icon-switched windows, no open-tab strip).
 *
 * Invariants encoded here:
 * - one atomic switch: activating a window switches the sidebar and the
 *   main area in the same call — no sidebar-only intermediate state;
 * - one window per functional area: the tab-bar icons are the only
 *   switcher, re-opening an area reuses its existing window (`windows`
 *   keyed by area), and no open-tab strip exists anywhere;
 * - the settings gear now switches the sidebar to its own menu tree along
 *   with the main window (FR-0318-03 updated by the network-settings
 *   requirement); ``keepSidebar`` remains an opt-in for views that render
 *   their own sidebar after activation;
 * - window state lives in memory only (module state, never persisted), so
 *   a refresh restores exactly the deep-linked area.
 */

import { renderSidebar } from "./sidebar.js";
import { renderView } from "./views.js";

const windows = new Map(); // area -> { route, title, context }
let active = null;
let activationToken = 0;

export function activeWindow() {
  return active;
}

export function openTab(area, context = {}) {
  const existing = windows.has(area) ? windows.get(area) : null;
  if (existing) {
    existing.title = context.title || existing.title;
    existing.context = { ...existing.context, ...context };
    activateTab(area);
    return existing;
  }
  const window = {
    route: area,
    title: context.title || area,
    context: { ...context },
  };
  windows.set(area, window);
  activateTab(area);
  return window;
}

function highlightTabbarItem(shell, area) {
  for (const item of shell.querySelectorAll('[data-testid^="tabbar-item-"]')) {
    const activeItem =
      item.getAttribute("data-testid") === `tabbar-item-${area}`;
    if (activeItem) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
}

export async function activateTab(area) {
  const window = windows.has(area) ? windows.get(area) : null;
  if (!window) return null;
  if (active && active !== window) {
    // Before any chrome moves, the outgoing view may refuse the unmount
    // (Prism round-2): a view holding a pending human decision — the docs
    // conflict dialog, an unsaveable draft — keeps its visible face and the
    // switch is abandoned with the chrome untouched.
    const host =
      (document.querySelector("#workbench") || document).querySelector(
        '[data-testid="main-body"]'
      ) || null;
    const currentView = host && host.firstElementChild;
    if (currentView && currentView.__beforeUnmount) {
      const ok = await currentView.__beforeUnmount();
      if (ok === false) return active;
    }
  }
  active = window;
  // A tab-bar re-click re-renders the area's window from its CURRENT context
  // (the registry re-lists): a re-click DROPS a one-shot deep-link runId
  // and returns to the registry — EXCEPT when the re-click is the tab
  // machine's own first activation of a freshly opened deep-link window
  // (no prior activation yet): then the focus must survive.
  if (area === "releases" && window.context && window.context.runId && window._activated) {
    delete window.context.runId;
    delete window.context.runTitle;
  }
  window._activated = true;
  const shell = document.querySelector("#workbench") || document;
  const mainArea = shell.querySelector('[data-testid="main-area"]');
  const token = ++activationToken;
  // One atomic switch: the sidebar and the main area move together. A view
  // may opt out and render its own sidebar from the mounted view.
  highlightTabbarItem(shell, area);
  if (!window.context.keepSidebar) {
    renderSidebar({ title: window.title, nodes: window.context.nodes || [] }, shell);
  }
  const rendered = renderView(window.context.view || area, mainArea, window.context);
  if (rendered && rendered.done) {
    await rendered.done;
  }
  return window;
}
