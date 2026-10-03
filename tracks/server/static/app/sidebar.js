/**
 * Sidebar navigation tree (interfaces §1l.3/§1l.4).
 *
 * The sidebar only shows the active tab-bar item's child tree: ``tabs.js``
 * hands the active model in during the same activation switch, so there is no
 * intermediate state where the sidebar moved but the main area did not.
 * Unknown nodes degrade to a readable marker instead of a blank surface.
 */

import { clear, element } from "./dom.js";

export function sidebarRoot(root = document) {
  return root.querySelector('[data-testid="sidebar"]');
}

export function renderSidebar(model = {}, root = document) {
  const host = sidebarRoot(root);
  if (!host) return null;
  clear(host);
  host.append(
    element("h2", {
      class: "sidebar-heading",
      "data-testid": "sidebar-heading",
      text: model.title || "Unknown",
    })
  );
  const list = element("ul", { class: "sidebar-list", "data-testid": "sidebar-list" });
  for (const node of model.nodes || []) {
    list.append(
      element("li", { class: "sidebar-node-wrap" }, [
        element("a", {
          class: "sidebar-node",
          href: node.href || "#",
          "data-testid": node.testid || "sidebar-node",
          text: node.label || "Unknown",
        }),
      ])
    );
  }
  host.append(list);
  return host;
}