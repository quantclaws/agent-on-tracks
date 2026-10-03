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
    const pane = createPane({ version }, docs, onSelect);
    panes.push(pane);
    root.append(pane.root);
    return pane;
  }

  return { root, panes, addPane };
}

function createPane(reference, docs, onSelect) {
  const select = element("select", {
    class: "pane-select",
    "data-testid": "pane-select",
  });
  for (const doc of docs) {
    const name = doc && doc.doc ? doc.doc : doc;
    select.append(element("option", { value: name, text: name }));
  }
  const toolbar = element("div", { class: "pane-toolbar", "data-testid": "pane-toolbar" });
  const body = element("div", { class: "pane-body", "data-testid": "pane-body" });
  const root = element("section", { class: "pane", "data-testid": "doc-pane" }, [
    element("header", { class: "pane-header" }, [select, toolbar]),
    body,
  ]);
  if (onSelect) {
    select.addEventListener("change", () => onSelect(reference, select.value, body));
  }
  return { root, select, toolbar, body, reference };
}