/**
 * Merged event-stream client (IF-STREAM-001 client face; interfaces §1l.6).
 *
 * A run snapshot carries ``event_cursor`` — the position the snapshot already
 * reflects. Reconnecting opens the stream with that cursor as the ``after``
 * parameter, so the server replays strictly after it (no gap, no overlap),
 * then applies live frames. A monotonic guard drops duplicate or out-of-order
 * frames (non-advancing cursors) so a stale replay can never regress the UI.
 *
 * The caller supplies the event-type names it learned from the server
 * projection, so the client never hardcodes the closed event set.
 */

import { requestJson } from "./api.js";

const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 15000;

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
  let stopped = false;

  function backfillUrl() {
    const base = `/api/runs/${encodeURIComponent(runId)}/events`;
    if (!lastEventCursor) return base;
    return `${base}?after=${encodeURIComponent(lastEventCursor)}`;
  }

  function shouldApply(cursor) {
    if (!cursor) return false;
    if (lastEventCursor === null || lastEventCursor === "") return true;
    if (cursor === lastEventCursor) return false;
    return compareCursors(cursor, lastEventCursor) > 0;
  }

  function apply(message) {
    const cursor = message.lastEventId || "";
    if (!shouldApply(cursor)) return; // duplicates/out-of-order never regress the UI
    lastEventCursor = cursor;
    let payload = null;
    try {
      payload = JSON.parse(message.data);
    } catch (error) {
      payload = { raw: message.data };
    }
    if (onEvent) onEvent({ type: message.type, cursor, payload });
  }

  function open() {
    if (stopped) return;
    source = new EventSource(backfillUrl());
    source.onopen = () => {
      retryDelay = BASE_DELAY_MS;
      if (onStatus) onStatus("live");
    };
    source.onmessage = (message) => apply(message);
    for (const type of eventTypes) {
      source.addEventListener(type, (message) => apply(message));
    }
    source.onerror = () => {
      if (onStatus) onStatus("reconnecting");
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    if (stopped || retryTimer !== null) return;
    if (source) source.close();
    retryTimer = globalThis.setTimeout(() => {
      retryTimer = null;
      retryDelay = Math.min(retryDelay * 2, MAX_DELAY_MS);
      open();
    }, retryDelay);
  }

  async function backfill() {
    const batch = await requestJson(`${backfillUrl()}${lastEventCursor ? "&" : "?"}wait=0`);
    if (batch && batch.cursor) lastEventCursor = batch.cursor;
    if (onStatus) onStatus("backfilled");
    return batch;
  }

  function close() {
    stopped = true;
    if (retryTimer !== null) globalThis.clearTimeout(retryTimer);
    retryTimer = null;
    if (source) source.close();
    source = null;
  }

  open();
  return { close, backfill, cursor: () => lastEventCursor };
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
  if (!a || !b) return left > right ? 1 : left < right ? -1 : 0;
  for (let index = 0; index < 3; index += 1) {
    if (a[index] < b[index]) return -1;
    if (a[index] > b[index]) return 1;
  }
  return 0;
}