// app/static/js/tracking_plan/views/metrics.js
// Metric designer (spec §5.8): type, event/property pickers, filter builder,
// live preview, comments drawer.
import { getState, subscribe, select, reload } from "tp/state";
import { doAction } from "tp/api";
import { h, mount } from "tp/render";
import { mountDrawer } from "tp/comments";
import { metricPreview } from "tp/util/metricPreview";
import { banner } from "tp/shell";

const TYPES = ["count", "sum", "unique", "average", "ratio"];
const NEEDS_PROP = new Set(["sum", "unique", "average"]);

export function mountView(container) {
  let _drawer = null;

  // Declare render BEFORE subscribe (TDZ fix)
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    // Destroy old drawer when re-rendering a full new detail panel
    if (_drawer) { _drawer.destroy(); _drawer = null; }
    const root = h("div", { class: "tp-pane is-active" });
    root.appendChild(buildMaster(st));
    const { node: detailNode, drawer } = buildDetail(st);
    _drawer = drawer;
    root.appendChild(detailNode);
    mount(container, root);
  };

  const unsub = subscribe(render);
  render();

  return () => {
    unsub();
    if (_drawer) { _drawer.destroy(); _drawer = null; }
  };
}

function buildMaster(st) {
  const m = h("div", { class: "tp-master" });
  const head = h("div", { class: "tp-master-head" });
  const add = h("button", { class: "btn btn-primary btn-sm btn-block" }, "+ New metric");
  add.onclick = async () => {
    try {
      // Use returned id from doAction (NOT find-by-name after reload)
      const r = await doAction("create_metric", { name: "New metric", type: "count" }, getState().branch);
      await reload();
      if (r && r.id) select("metric", r.id);
    } catch (e) {
      banner(e.message, "err");
    }
  };
  head.appendChild(add);
  m.appendChild(head);

  const list = h("div", { class: "tp-master-list" });
  const sel = st.selection && st.selection.id;
  const metrics = st.plan.metrics || [];
  if (!metrics.length) {
    list.appendChild(h("div", { class: "tp-row-empty" }, "No metrics yet."));
  }
  metrics.forEach((mm) => {
    const row = h("div", { class: "tp-row" + (sel === mm.id ? " is-active" : "") });
    const main = h("div", { class: "tp-row-main" });
    // Use h() for user text — no innerHTML (XSS fix)
    main.appendChild(h("div", { class: "tp-name" }, mm.name));
    main.appendChild(h("div", { class: "tp-row-sub" }, mm.event || mm.description || "—"));
    row.appendChild(main);
    row.appendChild(h("div", { class: "tp-row-meta" }, mm.type));
    // 2-positional select (stale spec fix)
    row.onclick = () => select("metric", mm.id);
    list.appendChild(row);
  });
  m.appendChild(list);
  return m;
}

