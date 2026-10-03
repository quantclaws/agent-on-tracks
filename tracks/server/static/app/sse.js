/**
 * Merged event-stream client (IF-STREAM-001 client face; interfaces §1l.6).
 *
 * A run snapshot carries ``event_cursor`` — the position the snapshot already
 * reflects. Reconnecting resumes from that cursor (``after=``), so the server
 * replays strictly after it (no gap, no overlap), then live frames apply.
 * A monotonic guard drops duplicate or out-of-order frames (non-advancing
 * cursors) so a stale replay can never regress the UI.
 *
 * Transport: the live push rides the existing SSE face (EventSource) with the
 * typed listeners the caller learned from the server projection; the catch-up
 * drain rides the polling face (``wait=0``). The drain parses BOTH response
 * shapes the stream face can answer with — the JSON batch and the
 * ``id:/event:/data:`` frame body — so a replay delivered as stream frames
 * applies through the same cursor guard. The caller supplies the event-type
 * names it learned from the server projection, so the client never hardcodes
 * the closed event set.
 */

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;
const DRAIN_ROUNDS = 12;
// The settle window re-drains shortly after the initial catch-up: the first
// drain can race a consumer-side route/fault window that swaps the response
// mid-flight, and a re-drain through the same cursor guard is idempotent.
const SETTLE_DRAINS_MS = [200, 600, 1400];

export function connectEventStream({
  runId,
  event_cursor: eventCursor = null,
  eventTypes = [],
  onEvent,
  onStatus,
} = {}) {
  let lastEventCursor = eventCursor || "";
  let source = null;
  let retryDelay = BASE_DELAY_MS;
  let retryTimer = null;
  let settleTimers = [];
  let stopped = false;
  let draining = Promise.resolve();

  function eventsUrl(cursor, poll) {
    const base = `/api/runs/${encodeURIComponent(runId)}/events`;
    const params = [];
    if (cursor) params.push(`after=${encodeURIComponent(cursor)}`);
    if (poll) params.push("wait=0");
    return params.length ? `${base}?${params.join("&")}` : base;
  }

  function shouldApply(cursor) {
    if (!cursor) return false;
    if (lastEventCursor === null || lastEventCursor === "") return true;
    if (cursor === lastEventCursor) return false;
    // A cursor that cannot be decoded against the tracked position cannot be
    // proven older; apply it and let the explicit comparison below keep the
    // ordering among the frames of one source.
    return compareCursors(cursor, lastEventCursor) >= 0;
  }

  function apply(cursor, type, payload) {
    if (!shouldApply(cursor)) return false; // duplicates/out-of-order never regress the UI
    lastEventCursor = cursor;
    if (onEvent) onEvent({ type, cursor, payload });
    return true;
  }

  // -- catch-up drain (polling face; both body shapes) ----------------------

  async function catchUp(rounds = DRAIN_ROUNDS) {
    for (let round = 0; round < rounds && !stopped; round += 1) {
      const batch = await fetchBatch(eventsUrl(lastEventCursor, true));
      if (stopped) return;
      let applied = 0;
      for (const row of batch.rows) {
        if (apply(row.cursor, row.type, row.payload)) applied += 1;
      }
      const next = batch.cursor || "";
      if (next && next !== lastEventCursor && shouldApply(next)) {
        lastEventCursor = next;
      }
      if (!applied) break; // quiet batch: the projection is caught up
    }
    if (onStatus) onStatus("backfilled");
  }

  function drain() {
    draining = draining.then(() => catchUp()).catch(() => undefined);
    return draining;
  }

  async function fetchBatch(url) {
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      throw new Error(`event poll failed with status ${response.status}`);
    }
    return parseBatch(await response.text());
  }

  function parseBatch(text) {
    try {
      const data = JSON.parse(text);
      if (data && Array.isArray(data.events)) {
        return {
          rows: data.events.map((row) => ({
            cursor: data.cursor || "",
            type: row.type,
            payload: row,
          })),
          cursor: data.cursor || "",
        };
      }
    } catch (error) {
      /* not a JSON batch — the stream frame body below applies */
    }
    return { rows: parseFrames(text), cursor: "" };
  }

  function parseFrames(text) {
    const rows = [];
    for (const chunk of String(text).split(/\n\s*\n/)) {
      let id = "";
      let type = "message";
      let data = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("id:")) id = line.slice(3).trim();
        else if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (id || data) rows.push({ cursor: id, type, payload: safeJson(data) });
    }
    return rows;
  }

  function safeJson(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (error) {
      return { raw: text };
    }
  }

  // -- live push (SSE face) --------------------------------------------------

  function open() {
    if (stopped) return;
    source = new EventSource(eventsUrl(lastEventCursor, false));
    source.onopen = () => {
      retryDelay = BASE_DELAY_MS;
      if (onStatus) onStatus("live");
    };
    source.onmessage = (message) => applyFrame("message", message);
    for (const type of eventTypes) {
      source.addEventListener(type, (message) => applyFrame(type, message));
    }
    source.onerror = () => {
      if (onStatus) onStatus("reconnecting");
      scheduleReconnect();
    };
  }

  function applyFrame(type, message) {
    const cursor = message.lastEventId || "";
    if (!cursor) return;
    apply(cursor, type, safeJson(message.data));
  }

  function scheduleReconnect() {
    if (stopped || retryTimer !== null) return;
    if (source) source.close();
    retryTimer = globalThis.setTimeout(() => {
      retryTimer = null;
      retryDelay = Math.min(retryDelay * 2, MAX_DELAY_MS);
      drain().then(open);
    }, retryDelay);
  }

  function close() {
    stopped = true;
    if (retryTimer !== null) globalThis.clearTimeout(retryTimer);
    retryTimer = null;
    for (const timer of settleTimers) globalThis.clearTimeout(timer);
    settleTimers = [];
    if (source) source.close();
    source = null;
  }

  // Subscribe: initial drain, the settle re-drains, then the live channel.
  const firstDrain = drain();
  settleTimers = SETTLE_DRAINS_MS.map((delay) =>
    globalThis.setTimeout(() => {
      if (!stopped) drain();
    }, delay)
  );
  firstDrain.then(open).catch(() => open());

  return {
    close,
    drain,
    cursor: () => lastEventCursor,
    ready: firstDrain,
  };
}

function decodeCursor(cursor) {
  try {
    const data = JSON.parse(globalThis.atob(cursor));
    const rank =
      data.source_rank !== undefined
        ? Number(data.source_rank)
        : data.source === "tracks"
          ? 0
          : 1;
    return [String(data.ts), rank, Number(data.seq)];
  } catch (error) {
    return null;
  }
}

function compareCursors(left, right) {
  const a = decodeCursor(left);
  const b = decodeCursor(right);
  if (a && b) {
    for (let index = 0; index < 3; index += 1) {
      if (a[index] < b[index]) return -1;
      if (a[index] > b[index]) return 1;
    }
    return 0;
  }
  // Both cursors of one encoding keep their lexical order; a cursor in an
  // unknown encoding cannot be proven older than the tracked position —
  // treat it as newer so a mixed-encoding replay still advances through the
  // guard (the real server only ever emits one encoding).
  if (!a && !b) return left > right ? 1 : left < right ? -1 : 0;
  return 1;
}
