/**
 * SM-04 tab state machine (interfaces §1l.4).
 *
 * Invariants encoded here:
 * - one atomic switch: activating a tab switches the sidebar and opens or
 *   activates the main-area tab in the same call — no sidebar-only state;
 * - coexisting tabs: opening another tab never closes an open one;
 * - unique instance: activating an already-open route reuses that tab;
 * - explicit close is the only close path (``closeTab`` on a user action);
 * no global close-all action exists;
 * - the set lives in memory only (module state, never persisted), so a
 *   refresh starts from the empty set.
 */

import { clear, element } from "./dom.js";
import { renderSidebar } from "./sidebar.js";
import { renderView } from "./views.js";

const tabs = [];
let active = null;

export function openTabs() {
  return tabs.slice();
}

export function activeTab() {
  return active;
}

export function openTab(route, context = {}) {
  const existing = tabs.find((tab) => tab.route === route);
  if (existing) {
    existing.title = context.title || existing.title;
    existing.context = { ...existing.context, ...context };
    activateTab(route);
    return existing;
  }
  const tab = { route, title: context.title || route, context };
  tabs.push(tab);
  activateTab(route);
  return tab;
}

export function activateTab(route) {
  const tab = tabs.find((candidate) => candidate.route === route);
  if (!tab) return null;
  active = tab;
  const shell = document.querySelector("#workbench") || document;
  const mainArea = shell.querySelector('[data-testid="main-area"]');
  renderSidebar({ title: tab.title, nodes: tab.context.nodes || [] }, shell);
  renderView(tab.route, mainArea, tab.context);
  renderTabStrip(shell);
  return tab;
}

export function closeTab(route) {
  const index = tabs.findIndex((tab) => tab.route === route);
  if (index === -1) return null;
  const [removed] = tabs.splice(index, 1);
  if (active === removed) {
    active = null;
    const next = tabs[index] || tabs[index - 1] || null;
    if (next) activateTab(next.route);
    else renderTabStrip(document.querySelector("#workbench") || document);
    return removed;
  }
  renderTabStrip(document.querySelector("#workbench") || document);
  return removed;
}

function renderTabStrip(shell) {
  const strip = shell.querySelector('[data-testid="tab-strip"]');
  if (!strip) return null;
  clear(strip);
  for (const tab of tabs) {
    const close = element("button", {
      type: "button",
      class: "tab-close",
      "data-testid": `tab-close-${tab.route}`,
      text: "x",
    });
    close.addEventListener("click", () => closeTab(tab.route));
    strip.append(
      element(
        "div",
        {
          class: "tab",
          "data-testid": `tab-${tab.route}`,
          "aria-selected": tab === active ? "true" : "false",
        },
        [
          element("span", { class: "tab-label", text: tab.title || "Unknown" }),
          close,
        ]
      )
    );
  }
  return strip;
}