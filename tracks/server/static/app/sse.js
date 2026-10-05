/**
 * Merged event-stream client (IF-STREAM-001 client face; interfaces §1l.6).
 *
 * A run snapshot carries ``event_cursor`` — the position the snapshot already
 * reflects. Reconnecting resumes from that cursor (``after=``), so the server
 * replays strictly after it (no gap, no overlap), then live frames apply.
 * A monotonic guard drops duplicate or out-of-order frames (non-advancing
 * cursors) so a stale replay can never regress the UI.
 *
 * Transport: one shared channel per run — every subscriber watching the same
 * run (its overview row, its timeline detail) rides ONE connection with ONE
 * cursor, fanned out to all of them; the last subscriber closing releases the
 * channel. Live channels are capped (``MAX_LIVE_CHANNELS``): a browser holds
 * only a handful of concurrent same-host connections, so unbounded per-run
 * streams starve every view's data fetches. A channel beyond the cap degrades
 * to slow drains instead of holding a connection. The catch-up drain rides
 * the polling face (``wait=0``) and parses BOTH response shapes the stream
 * face can answer with — the JSON batch and the ``id:/event:/data:`` frame
 * body. A channel's cursor position is set by its first subscriber; a
 * subscriber joining an existing channel does not replay history (faces
 * initialize from snapshots).
 */

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;
const DRAIN_ROUNDS = 12;
// The settle window re-drains shortly after the initial catch-up: the first
// drain can race a consumer-side route/fault window that swaps the response
// mid-flight, and a re-drain through the same cursor guard is idempotent.
const SETTLE_DRAINS_MS = [200, 600, 1400];
// Live-stream budget: keeps free same-host connection slots for view data
// fetches (a browser allows ~6 concurrent HTTP/1.1 connections per host).
const MAX_LIVE_CHANNELS = 4;
// A channel beyond the live budget degrades to a slow drain cadence.
const DEGRADED_POLL_MS = 30000;
// A poll that never answers must fail (and reconnect), never hang.
const FETCH_DEADLINE_MS = 10000;

const channels = new Map();

export function connectEventStream({
  runId,
  event_cursor: eventCursor = null,
  eventTypes = [],
  onEvent,
  onStatus,
} = {}) {
  const key = String(runId);
  let channel = channels.get(key);
  if (!channel) {
    channel = createChannel(key, eventCursor);
    channels.set(key, channel);
  }
  const subscriber = { eventTypes, onEvent, onStatus };
  channel.subscribers.add(subscriber);
  syncListeners(channel);
  let closed = false;
  return {
    close() {
      if (closed) return;
      closed = true;
      channel.subscribers.delete(subscriber);
      if (!channel.subscribers.size) teardownChannel(channel);
    },
    drain: () => channel.drain(),
    cursor: () => channel.lastEventCursor,
    ready: channel.ready,
  };
}

function createChannel(key, seedCursor) {
  const channel = {
    key,
    subscribers: new Set(),
    lastEventCursor: seedCursor || "",
    source: null,
    listened: new Set(),
    liveSlot: false,
    retryDelay: BASE_DELAY_MS,
    retryTimer: null,
    settleTimers: [],
    degradedTimer: null,
    stopped: false,
    draining: Promise.resolve(),
  };
  channel.drain = () => {
    channel.draining = channel.draining.then(() => catchUp(channel)).catch(() => undefined);
    return channel.draining;
  };
  channel.emit = (delivered) => {
    for (const subscriber of channel.subscribers) {
      if (subscriber.onEvent) subscriber.onEvent(delivered);
    }
  };
  channel.status = (state) => {
    for (const subscriber of channel.subscribers) {
      if (subscriber.onStatus) subscriber.onStatus(state);
    }
  };
  const firstDrain = channel.drain();
  channel.ready = firstDrain;
  channel.settleTimers = SETTLE_DRAINS_MS.map((delay) =>
    globalThis.setTimeout(() => {
      if (!channel.stopped) channel.drain();
    }, delay)
  );
  firstDrain.then(() => openChannel(channel)).catch(() => openChannel(channel));
  return channel;
}

function teardownChannel(channel) {
  channel.stopped = true;
  if (channel.retryTimer !== null) globalThis.clearTimeout(channel.retryTimer);
  channel.retryTimer = null;
  for (const timer of channel.settleTimers) globalThis.clearTimeout(timer);
  channel.settleTimers = [];
  if (channel.degradedTimer !== null) globalThis.clearTimeout(channel.degradedTimer);
  channel.degradedTimer = null;
  if (channel.source) channel.source.close();
  channel.source = null;
  channels.delete(channel.key);
  if (channel.liveSlot) {
    channel.liveSlot = false;
    promoteDegradedChannel();
  }
}

// -- catch-up drain (polling face; both body shapes) ----------------------

