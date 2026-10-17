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
import { createCommentComposer, createDiscussionNav, isReviewRound } from "./discussions.js";
import { createEditor } from "./editor.js";
import { createPaneContainer } from "./panes.js";
import { renderSidebar } from "./sidebar.js";

export const VEDITOR_BASE = "/static/vendor/vditor/";

// The last opened document per project — {version, doc} session memory only
// (the client persists nothing; a fresh session starts blank by design).
const lastOpenDoc = new Map();

// §1n.4: run details fetched to gate the review-round write controls (the
// awaiting_gate projection is the single gate source; a run detail without
// it simply answers no controls). Cached per editable run for the session.
const runDetailCache = new Map();

async function reviewRoundRunDetail(runId) {
  if (!runId) return null;
  if (runDetailCache.has(runId)) return runDetailCache.get(runId);
  let detail = null;
  try {
    detail = await requestJson(`/api/runs/${encodeURIComponent(runId)}`);
  } catch (error) {
    detail = null; // the gate degrades to "no write controls", never to an error face
  }
  runDetailCache.set(runId, detail);
  return detail;
}

/**
 * The review-round comment control (§1n.3/§1n.4): mounted ONLY when the
 * document's editable run sits at a review-round gate; every other context
 * (and every unresolvable run detail) keeps the pane free of write controls
 * beyond the existing editor save face.
 */
