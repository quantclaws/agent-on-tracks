/**
 * Docs-centre view (IF-DOCCENTER-001; interfaces §1n, reworked 2026-10-05
 * per operator directive; multi-version tree + banner removal 2026-10-07
 * per operator directive, #221/#222).
 *
 * The sidebar IS the whole version tree (louke Dev Docs model): one
 * collapsible group per version directory, newest first, group label
 * carrying the story title when present. The main area opens blank on a
 * fresh session, restores the last opened document (any version) within the
 * session, and every pane is a full document face — the pane bars are the
 * only control surface (no workbench-level banner). The editor hosts the
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

// The last opened document per project — {version, doc} session memory only
// (the client persists nothing; a fresh session starts blank by design).
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
    const all = Array.isArray(tree.versions) ? tree.versions : [];
    const versions = all.filter((version) => (version.docs || []).length);
    if (!versions.length) {
      renderSidebar({ title: "docs centre", nodes: [] });
      body.append(degradedMessage("No documents"));
      return;
    }
    // The docs window shows documents, not a category banner.
    const title = body
      .closest('[data-testid="main-area"]')
      ?.querySelector('[data-testid="main-title"]');
    if (title) title.textContent = "";
    const workbench = createDocsWorkbench(projectId, versions);
    // The sidebar is the whole version tree (louke Dev Docs model, #221): one
    // collapsible group per version directory, newest first; a group node
    // opens that version's document (the default navigation is suppressed).
    const sidebarHost = renderSidebar({
      title: "docs",
      groups: versions.map((version) => ({
        label: version.title
          ? `${version.version} · ${version.title}`
          : version.version || "unknown",
        testid: `doc-tree-version-${version.version}`,
        children: (version.docs || []).map((doc) => ({
          label: doc.doc,
          testid: `doc-tree-item-${version.version}-${doc.doc}`,
          attrs: { "data-version": version.version, "data-doc": doc.doc },
        })),
      })),
    });
    // The version and doc travel on data attributes — version names contain
    // hyphens (v0.9-hotfix-7), so the testid itself must never be parsed.
    if (sidebarHost) {
      for (const anchor of sidebarHost.querySelectorAll(
        '[data-testid^="doc-tree-item-"]'
      )) {
        anchor.addEventListener("click", (event) => {
          event.preventDefault();
          const version = anchor.getAttribute("data-version");
          const docName = anchor.getAttribute("data-doc");
          if (version && docName) workbench.openDoc(docName, version);
        });
      }
    }
    body.append(workbench.root);
    // Within the session the window reopens on the last document (any
    // version); a fresh session starts blank until the sidebar selects one.
    const last = lastOpenDoc.get(projectId);
    if (
      last &&
      versions.some(
        (version) =>
          version.version === last.version &&
          (version.docs || []).some((doc) => doc.doc === last.doc)
      )
    ) {
      await workbench.openDoc(last.doc, last.version);
    }
  });
}

function createDocsWorkbench(projectId, versions) {
  // louke Dev Docs layout (#221/#222): no workbench-level banner — the pane
  // bars are the only control surface; every pane is a full document face and
  // carries the document selector for the version it currently shows.
  let activePane = null;
  let discussions = null;
  const docsByVersion = new Map(
    versions.map((version) => [version.version, version])
  );
  // The panes.js contract is onLoad/onActivate/onSplit — the historical
  // load:/activate: keys never matched the destructured parameter names, so
  // the selector-change and pane-activation callbacks silently never fired.
  const panes = createPaneContainer({
    version: "",
    docs: [],
    onLoad: (pane, docName) =>
      loadDocIntoPane(pane, docName, pane.currentVersion),
    onActivate: (pane) => {
      activePane = pane;
    },
    // a split from any pane bar opens the active pane's document in the new
    // pane (the pane is never born empty)
    onSplit: (pane) => {
      if (activePane && activePane.currentVersion) {
        loadDocIntoPane(pane, activeDocName(), activePane.currentVersion);
      }
    },
  });
  const root = element("div", {
    class: "docs-workbench",
    "data-testid": "docs-workbench",
  }, [panes.root]);

  function activeDocName() {
    return activePane && activePane.select.value ? activePane.select.value : null;
  }

  function setPaneOptions(pane, version) {
    const model = docsByVersion.get(version);
    pane.select.textContent = "";
    for (const doc of (model && model.docs) || []) {
      pane.select.append(element("option", { value: doc.doc, text: doc.doc }));
    }
    if (!pane.select.options.length) {
      pane.select.append(element("option", { value: "", text: "no documents" }));
    }
  }

  function setPaneStatus(pane, text) {
    if (pane && pane.status) pane.status.textContent = text;
  }

  async function loadDocIntoPane(pane, docName, version) {
    const model = docsByVersion.get(version);
    if (!pane || !docName || !model) return;
    activePane = pane;
    pane.currentVersion = version;
    setPaneOptions(pane, version);
    pane.select.value = docName;
    lastOpenDoc.set(projectId, { version, doc: docName });
    markSidebarCurrent(version, docName);
    setPaneStatus(pane, "加载中");
    let payload;
    try {
      payload = await requestJson(docUrl(projectId, version, docName));
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
        model.editable_run_id || "unassigned"
      )}/docs/${encodeURIComponent(docName)}/edits`,
      revision: payload.revision,
      content: payload.content,
      reload: async () =>
        requestJson(docUrl(projectId, version, docName)).catch(() => null),
    });
    pane.body.append(editor.root);
    discussions = createDiscussionNav({ threads: [] });
    editor
      .root.querySelector('[data-testid="doc-toolbar"]')
      .append(discussions.controls);
    loadThreads(projectId, version, docName, discussions);
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

  function openDoc(docName, version) {
    if (!docName || !docsByVersion.has(version)) return;
    if (!activePane) {
      const pane = panes.addPane();
      if (!pane) return;
    }
    loadDocIntoPane(activePane || panes.panes[0], docName, version);
  }

  return { root, openDoc, panes };
}

function markSidebarCurrent(version, docName) {
  for (const node of document.querySelectorAll('[data-testid^="doc-tree-item-"]')) {
    node.setAttribute(
      "aria-current",
      node.getAttribute("data-version") === version &&
        node.getAttribute("data-doc") === docName
        ? "true"
        : "false"
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
