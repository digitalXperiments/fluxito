// app/static/js/tracking_plan/views/metrics.js
// Metric designer (spec §5.8), redesigned to the approved mockup
// (/tmp/tp_redesign_mockup.html): master list of metrics + a single-scroll
// editor with a sticky .tp-ed-head save cluster, .tp-card sections (definition
// fields + filter builder), and a live preview.
//
// SAVE MODEL (buffered — nothing saves until Save is clicked): selecting a
// metric snapshots `server` and a `draft` clone of its editable fields; every
// field/filter edit mutates the DRAFT only and re-renders the detail from it.
// `dirty = isDirty(draft, server)`. Save commits ONE update_metric (resolving
// event/property NAME→id via the plan), reloads, and re-snapshots. Discard
// restores the draft. Switching metric while dirty asks to confirm.
//
// Dashboard card link: each metric may be linked to a live dashboard card via
// TPMetric.dashboard_card_id. The Definition card shows a picker that fetches
// available cards via the list_dashboard_cards action and persists the selection
// via update_metric(dashboard_card_id=...). An unlinked metric surfaces the
// metric_not_measured warning from the validation engine.
//
// Hyperscript notes (render.js): h(tag, attrs, ...children); 2nd arg is a plain
// attrs object. All user text via h() children / value attrs — no innerHTML.

import { getState, subscribe, select, reload, setDirty } from "tp/state";
import { doAction } from "tp/api";
import { h, mount, mountAll } from "tp/render";
import { mountDrawer } from "tp/comments";
import { metricPreview } from "tp/util/metricPreview";
import { persist } from "tp/util/persist";
import { clone, isDirty, saveCluster } from "tp/util/editor";
import { eventByName, propByName } from "tp/util/format";

const TYPES = ["count", "sum", "unique", "average", "ratio"];
const NEEDS_PROP = new Set(["sum", "unique", "average"]);

