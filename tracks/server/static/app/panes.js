/**
 * Multi-pane document workspace (IF-DOCCENTER-001; interfaces §1n.3,
 * reworked 2026-10-05 after louke's Dev Docs design).
 *
 * Each pane IS a full document face — its bar carries the file selector,
 * a load status, the discussion navigation, and the split/close controls;
 * its body hosts the document editor. Up to four panes stand side by side
 * (the container's pane count drives the grid). A request beyond four
 * columns is a no-op — existing panes and their content are never touched.
 */

import { element } from "./dom.js";

export const MAX_PANES = 4;

export function createPaneContainer({ version = "", docs = [], onLoad, onActivate, onSplit } = {}) {
  const panes = [];
  let ordinal = 0;
  // The add control is NOT rendered here: each pane's own split control (and
  // any caller surface) drives addPane — panes only exist beside an open
  // document. ``onSplit`` receives each newly added pane so the caller can
  // load the active document into it (#222 moved the split entry point to
  // the pane bar).
  const root = element("div", { class: "panes", "data-testid": "doc-panes" });

  function addPane() {
    if (panes.length >= MAX_PANES) {
      return null; // refuse the fifth column; existing panes stay untouched
    }
    const pane = createPane({ version, ordinal }, docs, {
      load: onLoad,
      activate: onActivate,
      split: () => {
        const added = addPane();
        if (added && onSplit) onSplit(added);
      },
      close: () => closePane(pane),
    });
    ordinal += 1;
    panes.push(pane);
    root.append(pane.root);
    refreshLayout();
    return pane;
  }

  function closePane(pane) {
    // louke semantics: at least one pane stays open
    if (panes.length <= 1 || !panes.includes(pane)) return null;
    panes.splice(panes.indexOf(pane), 1);
    pane.root.remove();
    refreshLayout();
    return pane;
  }

  function refreshLayout() {
    root.setAttribute("data-pane-count", String(panes.length));
  }

  refreshLayout();
  return { root, panes, addPane, closePane };
}

function createPane(reference, docs, controls) {
  const select = element("select", {
    class: "pane-select",
    "data-testid": "pane-selector",
    "aria-label": "Pane document",
  });
  for (const doc of docs) {
    const name = doc && doc.doc ? doc.doc : doc;
    select.append(element("option", { value: name, text: name }));
  }
  if (!select.options.length) {
    select.append(element("option", { value: "", text: "no documents" }));
  }
  const status = element("span", {
    class: "doc-pane-status",
    "data-testid": "pane-status",
    text: "",
  });
  // The pane's own edit toggle: pressed state is per-pane only.
  const editButton = element("button", {
    type: "button",
    class: "pane-toolbar-edit",
    "data-testid": "pane-toolbar-edit",
    "aria-pressed": "false",
    title: "Toggle edit",
    text: "✎",
  });
  editButton.addEventListener("click", () => {
    const pressed = editButton.getAttribute("aria-pressed") === "true";
    editButton.setAttribute("aria-pressed", pressed ? "false" : "true");
  });
  const nextDiscussion = element("button", {
    type: "button",
    class: "pane-tool",
    "data-testid": "pane-next-discussion",
    title: "Next discussion",
    text: "↯",
  });
  const nextUnresolved = element("button", {
    type: "button",
    class: "pane-tool",
    "data-testid": "pane-next-unresolved",
    title: "Next unresolved discussion",
    text: "!",
  });
  const splitButton = element("button", {
    type: "button",
    class: "pane-tool",
    "data-testid": "pane-split",
    title: "新增文档分屏",
    text: "＋",
  });
  splitButton.addEventListener("click", () => controls.split && controls.split());
  const closeButton = element("button", {
    type: "button",
    class: "pane-tool",
    "data-testid": "pane-close",
    title: "关闭分屏",
    text: "×",
  });
  closeButton.addEventListener("click", () => controls.close && controls.close());
  const toolbar = element("div", { class: "pane-toolbar", "data-testid": "pane-toolbar" }, [
    nextDiscussion,
    nextUnresolved,
    editButton,
    splitButton,
    closeButton,
  ]);
  const body = element("div", { class: "pane-body", "data-testid": "pane-body" });
  const root = element("section", {
    class: "pane",
    "data-testid": `doc-pane-${reference.ordinal}`,
  }, [
    element("header", { class: "pane-header" }, [select, status, toolbar]),
    body,
  ]);
  // Any interaction inside a pane makes it the active face (the sidebar and
  // the tab bar load their documents into the active pane).
  root.addEventListener(
    "click",
    () => controls.activate && controls.activate(pane),
    true
  );
  select.addEventListener("change", () => {
    controls.activate && controls.activate(pane);
    controls.load && controls.load(pane, select.value, body);
  });
  const pane = { root, select, toolbar, editButton, body, status, reference };
  return pane;
}
