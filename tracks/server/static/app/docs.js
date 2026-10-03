/**
 * Docs-centre view (IF-DOCCENTER-001; interfaces §1n).
 *
 * The version tree drives document selection; the editor hosts the vendored
 * Vditor build on demand from the same origin in instant-rendering (``ir``)
 * mode, and falls back to a textarea when the asset or initialisation fails
 * so the document stays viewable and editable. The authoritative editable
 * draft is the markdown source area (``doc-editor-content``); panes stay
 * independent (shared container, at most four) and the discussion overlay is
 * read-only navigation only.
 */

import { requestJson } from "./api.js";
import { clear, degradedMessage, element, loadInto } from "./dom.js";
import { createDiscussionNav } from "./discussions.js";
import { createEditor } from "./editor.js";
import { createPaneContainer } from "./panes.js";
import { renderSidebar } from "./sidebar.js";

export const VEDITOR_BASE = "/static/vendor/vditor/";

let vditorRequest = null;

export function loadVditor() {
  if (globalThis.Vditor) return Promise.resolve(globalThis.Vditor);
  if (!vditorRequest) {
    vditorRequest = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `${VEDITOR_BASE}dist/index.min.js`;
      script.async = true;
      script.onload = () => {
        if (globalThis.Vditor) resolve(globalThis.Vditor);
        else reject(new Error("Vditor did not initialise"));
      };
      script.onerror = () => reject(new Error("Vditor asset unavailable"));
      document.head.append(script);
    });
    vditorRequest.catch(() => {
      vditorRequest = null; // a failed load may be retried later
    });
  }
  return vditorRequest;
}

function vditorStylesheet() {
  if (document.querySelector("link[data-vditor-styles]")) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `${VEDITOR_BASE}dist/index.css`;
  link.setAttribute("data-vditor-styles", "true");
  document.head.append(link);
}

export function mountTextarea(host, content) {
  const area = element("textarea", {
    class: "doc-textarea",
    "data-testid": "doc-editor-fallback",
    rows: 10,
  });
  area.value = content;
  clear(host);
  host.append(area);
  return area;
}

export async function mountDocEditor(host, content, handlers = {}) {
  // Same-origin vendor assets only (§1n.3): the vendored build keeps the npm
  // ``dist`` layout under the vendor root, so the cdn base is the vendor path
  // itself and Vditor's internal ``/dist/`` asset URLs resolve onto it.
  vditorStylesheet();
  const Vditor = await loadVditor();
  const mount = element("div", { class: "vditor-mount" });
  clear(host);
  host.append(mount);
  let instance = null;
  let initialized = false;
  instance = new Vditor(mount, {
    mode: "ir",
    cdn: VEDITOR_BASE.replace(/\/$/, ""),
    lang: "en_US",
    value: content,
    // no draft persistence: the editor state lives in memory only (§1l.4)
    cache: { enable: false },
    input: (value) => {
      // ignore the mount-time internal events: they precede the init
      // callback and must never clobber a draft the user already typed.
      if (!initialized) return;
      if (handlers.input) handlers.input(value);
    },
    after: () => {
      initialized = true;
      if (handlers.after) handlers.after(instance);
    },
  });
  return instance;
}

export function renderDocsView(body, context = {}) {
  return loadInto(body, async () => {
    let projectId = context.projectId || null;
    if (!projectId) {
      const projects = await requestJson("/api/projects").catch(() => null);
      const rows = projects
        ? Array.isArray(projects)
          ? projects
          : projects.projects || []
        : [];
      if (rows.length) projectId = rows[0].project_id;
    }
    if (!projectId) projectId = "host";
    const tree = await requestJson(
      `/api/projects/${encodeURIComponent(projectId)}/docs/tree`
    );
    const versions = Array.isArray(tree.versions) ? tree.versions : [];
    const version = versions[0];
    if (!version || !(version.docs || []).length) {
      renderSidebar({ title: "docs centre", nodes: [] });
      body.append(degradedMessage("No documents"));
      return;
    }
    renderSidebar({
      title: version.version || "docs",
      nodes: (version.docs || []).map((doc) => ({
        label: doc.doc,
        testid: `sidebar-doc-${doc.doc}`,
      })),
    });
    const workbench = createDocsWorkbench(projectId, version);
    body.append(workbench.root);
  });
}

