/**
 * Minimal DOM construction helpers shared by the workbench modules.
 *
 * The application layer is native ES modules with no framework (interfaces
 * §1l.2); these helpers keep element construction and failure rendering DRY
 * without hiding the DOM contract the ui e2e layer locates.
 */

export function element(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) continue;
    if (name === "text") {
      node.textContent = String(value);
    } else if (name === "hidden") {
      node.hidden = Boolean(value);
    } else {
      node.setAttribute(name, String(value));
    }
  }
  for (const child of children) node.append(child);
  return node;
}

export function clear(node) {
  if (node) node.replaceChildren();
  return node;
}

/** Readable degradation marker for unknown/missing values (interfaces §1l.5). */
export function degradedMessage(text, label = "NotFound") {
  return element("p", {
    class: "degraded",
    "data-testid": `degrade-${label}`,
    role: "note",
    text,
  });
}

/** Observable failure feedback: a rejected request renders, never false success. */
export async function loadInto(node, loader) {
  try {
    await loader();
  } catch (error) {
    const notFound = error && error.status === 404;
    const detail = error && error.message ? error.message : String(error);
    node.append(
      notFound
        ? degradedMessage(`Not found: ${detail}`)
        : element("p", {
            class: "request-error",
            "data-testid": "api-failure",
            role: "alert",
            text: `Request failed: ${detail}`,
          })
    );
  }
}
