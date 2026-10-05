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
 * - the settings gear is the documented exception that switches the main
 *   window without touching the sidebar (FR-0318-03);
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

export async function activateTab(area) {
  const window = windows.has(area) ? windows.get(area) : null;
  if (!window) return null;
  active = window;
  const shell = document.querySelector("#workbench") || document;
  const mainArea = shell.querySelector('[data-testid="main-area"]');
  const token = ++activationToken;
  // One atomic switch: the sidebar and the main area move together; the
  // settings gear is the documented exception that switches its window
  // without touching the sidebar (FR-0318-03).
  if (!window.context.keepSidebar) {
    renderSidebar({ title: window.title, nodes: window.context.nodes || [] }, shell);
  }
  const rendered = renderView(window.context.view || area, mainArea, window.context);
  if (rendered && rendered.done) {
    await rendered.done;
  }
  return window;
}