export function mountView(container) {
  const layout = h("div", { class: "tp-master-detail" });
  const master = h("div", { class: "tp-master" });
  const detail = h("div", { class: "tp-detail" });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = "";
  let drawer = null;
  let drawerEntityId = null;

  // The live editor draft + server snapshot for the selected metric.
  let draft = null;
  let server = null;
  let draftId = null; // id the current draft belongs to
  let saving = false;

  const plan = () => getState().plan;
  const metrics = () => (plan() && plan().metrics) || [];
  const selId = () => { const s = getState().selection; return s && s.id; };

  // Dashboard card picker state: list loaded lazily once per mount.
  let dashCards = null; // null = not yet fetched; [] = fetched (may be empty)
  let dashCardsLoading = false;

  async function ensureDashCards() {
    if (dashCards !== null || dashCardsLoading) return;
    dashCardsLoading = true;
    try {
      const r = await doAction("list_dashboard_cards", {}, getState().branch);
      dashCards = r.cards || [];
    } catch (e) {
      dashCards = []; // treat failure as empty list; picker will show "None"
    } finally {
      dashCardsLoading = false;
    }
    renderDetail(); // re-render picker now that cards are available
  }

  // Snapshot a fresh draft from the metric `m` (editable fields only).
  function snapshot(m) {
    server = {
      name: m.name,
      description: m.description || "",
      type: m.type,
      event: m.event || null,
      property: m.property || null,
      filters: { ...(m.filters || {}) },
      dashboard_card_id: m.dashboard_card_id || null,
    };
    draft = clone(server);
    draftId = m.id;
    saving = false;
    setDirty(false);
  }

  function touched() {
    setDirty(isDirty(draft, server));
    renderDetail();
  }

  const unsub = subscribe(() => { renderList(); renderDetailIfSelectionChanged(); });
  renderList();
  renderDetail();

  // Only re-snapshot/rebuild the detail when the SELECTION changes (so field
  // edits, which call renderDetail() directly, don't clobber the draft).
  function renderDetailIfSelectionChanged() {
    const id = selId();
    if (id !== draftId) { renderDetail(); }
  }

  // ── master list ───────────────────────────────────────────────────────────
  function renderList() {
    const head = h(
      "div",
      { class: "tp-master-head" },
      h(
        "div",
        { class: "tp-search" },
        h("svg", { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "2" }),
        h("input", {
          class: "input",
          placeholder: "Search metrics",
          value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        }),
      ),
      h("button", { class: "btn btn-primary btn-sm btn-block", onClick: newMetric }, "+ New metric"),
    );
    // search icon glyph
    head.querySelector("svg").appendChild(svgPath("M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4-4"));
    const listBody = h("div", { class: "tp-master-list" });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h("div", { class: "tp-row-empty" }, "Loading…")]); return; }
    let ms = metrics().slice().sort((a, b) => a.name.localeCompare(b.name));
    if (search) {
      const q = search.toLowerCase();
      ms = ms.filter((m) => m.name.toLowerCase().includes(q) || (m.event || "").toLowerCase().includes(q));
    }
    if (!ms.length) {
      mountAll(listBody, [h("div", { class: "tp-row-empty" }, search ? "No matches." : "No metrics yet.")]);
      return;
    }
    const sel = selId();
    const rows = ms.map((m) =>
      h(
        "div",
        { class: "tp-row" + (sel === m.id ? " is-active" : ""), onClick: () => selectMetric(m.id) },
        h(
          "div",
          { class: "tp-row-main" },
          h("div", { class: "tp-name" }, m.name),
          h("div", { class: "tp-row-sub" }, m.event || m.description || "—"),
        ),
        h("span", { class: "tp-badge ty" }, m.type),
        m.dashboard_card_id
          ? h("span", { class: "tp-badge tp-badge-linked", title: "Linked to dashboard card" }, "✓ linked")
          : h("span", { class: "tp-badge tp-badge-unlinked", title: "Not linked to any dashboard card" }, "unlinked"),
      ),
    );
    mountAll(listBody, rows);
  }

  function selectMetric(id) {
    if (id === selId()) return;
    if (getState().dirty && !confirm("Discard unsaved changes?")) return;
    setDirty(false);
    select("metric", id);
  }

  async function newMetric() {
    if (getState().dirty && !confirm("Discard unsaved changes?")) return;
    try {
      const r = await persist("Metric created", () =>
        doAction("create_metric", { name: "New metric", type: "count" }, getState().branch),
      );
      setDirty(false);
      await reload();
      if (r && r.id) select("metric", r.id);
    } catch (err) { /* persist surfaced the banner */ }
  }

  // ── detail / editor ───────────────────────────────────────────────────────
  function renderDetail() {
    if (!plan()) { mountAll(detail, [h("div", { class: "tp-empty" }, "Loading…")]); return; }
    const m = metrics().find((x) => x.id === selId());
    if (!m) {
      if (drawer) { drawer.destroy(); drawer = null; drawerEntityId = null; }
      draft = server = null; draftId = null;
      mountAll(detail, [h("div", { class: "tp-empty" }, h("div", {}, "Select a metric to design it, or create one."))]);
      return;
    }
    // Snapshot a new draft only when the selected metric changes.
    if (draftId !== m.id) snapshot(m);

    // Kick off card list fetch lazily so the picker can populate.
    ensureDashCards();

    const inner = h("div", { class: "tp-detail-inner" });
    inner.appendChild(buildHead(m));
    inner.appendChild(buildPreviewCard());
    inner.appendChild(buildDefinitionCard());
    inner.appendChild(buildDashboardLinkCard());
    inner.appendChild(buildFiltersCard());
    mountAll(detail, [inner]);

    // Recreate the drawer only when the selected metric changes, so an open
    // Comments panel survives field edits (which re-render the detail).
    if (drawerEntityId !== m.id) {
      if (drawer) { drawer.destroy(); drawer = null; }
      drawer = mountDrawer(document.querySelector(".tp-workspace") || document.body, {
        entityType: "metric",
        entityId: m.id,
        branch: getState().branch,
      });
      drawerEntityId = m.id;
    }
  }

  // sticky editor head: kicker + editable mono name + Comments/Delete + save cluster
  function buildHead(m) {
    const nameInp = h("input", { class: "input tp-titlefield", value: draft.name, placeholder: "metric_name" });
    nameInp.oninput = () => { draft.name = nameInp.value; setDirty(isDirty(draft, server)); refreshClusterOnly(); };
    const idBlock = h(
      "div",
      { class: "tp-ed-id" },
      h("div", { class: "tp-ed-kicker" }, "Metric"),
      nameInp,
    );

    const chips = h(
      "div",
      { class: "tp-ed-chips" },
      h("span", { class: "tp-badge ty" }, draft.type),
    );

    const commentsBtn = h("button", { class: "btn btn-ghost btn-sm", onClick: () => drawer && drawer.open() }, "Comments");
    const delBtn = h("button", { class: "btn btn-danger btn-sm", onClick: () => delMetric(m) }, "Delete");

    const actions = h(
      "div",
      { class: "tp-ed-actions" },
      commentsBtn,
      delBtn,
      h("div", { class: "tp-divv" }),
      saveCluster({
        dirty: isDirty(draft, server),
        saving,
        onSave: () => doSave(m),
        onDiscard: () => { draft = clone(server); setDirty(false); renderDetail(); },
      }),
    );

    return h("div", { class: "tp-ed-head" }, h("div", { class: "tp-ed-id-row" }, idBlock, chips, actions));
  }

  // live preview card
  function buildPreviewCard() {
    return h(
      "div",
      { class: "tp-card" },
      h("div", { class: "tp-card-h" }, h("h3", {}, "Preview")),
      h("div", { class: "tp-card-b" }, h("div", { class: "tp-metric-preview" }, metricPreview(draft))),
    );
  }

  // definition card: type / event / property pickers + description
  function buildDefinitionCard() {
    const body = h("div", { class: "tp-card-b" });
    const grid = h("div", { class: "tp-grid2" });

    const typeSel = h("select", { class: "select" });
    TYPES.forEach((t) => typeSel.appendChild(h("option", { value: t, selected: draft.type === t }, t)));
    typeSel.onchange = () => { draft.type = typeSel.value; if (!NEEDS_PROP.has(draft.type)) draft.property = null; touched(); };
    grid.appendChild(field("Type", typeSel));

    const evSel = h("select", { class: "select" });
    evSel.appendChild(h("option", { value: "" }, "(no event)"));
    (plan().events || []).forEach((e) =>
      evSel.appendChild(h("option", { value: e.name, selected: draft.event === e.name }, e.name)),
    );
    evSel.onchange = () => { draft.event = evSel.value || null; touched(); };
    grid.appendChild(field("Event", evSel));

    // property picker — only meaningful for sum/unique/average
    if (NEEDS_PROP.has(draft.type)) {
      const propSel = h("select", { class: "select" });
      propSel.appendChild(h("option", { value: "" }, "(no property)"));
      ((plan().properties && plan().properties.event) || []).forEach((p) =>
        propSel.appendChild(h("option", { value: p.name, selected: draft.property === p.name }, p.name)),
      );
      propSel.onchange = () => { draft.property = propSel.value || null; touched(); };
      grid.appendChild(field("Property", propSel));
    }

    const descTa = h("textarea", { class: "textarea", placeholder: "What does this metric measure?" });
    descTa.value = draft.description;
    descTa.oninput = () => { draft.description = descTa.value; setDirty(isDirty(draft, server)); refreshClusterOnly(); };
    grid.appendChild(field("Description", descTa, true));

    body.appendChild(grid);
    return h("div", { class: "tp-card" }, h("div", { class: "tp-card-h" }, h("h3", {}, "Definition")), body);
  }

  // dashboard card link section
  function buildDashboardLinkCard() {
    const body = h("div", { class: "tp-card-b" });

    if (dashCards === null) {
      // Still loading — show a placeholder row; renderDetail() will replace it.
      body.appendChild(h("div", { class: "tp-muted", style: "font-size:12.5px" }, "Loading dashboard cards…"));
    } else if (!dashCards.length) {
      const msg = h("div", { class: "tp-muted", style: "font-size:12.5px" });
      msg.appendChild(document.createTextNode("No dashboard cards found. "));
      const link = h("a", { href: "/dashboards", target: "_blank" }, "Create a dashboard →");
      msg.appendChild(link);
      body.appendChild(msg);
    } else {
      // Build the picker: "(unlinked)" option + one option per card.
      const sel = h("select", { class: "select" });
      sel.appendChild(h("option", { value: "" }, "(not linked)"));
      dashCards.forEach((c) => {
        const label = c.dashboard_title ? `${c.dashboard_title} — ${c.title}` : c.title;
        sel.appendChild(
          h("option", { value: c.id, selected: draft.dashboard_card_id === c.id }, label),
        );
      });
      sel.onchange = () => {
        draft.dashboard_card_id = sel.value || null;
        touched();
      };
      body.appendChild(field("Dashboard card", sel));

      // Status line: show link badge or warning if unlinked.
      if (draft.dashboard_card_id) {
        const linked = dashCards.find((c) => c.id === draft.dashboard_card_id);
        const label = linked
          ? (linked.dashboard_title ? `${linked.dashboard_title} — ${linked.title}` : linked.title)
          : draft.dashboard_card_id;
        body.appendChild(
          h("div", { class: "tp-muted", style: "font-size:12px;margin-top:4px" },
            h("span", { class: "tp-badge tp-badge-linked" }, "✓ linked"),
            ` ${label}`,
          ),
        );
      } else {
        body.appendChild(
          h("div", { class: "tp-muted", style: "font-size:12px;margin-top:4px" },
            h("span", { class: "tp-badge tp-badge-unlinked" }, "unlinked"),
            " Link this metric to a live dashboard card to dismiss the metric_not_measured warning.",
          ),
        );
      }
    }

    return h(
      "div",
      { class: "tp-card" },
      h("div", { class: "tp-card-h" }, h("h3", {}, "Dashboard link")),
      body,
    );
  }

  // filter builder card
  function buildFiltersCard() {
    const body = h("div", { class: "tp-card-b" });
    const list = h("div", { class: "tp-filter-list" });

    const renderFilters = () => {
      const keys = Object.keys(draft.filters);
      if (!keys.length) {
        mountAll(list, [h("div", { class: "tp-muted", style: "font-size:12.5px" }, "No filters — this metric counts all matching events.")]);
        return;
      }
      const rows = keys.map((k) => {
        const kIn = h("input", { class: "input mono", value: k, placeholder: "property", style: "max-width:200px" });
        const vIn = h("input", { class: "input mono", value: draft.filters[k], placeholder: "value" });
        const rm = h("button", { class: "btn btn-ghost btn-sm", title: "Remove filter" }, "✕");
        kIn.onchange = () => {
          const nv = draft.filters[k];
          delete draft.filters[k];
          const nk = kIn.value.trim();
          if (nk) draft.filters[nk] = nv;
          renderFilters();
          touched();
        };
        vIn.oninput = () => { draft.filters[k] = vIn.value; setDirty(isDirty(draft, server)); refreshClusterOnly(); };
        rm.onclick = () => { delete draft.filters[k]; renderFilters(); touched(); };
        return h("div", { class: "tp-filter-row" }, kIn, h("span", { class: "tp-muted" }, "="), vIn, rm);
      });
      mountAll(list, rows);
    };
    renderFilters();
    body.appendChild(list);

    const addF = h("button", { class: "btn btn-secondary btn-sm", style: "margin-top:4px" }, "+ Add filter");
    addF.onclick = () => {
      let base = "field";
      let i = 1;
      while (Object.prototype.hasOwnProperty.call(draft.filters, base)) base = `field${++i}`;
      draft.filters[base] = "";
      renderFilters();
      touched();
    };
    body.appendChild(addF);

    return h("div", { class: "tp-card" }, h("div", { class: "tp-card-h" }, h("h3", {}, "Filters")), body);
  }

  // Update ONLY the head save cluster + preview without rebuilding inputs, so
  // textarea/value-input focus + caret survive while typing.
  function refreshClusterOnly() {
    const head = detail.querySelector(".tp-ed-actions");
    if (head) {
      const old = head.querySelector(".tp-savecluster");
      const fresh = saveCluster({
        dirty: isDirty(draft, server),
        saving,
        onSave: () => doSave(currentMetric()),
        onDiscard: () => { draft = clone(server); setDirty(false); renderDetail(); },
      });
      if (old) old.replaceWith(fresh); else head.appendChild(fresh);
    }
    const prev = detail.querySelector(".tp-metric-preview");
    if (prev) prev.textContent = metricPreview(draft);
    const typeChip = detail.querySelector(".tp-ed-chips .tp-badge");
    if (typeChip) typeChip.textContent = draft.type;
  }

  function currentMetric() { return metrics().find((x) => x.id === draftId) || null; }

  async function doSave(m) {
    if (!m) return;
    saving = true;
    renderDetail();
    const ev = draft.event ? eventByName(plan(), draft.event) : null;
    const pr = NEEDS_PROP.has(draft.type) && draft.property ? propByName(plan(), draft.property) : null;
    const cleanFilters = {};
    Object.keys(draft.filters).forEach((k) => { if (k.trim()) cleanFilters[k] = draft.filters[k]; });
    try {
      await persist("Saved", () =>
        doAction(
          "update_metric",
          {
            metric_id: m.id,
            name: draft.name.trim(),
            description: draft.description || null,
            type: draft.type,
            event_id: ev ? ev.id : null,
            property_id: pr ? pr.id : null,
            filters: Object.keys(cleanFilters).length ? cleanFilters : null,
            dashboard_card_id: draft.dashboard_card_id || null,
          },
          getState().branch,
        ),
      );
      await reload();
      const fresh = currentMetric();
      if (fresh) snapshot(fresh); // re-snapshot server + draft from fresh entity
      renderDetail();
    } catch (err) {
      saving = false;
      renderDetail(); // keep the draft; error already bannered
    }
  }

  async function delMetric(m) {
    if (!confirm(`Delete metric "${m.name}"?`)) return;
    try {
      await persist("Metric deleted", () => doAction("delete_metric", { metric_id: m.id }, getState().branch));
      setDirty(false);
      select("metric", null);
      await reload();
    } catch (err) { /* persist surfaced the banner */ }
  }

  return () => { unsub(); if (drawer) drawer.destroy(); };
}

function field(label, control, full) {
  return h(
    "div",
    { class: "tp-field" + (full ? " tp-col-2" : "") },
    h("label", { class: "tp-lbl" }, label),
    control,
  );
}

// Build an SVG <path> via createElementNS (h() makes invisible SVG).
function svgPath(d) {
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", d);
  return p;
}