async function catchUp(channel, rounds = DRAIN_ROUNDS) {
  for (let round = 0; round < rounds && !channel.stopped; round += 1) {
    const batch = await fetchBatch(eventsUrl(channel.key, channel.lastEventCursor, true));
    if (channel.stopped) return;
    let applied = 0;
    for (const row of batch.rows) {
      if (apply(channel, row.cursor, row.type, row.payload)) applied += 1;
    }
    const next = batch.cursor || "";
    if (next && next !== channel.lastEventCursor && shouldApply(channel, next)) {
      channel.lastEventCursor = next;
    }
    if (!applied) break; // quiet batch: the projection is caught up
  }
  channel.status("backfilled");
}

async function fetchBatch(url) {
  const controller = new AbortController();
  const timer = globalThis.setTimeout(() => controller.abort(), FETCH_DEADLINE_MS);
  let response;
  try {
    response = await fetch(url, {
      credentials: "same-origin",
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("event poll timed out without a response");
    }
    throw error;
  } finally {
    globalThis.clearTimeout(timer);
  }
  if (!response.ok) {
    throw new Error(`event poll failed with status ${response.status}`);
  }
  return parseBatch(await response.text());
}

function eventsUrl(key, cursor, poll) {
  const base = `/api/runs/${encodeURIComponent(key)}/events`;
  const params = [];
  if (cursor) params.push(`after=${encodeURIComponent(cursor)}`);
  if (poll) params.push("wait=0");
  return params.length ? `${base}?${params.join("&")}` : base;
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

// -- cursor guard (duplicates/out-of-order never regress the UI) -----------

function shouldApply(channel, cursor) {
  if (!cursor) return false;
  if (channel.lastEventCursor === null || channel.lastEventCursor === "") return true;
  if (cursor === channel.lastEventCursor) return false;
  // A cursor that cannot be decoded against the tracked position cannot be
  // proven older; apply it and let the explicit comparison below keep the
  // ordering among the frames of one source.
  return compareCursors(cursor, channel.lastEventCursor) >= 0;
}

function apply(channel, cursor, type, payload) {
  if (!shouldApply(channel, cursor)) return false;
  channel.lastEventCursor = cursor;
  channel.emit({ type, cursor, payload });
  return true;
}

// -- live push (SSE face) --------------------------------------------------

function openChannel(channel) {
  if (channel.stopped) return;
  if (!acquireLiveSlot(channel)) {
    scheduleDegradedPoll(channel);
    return;
  }
  channel.source = new EventSource(eventsUrl(channel.key, channel.lastEventCursor, false));
  channel.listened = new Set();
  channel.source.onopen = () => {
    channel.retryDelay = BASE_DELAY_MS;
    channel.status("live");
  };
  channel.source.onmessage = (message) => applyFrame(channel, "message", message);
  syncListeners(channel);
  channel.source.onerror = () => {
    channel.status("reconnecting");
    scheduleReconnect(channel);
  };
}

// Typed listeners are additive: every applied frame fans out to all
// subscribers regardless of type, so a stale type listener is harmless.
function syncListeners(channel) {
  if (!channel.source) return;
  for (const subscriber of channel.subscribers) {
    for (const type of subscriber.eventTypes || []) {
      if (channel.listened.has(type)) continue;
      channel.listened.add(type);
      channel.source.addEventListener(type, (message) =>
        applyFrame(channel, type, message)
      );
    }
  }
}

function applyFrame(channel, type, message) {
  const cursor = message.lastEventId || "";
  if (!cursor) return;
  apply(channel, cursor, type, safeJson(message.data));
}

function acquireLiveSlot(channel) {
  if (channel.liveSlot) return true;
  let live = 0;
  for (const other of channels.values()) {
    if (other.liveSlot) live += 1;
  }
  if (live >= MAX_LIVE_CHANNELS) return false;
  channel.liveSlot = true;
  return true;
}

function scheduleDegradedPoll(channel) {
  if (channel.stopped || channel.degradedTimer !== null) return;
  channel.degradedTimer = globalThis.setTimeout(() => {
    channel.degradedTimer = null;
    channel
      .drain()
      .then(() => openChannel(channel))
      .catch(() => scheduleDegradedPoll(channel));
  }, DEGRADED_POLL_MS);
}

function promoteDegradedChannel() {
  for (const channel of channels.values()) {
    if (channel.stopped || channel.liveSlot || channel.degradedTimer === null) continue;
    globalThis.clearTimeout(channel.degradedTimer);
    channel.degradedTimer = null;
    openChannel(channel);
    return;
  }
}

function scheduleReconnect(channel) {
  if (channel.stopped || channel.retryTimer !== null) return;
  if (channel.source) channel.source.close();
  channel.source = null;
  retryTimerWait(channel);
}

function retryTimerWait(channel) {
  channel.retryTimer = globalThis.setTimeout(() => {
    channel.retryTimer = null;
    channel.retryDelay = Math.min(channel.retryDelay * 2, MAX_DELAY_MS);
    channel
      .drain()
      .then(() => openChannel(channel))
      .catch(() => openChannel(channel));
  }, channel.retryDelay);
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
