/**
 * SM-04 tab state machine (interfaces §1l.4).
 *
 * Invariants encoded here:
 * - one atomic switch: activating a tab switches the sidebar and opens or
 *   activates the main-area tab in the same call — no sidebar-only state;
 * - coexisting tabs: opening another tab never closes an open one;
 * - unique instance: activating an already-open route reuses that tab;
 * - explicit close is the only close path (``closeTab`` on a user action);
 *   no global close-everything action exists;
 * - the set lives in memory only (module state, never persisted), so a
 *   refresh starts from the empty set. A server deep link may restore the
 *   initial functional area as an *implicit* tab: it renders the view but
 *   stays out of the open-tab strip until a user action opens it, so a
 *   fresh browser session starts with the empty tab set (FR-0318-02).
 */

import { clear, element } from "./dom.js";
import { renderSidebar } from "./sidebar.js";
import { renderView } from "./views.js";

const tabs = [];
let active = null;
let activationToken = 0;

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
    existing.implicit = Boolean(existing.implicit && context.implicit);
    existing.context = { ...existing.context, ...context };
    existing.context.implicit = existing.implicit;
    if (context.activate === false) {
      renderTabStrip(shellRoot());
      return existing;
    }
    activateTab(route);
    return existing;
  }
  const tab = {
    route,
    title: context.title || route,
    implicit: Boolean(context.implicit),
    context: { ...context, implicit: Boolean(context.implicit) },
  };
  tabs.push(tab);
  if (context.activate === false) {
    renderTabStrip(shellRoot());
    return tab;
  }
  activateTab(route);
  return tab;
}

export async function activateTab(route) {
  const tab = tabs.find((candidate) => candidate.route === route);
  if (!tab) return null;
  active = tab;
  const shell = shellRoot();
  const mainArea = shell.querySelector('[data-testid="main-area"]');
  const token = ++activationToken;
  // One atomic switch: the sidebar and the main area move together; the
  // settings gear is the documented exception that opens its tab without
  // touching the sidebar (FR-0318-03).
  if (!tab.context.keepSidebar) {
    renderSidebar({ title: tab.title, nodes: tab.context.nodes || [] }, shell);
  }
  const rendered = renderView(tab.route, mainArea, tab.context);
  if (rendered && rendered.done) {
    await rendered.done;
  }
  if (token === activationToken) {
    renderTabStrip(shell);
  }
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
    else renderTabStrip(shellRoot());
    return removed;
  }
  renderTabStrip(shellRoot());
  return removed;
}

function shellRoot() {
  return document.querySelector("#workbench") || document;
}

function renderTabStrip(shell) {
  const strip = shell.querySelector('[data-testid="open-tabs"]');
  if (!strip) return null;
  clear(strip);
  for (const tab of tabs) {
    if (tab.implicit) continue; // the deep-link restore stays out of the set
    const close = element("button", {
      type: "button",
      class: "tab-close",
      "data-testid": `tab-close-${tab.route}`,
      "aria-label": `Close ${tab.title || tab.route}`,
      text: "x",
    });
    close.addEventListener("click", () => closeTab(tab.route));
    const entry = element(
      "div",
      {
        class: "tab",
        "data-testid": `tab-${tab.route}`,
        role: "tab",
        "aria-selected": tab === active ? "true" : "false",
      },
      [
        element("span", { class: "tab-label", text: tab.title || "unknown" }),
        close,
      ]
    );
    entry.addEventListener("click", (event) => {
      if (event.target === close || close.contains(event.target)) return;
      activateTab(tab.route);
    });
    strip.append(entry);
  }
  return strip;
}
