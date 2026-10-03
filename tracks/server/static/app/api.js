/**
 * Same-origin JSON client for the workbench (interfaces §1l.6 / §2b).
 *
 * Every mutation injects the two write-safety headers the HTTP contract
 * requires: ``X-Trac-CSRF`` (the session's CSRF credential, seeded in memory
 * from the auth face — the client persists no credential) and
 * ``Idempotency-Key`` (a fresh request identity for the command service's
 * de-duplication). Failures raise so callers render an observable error
 * instead of reporting success; the parsed error body rides on the thrown
 * error so the editor can read the 409 top-level ``current_revision``.
 */

export const CSRF_HEADER = "X-Trac-CSRF";
export const IDEMPOTENCY_HEADER = "Idempotency-Key";
export const MUTATION_METHODS = ["POST", "PUT", "PATCH", "DELETE"];

let csrfCredential = "";

/** Seed the in-memory CSRF credential (the login response's ``csrf_token``). */
export function setCsrfToken(token) {
  csrfCredential = typeof token === "string" ? token : "";
}

export function getCsrfToken() {
  return csrfCredential;
}

export function newIdempotencyKey() {
  const crypto = globalThis.crypto;
  if (crypto && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export async function requestJson(url, options = {}) {
  const method = String(options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (MUTATION_METHODS.includes(method)) {
    headers.set(CSRF_HEADER, options.csrfToken || csrfCredential);
    headers.set(IDEMPOTENCY_HEADER, options.idempotencyKey || newIdempotencyKey());
  }
  const response = await fetch(url, {
    method,
    headers,
    body: options.body,
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw await failure(response, method, url);
  }
  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function failure(response, method, url) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  const detail = payload && payload.error ? payload.error.detail : "";
  const fault = new Error(
    `${method} ${url} failed with status ${response.status}${detail ? `: ${detail}` : ""}`
  );
  fault.status = response.status;
  fault.payload = payload;
  return fault;
}

export function postJson(url, payload, options = {}) {
  return requestJson(url, { ...options, method: "POST", body: JSON.stringify(payload) });
}