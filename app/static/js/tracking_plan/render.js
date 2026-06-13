// app/static/js/tracking_plan/render.js
// Tiny hyperscript: h(tag, attrs, ...children) -> DOM node; mount(container, node)
// replaces the container's children. No vdom — callers re-render the changed
// subtree on state change.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class' || k === 'className') {
        el.className = v;
      } else if (k === 'dataset') {
        for (const [dk, dv] of Object.entries(v)) {
          if (dv != null) el.dataset[dk] = String(dv);
        }
      } else if (k === 'style' && typeof v === 'object') {
        Object.assign(el.style, v);
      } else if (k.startsWith('on') && typeof v === 'function') {
        el.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === 'value') {
        el.value = v;
      } else if (k === 'checked' || k === 'selected' || k === 'disabled') {
        el[k] = !!v;
      } else if (k === 'html') {
        el.innerHTML = v; // caller-escaped only
      } else {
        el.setAttribute(k, v === true ? '' : String(v));
      }
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function mount(container, node) {
  container.replaceChildren(node);
  return node;
}

// Convenience: replace children with several nodes.
export function mountAll(container, nodes) {
  container.replaceChildren(...nodes.filter(Boolean));
}
