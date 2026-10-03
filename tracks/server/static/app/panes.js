/**
 * Multi-pane split container (IF-DOCCENTER-001; interfaces §1n.3).
 *
 * Up to four independent columns: each pane owns its document selector and
 * toolbar and loads a different document, with selection and editing kept
 * isolated per pane. A request beyond four columns is a no-op — existing
 * panes and their content are never touched.
 */

import { element } from "./dom.js";

export const MAX_PANES = 4;

export function createPaneContainer({ version = "", docs = [], onSelect } = {}) {
  const panes = [];
  let ordinal = 0;
  const addButton = element("button", {
    type: "button",
    class: "pane-add",
    "data-testid": "pane-add",
    text: "Add pane",
  });
  const root = element("div", { class: "panes", "data-testid": "doc-panes" }, [
    element("div", { class: "pane-controls", "data-testid": "pane-controls" }, [addButton]),
  ]);
  addButton.addEventListener("click", () => addPane());

  function addPane() {
    if (panes.length >= MAX_PANES) {
      return null; // refuse the fifth column; existing panes stay untouched
    }
    const pane = createPane({ version, ordinal }, docs, onSelect);
    ordinal += 1;
    panes.push(pane);
    root.append(pane.root);
    return pane;
  }

  return { root, panes, addPane };
}

function createPane(reference, docs, onSelect) {
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
  // The pane's own edit toggle: pressed state is per-pane only.
  const editButton = element("button", {
    type: "button",
    class: "pane-toolbar-edit",
    "data-testid": "pane-toolbar-edit",
    "aria-pressed": "false",
    text: "edit",
  });
  editButton.addEventListener("click", () => {
    const pressed = editButton.getAttribute("aria-pressed") === "true";
    editButton.setAttribute("aria-pressed", pressed ? "false" : "true");
  });
  const toolbar = element("div", { class: "pane-toolbar", "data-testid": "pane-toolbar" }, [
    editButton,
  ]);
  const body = element("div", { class: "pane-body", "data-testid": "pane-body" });
  const root = element("section", {
    class: "pane",
    "data-testid": `doc-pane-${reference.ordinal}`,
  }, [
    element("header", { class: "pane-header" }, [select, toolbar]),
    body,
  ]);
  if (onSelect) {
    select.addEventListener("change", () => onSelect(reference, select.value, body));
  }
  return { root, select, toolbar, editButton, body, reference };
}
