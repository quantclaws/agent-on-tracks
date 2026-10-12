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
      text: model.title || "unknown",
    })
  );
  // Releases-window sidebar container (§1e.2): the three groups (registry /
  // backlog / archive) ride this landmark. Plain trees (docs·runs switching
  // surfaces) keep the bare list without the container, so the releases
  // face stays distinguishable from other tabs' sub-content.
  let treeHost = host;
  if (model.window === "releases" || (model.groups || []).length === 3) {
    treeHost = element("section", {
      class: "releases-sidebar",
      "data-testid": "releases-sidebar",
      "aria-label": "Releases sidebar",
    });
    host.append(treeHost);
  }
  const list = element("ul", {
    class: "sidebar-list",
    "data-testid": "sidebar-tree",
  });
  for (const node of model.nodes || []) {
    list.append(
      element("li", { class: "sidebar-node-wrap" }, [
        element("a", {
          class: "sidebar-node",
          href: node.href || "#",
          "data-testid": node.testid || "sidebar-node",
          text: node.label || "unknown",
        }),
      ])
    );
  }
  // Grouped mode (louke Dev Docs tree, #221): one collapsible group per
  // version directory — the summary carries the version label, the children
  // are that version's documents. The first group renders open so a fresh
  // session exposes the newest version's documents directly.
  // Releases-window grouping (§1e.2 sidebar three groups, §9.2): the model
  // carries group.testid (the closed releases-group-* face) plus an
  // optional group.versionTestid (the per-version releases-registry-*
  // anchor). A hidden marker inside the details exposes the version
  // anchor WITHOUT duplicating group lookups (one group per testid; the
  // marker counts only for the version testid).
  (model.groups || []).forEach((group, index) => {
    const children = element("div", { class: "sidebar-group-items" });
    for (const node of group.children || []) {
      children.append(
        element("a", {
          class: "sidebar-node",
          href: node.href || "#",
          "data-testid": node.testid || "sidebar-node",
          ...(node.attrs || {}),
          text: node.label || "unknown",
        })
      );
    }
    const summaryNode = element("summary", {
      class: "sidebar-group-label",
      text: group.label || "unknown",
    });
    if (group.versionTestid) {
      // the version anchor is a hidden marker — visible groups stay
      // unique; hidden markers are ignored by to_be_visible lookups.
      summaryNode.append(
        element("span", {
          class: "releases-version-anchor",
          "data-testid": group.versionTestid,
          hidden: true,
          text: group.label || "version",
        })
      );
    }
    list.append(
      element("li", { class: "sidebar-group-wrap" }, [
        element("details", {
          class: "sidebar-group",
          "data-testid": group.testid || "sidebar-group",
          open: index === 0 || group.open === true,
        }, [summaryNode, children]),
      ])
    );
  });
  treeHost.append(list);
  return host;
}
