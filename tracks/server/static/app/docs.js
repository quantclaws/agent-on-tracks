/**
 * Docs-centre view (IF-DOCCENTER-001; interfaces §1n, reworked 2026-10-05
 * per operator directive).
 *
 * The sidebar IS the document tree (one tree, never duplicated in the main
 * area). The main area opens blank on a fresh session, restores the last
 * opened document within the session, and once a document is open it gets a
 * tab carrying the file name — the split icon on the right of the tab row
 * adds an independent viewer pane beside the editor. The editor hosts the
 * vendored Vditor build on demand from the same origin in instant-rendering
 * (``ir``) mode and falls back to a textarea when the asset or
 * initialisation fails so the document stays viewable and editable.
 */

import { requestJson } from "./api.js";
import { clear, degradedMessage, element, loadInto } from "./dom.js";
import { createDiscussionNav } from "./discussions.js";
import { createEditor } from "./editor.js";
import { createPaneContainer } from "./panes.js";
import { renderSidebar } from "./sidebar.js";

export const VEDITOR_BASE = "/static/vendor/vditor/";

// The last opened document per project/version — session memory only (the
// client persists nothing; a fresh session starts blank by design).
const lastOpenDoc = new Map();

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
  link.href = `${VEDITOR_BASE}index.css`;
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
    // The docs window shows documents, not a category banner.
    const title = body
      .closest('[data-testid="main-area"]')
      ?.querySelector('[data-testid="main-title"]');
    if (title) title.textContent = "";
    const workbench = createDocsWorkbench(projectId, version);
    // The sidebar is the document tree — the one and only tree face; its
    // nodes open the document (a node is a link, so the default navigation
    // is suppressed).
    const sidebarHost = renderSidebar({
      title: version.version || "docs",
      nodes: (version.docs || []).map((doc) => ({
        label: doc.doc,
        testid: `doc-tree-item-${doc.doc}`,
      })),
    });
    if (sidebarHost) {
      for (const anchor of sidebarHost.querySelectorAll(
        '[data-testid^="doc-tree-item-"]'
      )) {
        anchor.addEventListener("click", (event) => {
          event.preventDefault();
          const docName = (anchor.getAttribute("data-testid") || "").replace(
            "doc-tree-item-",
            ""
          );
          workbench.openDoc(docName);
        });
      }
    }
    body.append(workbench.root);
    // Within the session the window reopens on the last document; a fresh
    // session starts blank until the sidebar selects one.
    const last = lastOpenDoc.get(`${projectId}/${version.version}`);
    if (last && (version.docs || []).some((doc) => doc.doc === last)) {
      await workbench.openDoc(last);
    }
  });
}

function createDocsWorkbench(projectId, version) {
  // louke Dev Docs layout: a slim toolbar (label + the split button on the
  // right) above a pane container; every pane is a full document face.
  let activePane = null;
  let discussions = null;
  const panes = createPaneContainer({
    version: version.version || "",
    docs: version.docs || [],
    load: (pane, docName) => loadDocIntoPane(pane, docName),
    activate: (pane) => {
      activePane = pane;
    },
  });
  const split = element("button", {
    type: "button",
    class: "devdocs-tool",
    "data-testid": "pane-add",
    title: "新增文档分屏",
    text: "＋ 新增分屏",
  });
  split.addEventListener("click", () => {
    const pane = panes.addPane();
    if (pane) loadDocIntoPane(pane, activeDocName());
  });
  const toolbar = element("header", { class: "devdocs-toolbar" }, [
    element("strong", { text: "Docs" }),
    element("span", { text: "多文档实时编辑工作区" }),
    element("div", { class: "devdocs-tools" }, [split]),
  ]);
  const root = element("div", {
    class: "docs-workbench",
    "data-testid": "docs-workbench",
  }, [toolbar, panes.root]);

  function activeDocName() {
    return activePane && activePane.select.value ? activePane.select.value : null;
  }

  function setPaneStatus(pane, text) {
    if (pane && pane.status) pane.status.textContent = text;
  }

  async function loadDocIntoPane(pane, docName) {
    if (!pane || !docName) return;
    activePane = pane;
    pane.select.value = docName;
    lastOpenDoc.set(`${projectId}/${version.version}`, docName);
    markSidebarCurrent(docName);
    setPaneStatus(pane, "加载中");
    let payload;
    try {
      payload = await requestJson(docUrl(projectId, version.version, docName));
    } catch (error) {
      setPaneStatus(pane, "加载失败");
      pane.body.append(
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
    clear(pane.body);
    const editor = createEditor({
      url: `/api/runs/${encodeURIComponent(
        version.editable_run_id || "unassigned"
      )}/docs/${encodeURIComponent(docName)}/edits`,
      revision: payload.revision,
      content: payload.content,
      reload: async () =>
        requestJson(docUrl(projectId, version.version, docName)).catch(() => null),
    });
    pane.body.append(editor.root);
    discussions = createDiscussionNav({ threads: [] });
    editor
      .root.querySelector('[data-testid="doc-toolbar"]')
      .append(discussions.controls);
    loadThreads(projectId, version.version, docName, discussions);
    // The pane-bar discussion navigation (louke): ↯ walks threads, ! jumps
    // unresolved-only.
    pane.root.querySelector('[data-testid="pane-next-discussion"]').onclick = () => {
      discussions.toggle();
      discussions.nextThread();
    };
    pane.root.querySelector('[data-testid="pane-next-unresolved"]').onclick = () => {
      discussions.toggle();
      // advance until the cursor lands on an unresolved thread (a full
      // cycle gives up — everything may be resolved)
      for (let step = 0; step < 32; step += 1) {
        const thread = discussions.nextThread();
        if (!thread || thread.status !== "resolved") break;
      }
    };
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
    setPaneStatus(pane, "已加载");
  }

  function openDoc(docName) {
    if (!docName) return;
    if (!activePane) {
      const pane = panes.addPane();
      if (!pane) return;
    }
    loadDocIntoPane(activePane || panes.panes[0], docName);
  }

  return { root, openDoc, panes };
}

function markSidebarCurrent(docName) {
  for (const node of document.querySelectorAll('[data-testid^="doc-tree-item-"]')) {
    node.setAttribute(
      "aria-current",
      node.getAttribute("data-testid") === `doc-tree-item-${docName}` ? "true" : "false"
    );
  }
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
