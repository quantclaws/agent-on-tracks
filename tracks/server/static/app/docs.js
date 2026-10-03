/**
 * Docs-centre view (IF-DOCCENTER-001; interfaces §1n).
 *
 * The version tree drives document selection; the editor host mounts the
 * vendored Vditor build on demand from the same origin in instant-rendering
 * (``ir``) mode, and falls back to a textarea when the asset or initialisation
 * fails so the document stays viewable and editable. Panes stay independent
 * (shared container, at most four) and the discussion overlay is read-only.
 */

import { requestJson } from "./api.js";
import { clear, degradedMessage, element, loadInto } from "./dom.js";
import { createDiscussionNav } from "./discussions.js";
import { createEditor } from "./editor.js";
import { createPaneContainer } from "./panes.js";

export const VEDITOR_BASE = "/static/vendor/vditor/";

let vditorRequest = null;

export function loadVditor() {
  if (globalThis.Vditor) return Promise.resolve(globalThis.Vditor);
  if (!vditorRequest) {
    vditorRequest = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `${VEDITOR_BASE}index.min.js`;
      script.async = true;
      script.onload = () => {
        if (globalThis.Vditor) resolve(globalThis.Vditor);
        else reject(new Error("Vditor did not initialise"));
      };
      script.onerror = () => reject(new Error("Vditor asset unavailable"));
      document.head.append(script);
    });
  }
  return vditorRequest;
}

export function mountTextarea(host, content) {
  const area = element("textarea", {
    class: "doc-textarea",
    "data-testid": "doc-textarea",
  });
  area.value = content;
  clear(host);
  host.append(area);
  return area;
}

export async function mountDocEditor(host, content, onChange) {
  try {
    const Vditor = await loadVditor();
    return new Vditor(host, {
      mode: "ir",
      value: content,
      input: (value) => {
        if (onChange) onChange(value);
      },
    });
  } catch (error) {
    // Asset 404 / script error / init failure: keep the document usable.
    const area = mountTextarea(host, content);
    area.addEventListener("input", () => {
      if (onChange) onChange(area.value);
    });
    return area;
  }
}

export function renderDocsView(body, context = {}) {
  return loadInto(body, async () => {
    const projectId = context.projectId;
    if (!projectId) {
      body.append(degradedMessage("Unknown project"));
      return;
    }
    const tree = await requestJson(
      `/api/projects/${encodeURIComponent(projectId)}/docs/tree`
    );
    const versions = Array.isArray(tree.versions) ? tree.versions : [];
    const first = versions[0];
    if (!first) {
      body.append(degradedMessage("No documents"));
      return;
    }
    const workbench = createWorkbench(projectId, first);
    body.append(workbench.root);
    await workbench.open(first, (first.docs || [])[0]);
  });
}

function createWorkbench(projectId, version) {
  const treeHost = element("nav", { class: "doc-tree", "data-testid": "doc-tree" });
  const editorHost = element("div", {
    class: "doc-editor-host",
    "data-testid": "doc-editor-host",
  });
  const panes = createPaneContainer({
    version: version ? version.version : "",
    docs: version ? version.docs || [] : [],
    onSelect: (reference, docName, paneBody) =>
      loadPaneDocument(projectId, reference, docName, paneBody),
  });
  const root = element("div", { class: "docs-workbench", "data-testid": "docs-workbench" }, [
    treeHost,
    editorHost,
    panes.root,
  ]);
  let active = null;

  async function open(version, docRef) {
    const docName = docRef ? docRef.doc || docRef : "";
    if (!version || !docName) {
      editorHost.append(degradedMessage("Unknown document"));
      return;
    }
    active = { version, docName };
    renderTree(treeHost, version, docName, open);
    const payload = await readDocument(projectId, version.version, docName);
    clear(editorHost);
    const url = docUrl(projectId, version.version, docName);
    const editor = createEditor({
      url: `${url}/edits`,
      revision: payload.revision,
      content: payload.content,
      reload: () => readDocument(projectId, version.version, docName),
    });
    editorHost.append(editor.root);
    await mountDocEditor(editor.surface, payload.content, (value) =>
      editor.setContent(value)
    );
    const discussions = createDiscussionNav({ threads: [] });
    editorHost.append(discussions.root);
    await loadThreads(projectId, version.version, docName, discussions);
  }

  return { root, open, panes, active: () => active };
}

function renderTree(treeHost, version, activeDoc, open) {
  clear(treeHost);
  for (const doc of version.docs || []) {
    const item = element("button", {
      type: "button",
      class: "doc-item",
      "data-testid": `doc-item-${doc.doc}`,
      "aria-current": doc.doc === activeDoc ? "true" : "false",
      text: doc.doc,
    });
    item.addEventListener("click", () => open(version, doc));
    treeHost.append(item);
  }
}

async function readDocument(projectId, version, docName) {
  return requestJson(docUrl(projectId, version, docName));
}

function docUrl(projectId, version, docName) {
  return `/api/projects/${encodeURIComponent(projectId)}/docs/${encodeURIComponent(
    version
  )}/${encodeURIComponent(docName)}`;
}

async function loadThreads(projectId, version, docName, discussions) {
  try {
    const payload = await requestJson(`${docUrl(projectId, version, docName)}/discussions`);
    discussions.setThreads(Array.isArray(payload.threads) ? payload.threads : []);
  } catch (error) {
    discussions.setThreads([]);
  }
}

async function loadPaneDocument(projectId, reference, docName, paneBody) {
  const version = reference && reference.version ? reference.version : "";
  if (!version || !docName) return;
  const payload = await readDocument(projectId, version, docName);
  mountTextarea(paneBody, payload.content);
}