function createDocsWorkbench(projectId, version) {
  const treeHost = element("nav", {
    class: "doc-tree",
    "data-testid": "doc-tree",
    "aria-label": "Document tree",
  });
  treeHost.append(
    element("h3", { class: "doc-tree-version", text: version.version || "unknown" })
  );
  const editorHost = element("div", {
    class: "doc-editor-host",
    "data-testid": "doc-editor-host",
  });
  editorHost.append(
    element("p", {
      class: "doc-hint",
      "data-testid": "doc-hint",
      text: "Select a document from the tree.",
    })
  );
  const panes = createPaneContainer({
    version: version.version || "",
    docs: version.docs || [],
    onSelect: (reference, docName, paneBody) => {
      loadPaneDocument(projectId, version.version, docName, paneBody);
    },
  });
  const root = element("div", {
    class: "docs-workbench",
    "data-testid": "docs-workbench",
  }, [treeHost, editorHost, panes.root]);
  let discussions = null;

  for (const doc of version.docs || []) {
    const item = element("button", {
      type: "button",
      class: "doc-item",
      "data-testid": `doc-tree-item-${doc.doc}`,
      text: doc.doc,
    });
    item.addEventListener("click", () => openDoc(doc.doc));
    treeHost.append(item);
  }

  async function openDoc(docName) {
    if (!docName) {
      editorHost.append(degradedMessage("Unknown document"));
      return;
    }
    let payload;
    try {
      payload = await requestJson(docUrl(projectId, version.version, docName));
    } catch (error) {
      clear(editorHost);
      editorHost.append(
        error && error.status === 404
          ? degradedMessage(`Unknown document: ${docName}`)
          : element("p", {
              class: "request-error",
              "data-testid": "api-failure",
              role: "alert",
              text: `The document read failed: ${
                error && error.message ? error.message : error
              }`,
            })
      );
      return;
    }
    for (const item of treeHost.querySelectorAll(".doc-item")) {
      item.setAttribute(
        "aria-current",
        item.getAttribute("data-testid") === `doc-tree-item-${docName}` ? "true" : "false"
      );
    }
    clear(editorHost);
    const editor = createEditor({
      url: `/api/runs/${encodeURIComponent(
        version.editable_run_id || "unassigned"
      )}/docs/${encodeURIComponent(docName)}/edits`,
      revision: payload.revision,
      content: payload.content,
      reload: async () =>
        requestJson(docUrl(projectId, version.version, docName)).catch(() => null),
    });
    editorHost.append(editor.root);
    discussions = createDiscussionNav({ threads: [] });
    editor
      .root.querySelector('[data-testid="doc-toolbar"]')
      .append(discussions.controls);
    loadThreads(projectId, version.version, docName, discussions);
    // The ir host renders live: its markdown serialization is the editable
    // document (the mount-time adoption is what makes a freshly opened,
    // normalised document savable). Any asset/init failure falls back to the
    // editable textarea without losing content (§1n.3).
    mountDocEditor(editor.irHost, payload.content, {
      input: (value) => editor.hostDraft(value),
      after: (instance) => {
        try {
          editor.adoptHostValue(instance.getValue());
        } catch (error) {
          /* the host keeps whatever draft it already holds */
        }
      },
    }).catch(() => {
      const area = mountTextarea(editor.irHost, payload.content);
      area.addEventListener("input", () => {
        editor.hostDraft(area.value);
      });
    });
  }

  return { root, openDoc, panes };
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

async function loadPaneDocument(projectId, version, docName, paneBody) {
  if (!docName) return;
  try {
    const payload = await requestJson(docUrl(projectId, version, docName));
    const view = element("pre", { class: "pane-doc", text: payload.content || "" });
    clear(paneBody);
    paneBody.append(view);
  } catch (error) {
    clear(paneBody);
    paneBody.append(degradedMessage(`Unknown document: ${docName}`));
  }
}
