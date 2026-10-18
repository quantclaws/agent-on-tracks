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
  const heading = element("h2", {
    class: "sidebar-heading",
    "data-testid": "sidebar-heading",
    text: model.title || "unknown",
  });
  if (model.action) {
    // Optional heading action (releases "+" create entry, v0.12 OOB): the
    // heading row wraps the unchanged h2 beside a small action button.
    // Without a model.action the bare h2 append above stays the only
    // branch — every other window renders byte-identical output.
    const action = model.action;
    const actionButton = element("button", {
      type: "button",
      class: "sidebar-action",
      "data-testid": action.testid || "sidebar-action",
      "aria-label": action.ariaLabel || action.label || "action",
      text: action.label || "+",
    });
    if (typeof action.onClick === "function") {
      actionButton.addEventListener("click", () => action.onClick());
    }
    host.append(
      element("div", { class: "sidebar-heading-row" }, [heading, actionButton])
    );
  } else {
    host.append(heading);
  }
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
      const selected =
        (node.attrs && node.attrs["data-selected"] === "true") ||
        node.selected === true;
      children.append(
        element("a", {
          class: `sidebar-node${selected ? " sidebar-node-selected" : ""}`,
          href: node.href || "#",
          "data-testid": node.testid || "sidebar-node",
          ...(node.attrs || {}),
          ...(selected
            ? { "data-selected": "true", "aria-current": "true" }
            : {}),
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