async function mountReviewComposer(pane, editor, model, docName, reloadThreads) {
  pane.root.querySelectorAll('[data-testid="review-comment-bar"]').forEach((node) => node.remove());
  if (!model || !model.editable_run_id) return;
  const detail = await reviewRoundRunDetail(model.editable_run_id);
  if (!detail || !isReviewRound(detail)) return;
  const composer = createCommentComposer({
    doc: docName,
    runId: model.editable_run_id,
    anchorLineOf: editor.selectionLineText,
    onLanded: reloadThreads,
  });
  pane.body.append(composer.root);
}

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
    // The docs window shows documents, not a category banner (the main
    // header strip is gone entirely — 2026-10-09 operator directive).
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
    // tabs.js asks this hook before unmounting the docs window: a pending
    // decision or unsaveable draft on any pane keeps the whole view mounted.
    body.__beforeUnmount = workbench.prepareUnmount;
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
  // onClose guards the close path with the same release rules as a doc
  // switch (Prism round-2: no teardown path may drop a decision face).
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
    onClose: (pane) => releasePane(pane),
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

  /**
   * The single release guard behind every teardown path (doc switch inside
   * a pane, pane close, window switch away from docs): an unresolved
   * conflict or an unsaveable draft keeps its visible decision face — the
   * editor stays mounted and the caller is told to stand down. A released
   * pane's pending autosave flushes now, so an edit never rides on a timer
   * that is about to lose its surface.
   */
  async function releasePane(pane) {
    const editor = pane && pane.editor;
    if (!editor) return true;
    if (editor.conflictOpen && editor.conflictOpen()) {
      setPaneStatus(pane, "有未处理的冲突——请先在编辑器下方选择“重载服务端版本 / 用本地修改覆盖”（二选一）");
      return false;
    }
    const flush = editor.destroy ? await editor.destroy() : { flushed: false };
    if (!flush) {
      setPaneStatus(pane, "保存未完成——请重试或处理后再操作");
      return false;
    }
    return true;
  }

  async function loadDocIntoPane(pane, docName, version) {
    const model = docsByVersion.get(version);
    if (!pane || !docName || !model) return;
    // The outgoing document must be safely on the server before this pane
    // switches away — an unresolved conflict (its panel is alive on the
    // mounted editor) or a failed flush blocks the switch so the decision
    // face is never torn down behind the user's back.
    if (pane.editor && !(await releasePane(pane))) return;
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
      // the pane's control face belongs to no document now — the outgoing
      // editor's save control must not survive a failed load, and the old
      // document's body must not read as the requested one (Prism R2-A4)
      pane.editor = null;
      if (pane.editorSlot) pane.editorSlot.replaceChildren();
      clear(pane.body);
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
    pane.editor = editor;
    // The save control joins the pane bar (merge directive 2026-10-09): the
    // editor renders no toolbar row of its own.
    if (pane.editorSlot) pane.editorSlot.replaceChildren(editor.saveControl);
    pane.body.append(editor.root);
    // Discussion navigation lands IN the document: the overlay's Next and
    // the pane-bar ↯/! controls scroll to the thread's anchor and flash it.
    const navigateToThread = (entryLine, thread) =>
      focusThreadAnchor(pane, thread, entryLine);
    discussions = createDiscussionNav({ threads: [], onNavigate: navigateToThread });
    const reloadThreads = () =>
      loadThreads(projectId, version, docName, discussions).catch(() => null);
    discussions.setReplyContext({
      runId: model.editable_run_id || null,
      doc: docName,
      onReload: reloadThreads,
    });
    reloadThreads();
    // The pane-bar discussion navigation (louke): ☰ opens the thread list,
    // ↯ walks threads, ! jumps unresolved-only.
    pane.root.querySelector('[data-testid="discussion-toggle"]').onclick = () => {
      discussions.toggle();
    };
    pane.root.querySelector('[data-testid="pane-next-discussion"]').onclick = () => {
      if (!discussions.isOpen()) discussions.toggle();
      discussions.nextThread();
    };
    pane.root.querySelector('[data-testid="pane-next-unresolved"]').onclick = () => {
      if (!discussions.isOpen()) discussions.toggle();
      discussions.nextUnresolved();
    };
    // The ir host renders the body live: its markdown serialization is the
    // editable document. Any asset/init failure falls back to the editable
    // textarea without losing content (§1n.3).
    mountDocEditor(editor.irHost, editor.draft.content, {
      input: (value) => editor.hostDraft(value),
    }).catch(() => {
      const area = mountTextarea(editor.irHost, editor.draft.content);
      area.addEventListener("input", () => {
        editor.hostDraft(area.value);
      });
    });
    // §1n.4: the inline-comment control mounts only inside a review-round
    // gate (awaiting_gate.type ∈ {review, review_pending_threads, triage}).
    mountReviewComposer(pane, editor, model, docName, reloadThreads);
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

  /**
   * Window-switch guard (tabs.js asks before unmounting this view): every
   * pane releases first; a pending decision or unsaveable draft on ANY pane
   * refuses the unmount so its face stays visible.
   */
  async function prepareUnmount() {
    let ok = true;
    for (const pane of panes.panes) {
      if (!(await releasePane(pane))) ok = false;
    }
    return ok;
  }

  return { root, openDoc, panes, prepareUnmount };
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

/**
 * Discussion navigation landing (the ``onNavigate`` face): scroll the
 * document to the thread's anchor and flash it. The thread projection
 * carries ``summary`` (the root body prefix) as the content anchor plus
 * ``entry_line`` (the 1-indexed root line) for the plain-text fallback.
 * Every path is fail-closed silent — a missed anchor never throws.
 */
export function focusThreadAnchor(pane, thread, entryLine) {
  try {
    if (!pane || !pane.body || !thread) return false;
    const needle = threadAnchorText(thread);
    const host = pane.body.querySelector('[data-testid="vditor-ir-host"]');
    if (host && needle) {
      const hit = findBlockWithText(host, needle);
      if (hit) {
        hit.scrollIntoView({ block: "center" });
        hit.classList.add("thread-flash");
        globalThis.setTimeout(() => hit.classList.remove("thread-flash"), 1500);
        return true;
      }
    }
    // Textarea fallback (the source area or the no-vditor fallback): select
    // the anchor line so the caret — and the scroll — land on the thread.
    const area = pane.body.querySelector(
      '[data-testid="doc-editor-content"], [data-testid="doc-editor-fallback"]'
    );
    if (area && typeof area.value === "string") {
      const lines = area.value.split("\n");
      const lineNo = anchorLineNo(lines, needle, entryLine);
      if (lineNo !== null) {
        const start = lines.slice(0, lineNo).join("\n").length + (lineNo > 0 ? 1 : 0);
        const end = start + lines[lineNo].length;
        if (typeof area.setSelectionRange === "function") {
          area.setSelectionRange(start, Math.max(start, end));
        }
        if (typeof area.focus === "function") area.focus({ preventScroll: true });
        if (area.value.length) {
          area.scrollTop = Math.floor((area.scrollHeight * start) / area.value.length);
        }
        return true;
      }
    }
  } catch (error) {
    // locate hint only — the thread list stays usable without the landing
  }
  return false;
}

function threadAnchorText(thread) {
  for (const key of ["summary", "anchor_text", "root_text"]) {
    const value = thread ? thread[key] : null;
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function collapseWhitespace(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

// The smallest block containing the anchor text (the deepest match wins —
// an ancestor's textContent always contains its children's, so the shortest
// containing element is the tightest highlight target). Guarded against
// pathological DOM sizes; null when nothing contains the anchor.
function findBlockWithText(host, needle) {
  const target = collapseWhitespace(needle).slice(0, 40);
  if (!target || !host) return null;
  let best = null;
  let bestLen = Infinity;
  let seen = 0;
  const walker = document.createTreeWalker(host, NodeFilter.SHOW_ELEMENT);
  let node = walker.nextNode();
  while (node && seen < 20000) {
    seen += 1;
    const text = collapseWhitespace(node.textContent || "");
    if (text && text.includes(target) && text.length < bestLen) {
      best = node;
      bestLen = text.length;
    }
    node = walker.nextNode();
  }
  return best;
}

// The 0-indexed line to select: first line containing the anchor text, else
// the projected entry_line (1-indexed root line), else null.
function anchorLineNo(lines, needle, entryLine) {
  if (needle) {
    const target = collapseWhitespace(needle).slice(0, 40);
    for (let index = 0; index < lines.length; index += 1) {
      if (target && collapseWhitespace(lines[index]).includes(target)) return index;
    }
  }
  const line = Number(entryLine);
  if (Number.isInteger(line) && line >= 1 && line <= lines.length) return line - 1;
  return null;
}