// Returns { node, drawer } so caller can track the drawer for teardown
function buildDetail(st) {
  const d = h("div", { class: "tp-detail" });
  const m = (st.plan.metrics || []).find(
    (x) => x.id === (st.selection && st.selection.id)
  );
  if (!m) {
    const empty = h("div", { class: "tp-empty" });
    empty.appendChild(h("div", {}, "Select a metric to design it."));
    d.appendChild(empty);
    return { node: d, drawer: null };
  }

  // Live draft — event/property are names; resolved to ids on save
  const draft = {
    name: m.name,
    description: m.description || "",
    type: m.type,
    event: m.event || null,
    property: m.property || null,
    filters: { ...(m.filters || {}) },
  };

  const inner = h("div", { class: "tp-detail-inner" });

  // ── Header ────────────────────────────────────────────────────────────
  const head = h("div", { class: "tp-d-head" });
  const title = h("div", { class: "tp-d-title" });
  // Use h('input') with value attr — not innerHTML (XSS fix)
  const nameInp = h("input", { class: "tp-titlefield", value: draft.name });
  nameInp.oninput = () => { draft.name = nameInp.value; refreshPreview(); };
  title.appendChild(nameInp);
  head.appendChild(title);

  const acts = h("div", { class: "tp-d-actions" });
  const drawerBtn = h("button", { class: "btn btn-ghost btn-sm" }, "💬 Comments");
  const del = h("button", { class: "btn btn-ghost btn-sm" }, "Delete");
  const save = h("button", { class: "btn btn-primary btn-sm" }, "Save");
  acts.appendChild(drawerBtn);
  acts.appendChild(del);
  acts.appendChild(save);
  head.appendChild(acts);
  inner.appendChild(head);

  // ── Live preview ──────────────────────────────────────────────────────
  // Use textContent assignment (not innerHTML) for live preview text (XSS fix)
  const preview = h("div", { class: "tp-metric-preview" });
  preview.textContent = metricPreview(draft);
  const refreshPreview = () => { preview.textContent = metricPreview(draft); };
  inner.appendChild(preview);

  // ── Fields ────────────────────────────────────────────────────────────
  const sec = h("div", { class: "tp-section" });
  const grid = h("div", { class: "tp-fieldgrid" });

  // Type select — 2nd arg is plain attrs object (dropped-first-option fix)
  const typeSel = h("select", {});
  TYPES.forEach((t) => {
    const o = h("option", { value: t }, t);
    if (draft.type === t) o.selected = true;
    typeSel.appendChild(o);
  });
  grid.appendChild(wrapField("Type", typeSel));

  // Event select — 2nd arg is plain attrs object (dropped-first-option fix)
  const evSel = h("select", {});
  evSel.appendChild(h("option", { value: "" }, "(no event)"));
  (st.plan.events || []).forEach((e) => {
    const o = h("option", { value: e.name }, e.name);
    if (draft.event === e.name) o.selected = true;
    evSel.appendChild(o);
  });
  evSel.onchange = () => { draft.event = evSel.value || null; refreshPreview(); };
  grid.appendChild(wrapField("Event", evSel));

  // Property picker — only visible for sum/unique/average
  const propField = wrapField("Property", null);
  // Property select — 2nd arg is plain attrs object (dropped-first-option fix)
  const propSel = h("select", {});
  propSel.appendChild(h("option", { value: "" }, "(no property)"));
  (st.plan.properties && st.plan.properties.event ? st.plan.properties.event : []).forEach((p) => {
    const o = h("option", { value: p.name }, p.name);
    if (draft.property === p.name) o.selected = true;
    propSel.appendChild(o);
  });
  propSel.onchange = () => { draft.property = propSel.value || null; refreshPreview(); };
  propField.appendChild(propSel);
  grid.appendChild(propField);

  const syncPropVisibility = () => {
    propField.style.display = NEEDS_PROP.has(draft.type) ? "" : "none";
  };
  typeSel.onchange = () => { draft.type = typeSel.value; syncPropVisibility(); refreshPreview(); };
  syncPropVisibility();

  // Description textarea — use h() with text child (not innerHTML)
  const descTa = h("textarea", {});
  descTa.value = draft.description;
  descTa.oninput = () => { draft.description = descTa.value; };
  grid.appendChild(wrapField("Description", descTa, true));

  sec.appendChild(grid);
  inner.appendChild(sec);

  // ── Filter builder ────────────────────────────────────────────────────
  const fsec = h("div", { class: "tp-section" });
  fsec.appendChild(h("h3", {}, "Filters"));
  const fwrap = h("div", { class: "tp-filter-list" });

  const renderFilters = () => {
    // Use replaceChildren to clear (not innerHTML)
    fwrap.replaceChildren();
    Object.keys(draft.filters).forEach((k) => {
      const r = h("div", { class: "tp-filter-row" });
      // Use h('input') with value attr — not innerHTML (XSS fix)
      const kIn = h("input", { value: k, placeholder: "property", class: "tp-mono-input" });
      const vIn = h("input", { value: draft.filters[k], placeholder: "value" });
      const rm = h("button", { class: "btn btn-ghost btn-sm" }, "✕");
      kIn.onchange = () => {
        const nv = draft.filters[k];
        delete draft.filters[k];
        if (kIn.value.trim()) draft.filters[kIn.value.trim()] = nv;
        renderFilters();
        refreshPreview();
      };
      vIn.oninput = () => { draft.filters[k] = vIn.value; refreshPreview(); };
      rm.onclick = () => { delete draft.filters[k]; renderFilters(); refreshPreview(); };
      r.appendChild(kIn);
      r.appendChild(h("span", { class: "tp-muted" }, "="));
      r.appendChild(vIn);
      r.appendChild(rm);
      fwrap.appendChild(r);
    });
  };
  renderFilters();
  fsec.appendChild(fwrap);

  const addF = h("button", { class: "btn btn-secondary btn-sm" }, "+ Add filter");
  addF.onclick = () => {
    let base = "field";
    let i = 1;
    while (Object.prototype.hasOwnProperty.call(draft.filters, base)) base = `field${++i}`;
    draft.filters[base] = "";
    renderFilters();
    refreshPreview();
  };
  fsec.appendChild(addF);
  inner.appendChild(fsec);

  // ── Save / Delete / Comments wiring ──────────────────────────────────
  const evByName = (n) => (st.plan.events || []).find((e) => e.name === n);
  const propByName = (n) =>
    (st.plan.properties && st.plan.properties.event ? st.plan.properties.event : []).find(
      (p) => p.name === n
    );

  save.onclick = async () => {
    const ev = draft.event ? evByName(draft.event) : null;
    const pr = NEEDS_PROP.has(draft.type) && draft.property ? propByName(draft.property) : null;
    const cleanFilters = {};
    Object.keys(draft.filters).forEach((k) => {
      if (k.trim()) cleanFilters[k] = draft.filters[k];
    });
    try {
      await doAction(
        "update_metric",
        {
          metric_id: m.id,
          name: draft.name.trim(),
          description: draft.description || null,
          type: draft.type,
          event_id: ev ? ev.id : null,
          property_id: pr ? pr.id : null,
          filters: Object.keys(cleanFilters).length ? cleanFilters : null,
        },
        getState().branch
      );
      await reload();
    } catch (e) {
      banner(e.message, "err");
    }
  };

  del.onclick = async () => {
    if (!confirm(`Delete metric "${m.name}"?`)) return;
    try {
      await doAction("delete_metric", { metric_id: m.id }, getState().branch);
      // 2-positional select (stale spec fix); null clears selection
      select("metric", null);
      await reload();
    } catch (e) {
      banner(e.message, "err");
    }
  };

  // Mount drawer into workspace root (fixed-position), toggled by button
  const drawerContainer = document.querySelector(".tp-workspace") || document.body;
  const drawer = mountDrawer(drawerContainer, {
    entityType: "metric",
    entityId: m.id,
    branch: st.branch,
  });
  drawerBtn.onclick = () => drawer.open();

  d.appendChild(inner);
  return { node: d, drawer };
}

function wrapField(label, control, full) {
  const f = h("div", { class: "tp-field" + (full ? " tp-col-2" : "") });
  f.appendChild(h("label", {}, label));
  if (control) f.appendChild(control);
  return f;
}
