// app/static/js/tracking_plan/views/sources.js
// Sources & Destinations (spec §5.7), redesigned to the approved mockup
// (/tmp/tp_redesign_mockup.html): a refined 2-column CATALOG of .tp-card source
// and destination cards, plus a clean ROUTING GRAPH with SVG connectors.
//
// SAVE MODEL (per the buffered-save requirement): each catalog card holds a
// local DRAFT of its editable FIELDS ({name, platform_type/platform,
// account_id}); typing mutates the draft only and never hits the API. The card
// header shows the save cluster (● Unsaved / Discard / Save) via util/editor;
// Save commits one update_<entity> and reloads; Discard restores the draft.
// Routing (connect/disconnect) stays IMMEDIATE — it is a direct routing action,
// not a field edit — exactly as the brief permits.
//
// Hyperscript notes (render.js): h(tag, attrs, ...children); the 2nd arg MUST be
// a plain attrs object (never a node). SVG shapes use createElementNS (h() makes
// HTMLElements, which are invisible as SVG). All user text goes through h()
// children / value attrs — no innerHTML.

import { getState, subscribe, reload } from "tp/state";
import { doAction } from "tp/api";
import { h, mount } from "tp/render";
import { routingLinks, isLinked } from "tp/util/routing";
import { persist } from "tp/util/persist";
import { clone, isDirty, saveCluster } from "tp/util/editor";

const PLATFORM_TYPES = ["ios", "android", "web", "server", "warehouse"];

// transient (non-plan) UI state for the routing graph: pending source + hover.
let pendingSourceId = null;
let hoverNodeId = null;

export function mountView(container) {
  // Declare render before subscribe to avoid TDZ.
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    const root = h("div", { class: "tp-pane is-active" });
    const detail = h("div", { class: "tp-detail", style: "flex:1" });
    const inner = h("div", { class: "tp-detail-inner", style: "max-width:1040px" });
    inner.appendChild(headerSection(st, render));
    inner.appendChild(catalogSection(st, render));
    inner.appendChild(graphSection(st, render));
    detail.appendChild(inner);
    root.appendChild(detail);
    mount(container, root);
    drawConnectors(detail, st);
  };

  const unsub = subscribe(render);
  render();

  const onResize = () => {
    const d = container.querySelector(".tp-detail");
    if (d) drawConnectors(d, getState());
  };
  window.addEventListener("resize", onResize);

  return () => {
    unsub();
    window.removeEventListener("resize", onResize);
  };
}

// ── page header: title + add buttons ────────────────────────────────────────
function headerSection(st, rerender) {
  const head = h("div", { class: "tp-d-head" });
  const title = h(
    "div",
    { class: "tp-d-title" },
    h("h2", { style: "margin:0;font-size:20px;font-weight:650;letter-spacing:-.01em" }, "Sources & destinations"),
  );
  head.appendChild(title);

  const addSrc = h("button", { class: "btn btn-secondary btn-sm" }, "+ Add source");
  addSrc.onclick = async () => {
    await persist("Source created", () =>
      doAction("create_source", { name: "New source", platform_type: "web" }, getState().branch),
    );
    await reload();
  };
  const addDest = h("button", { class: "btn btn-primary btn-sm" }, "+ Add destination");
  addDest.onclick = async () => {
    await persist("Destination created", () =>
      doAction("create_destination", { name: "New destination", platform: "ga4" }, getState().branch),
    );
    await reload();
  };
  head.appendChild(h("div", { class: "tp-d-actions" }, addSrc, addDest));
  // keep rerender referenced (header has no buffered field of its own)
  void rerender;
  return head;
}

// ── 2-column catalog of source + destination cards ──────────────────────────
function catalogSection(st, rerender) {
  const sec = h("div", { class: "tp-section" });
  const cols = h("div", { class: "tp-catalog" });

  // sources column
  const sCol = h("div", { class: "tp-catalog-col" });
  sCol.appendChild(h("div", { class: "tp-catalog-head" }, "Sources"));
  const sources = st.plan.sources || [];
  if (!sources.length) {
    sCol.appendChild(h("div", { class: "tp-row-empty" }, "No sources yet."));
  }
  sources.forEach((s) => sCol.appendChild(sourceCard(s, st.branch, rerender)));
  cols.appendChild(sCol);

  // destinations column
  const dCol = h("div", { class: "tp-catalog-col" });
  dCol.appendChild(h("div", { class: "tp-catalog-head" }, "Destinations"));
  const dests = st.plan.destinations || [];
  if (!dests.length) {
    dCol.appendChild(h("div", { class: "tp-row-empty" }, "No destinations yet."));
  }
  dests.forEach((d) => dCol.appendChild(destCard(d, st.branch, rerender)));
  cols.appendChild(dCol);

  sec.appendChild(cols);
  return sec;
}

// A single source .tp-card with a buffered {name, platform_type} draft.
function sourceCard(s, branch, rerender) {
  const server = { name: s.name, platform_type: s.platform_type || "" };
  const draft = clone(server);
  let saving = false;

  const card = h("div", { class: "tp-card", style: "margin-bottom:12px" });
  const headHa = h("div", { class: "tp-card-ha" });
  const head = h(
    "div",
    { class: "tp-card-h" },
    h("span", { class: "tp-sd green" }),
    h("h3", { class: "tp-mono" }, s.name),
    headHa,
  );
  card.appendChild(head);

  const refreshCluster = () => {
    const dirty = isDirty(draft, server);
    mount(
      headHa,
      saveCluster({
        dirty,
        saving,
        onSave: doSave,
        onDiscard: () => {
          Object.assign(draft, clone(server));
          rerender();
        },
      }),
    );
  };

  async function doSave() {
    saving = true;
    refreshCluster();
    try {
      await persist("Source saved", () =>
        doAction(
          "update_source",
          { source_id: s.id, name: draft.name.trim(), platform_type: draft.platform_type || null },
          getState().branch,
        ),
      );
      await reload(); // re-snapshots from a fresh render
    } catch (err) {
      saving = false;
      refreshCluster();
    }
  }

  const body = h("div", { class: "tp-card-b" });
  const grid = h("div", { class: "tp-grid2" });

  // name (mono identifier)
  const nameInp = h("input", { class: "input mono", value: draft.name, placeholder: "source_name" });
  nameInp.oninput = () => { draft.name = nameInp.value; refreshCluster(); };
  grid.appendChild(field("Name", nameInp));

  // platform type
  const plSel = h("select", { class: "select" });
  plSel.appendChild(h("option", { value: "" }, "platform…"));
  PLATFORM_TYPES.forEach((t) => {
    const o = h("option", { value: t, selected: draft.platform_type === t }, t);
    plSel.appendChild(o);
  });
  plSel.onchange = () => { draft.platform_type = plSel.value; refreshCluster(); };
  grid.appendChild(field("Platform", plSel));

  body.appendChild(grid);

  const del = h("button", { class: "btn btn-danger btn-sm", style: "margin-top:14px" }, "Delete source");
  del.onclick = async () => {
    if (!confirm(`Delete source "${s.name}"?`)) return;
    await persist("Source deleted", () => doAction("delete_source", { source_id: s.id }, branch));
    await reload();
  };
  body.appendChild(del);

  card.appendChild(body);
  refreshCluster();
  return card;
}

// A single destination .tp-card with a buffered {name, platform, account_id} draft.
function destCard(dest, branch, rerender) {
  const server = {
    name: dest.name,
    platform: dest.platform || "",
    account_id: dest.platform_account_id || "",
  };
  const draft = clone(server);
  let saving = false;

  const card = h("div", { class: "tp-card", style: "margin-bottom:12px" });
  const headHa = h("div", { class: "tp-card-ha" });
  const head = h("div", { class: "tp-card-h" }, h("h3", { class: "tp-mono" }, dest.name), headHa);
  card.appendChild(head);

  const refreshCluster = () => {
    const dirty = isDirty(draft, server);
    mount(
      headHa,
      saveCluster({
        dirty,
        saving,
        onSave: doSave,
        onDiscard: () => {
          Object.assign(draft, clone(server));
          rerender();
        },
      }),
    );
  };

  async function doSave() {
    saving = true;
    refreshCluster();
    try {
      await persist("Destination saved", () =>
        doAction(
          "update_destination",
          {
            destination_id: dest.id,
            name: draft.name.trim(),
            platform: draft.platform.trim(),
            platform_account_id: draft.account_id || null,
          },
          getState().branch,
        ),
      );
      await reload();
    } catch (err) {
      saving = false;
      refreshCluster();
    }
  }

  const body = h("div", { class: "tp-card-b" });
  const grid = h("div", { class: "tp-grid2" });

  const nameInp = h("input", { class: "input mono", value: draft.name, placeholder: "Destination" });
  nameInp.oninput = () => { draft.name = nameInp.value; refreshCluster(); };
  grid.appendChild(field("Name", nameInp));

  const plInp = h("input", { class: "input mono", value: draft.platform, placeholder: "ga4 / amplitude / …" });
  plInp.oninput = () => { draft.platform = plInp.value; refreshCluster(); };
  grid.appendChild(field("Platform", plInp));

  const acctInp = h("input", { class: "input mono", value: draft.account_id, placeholder: "account id" });
  acctInp.oninput = () => { draft.account_id = acctInp.value; refreshCluster(); };
  grid.appendChild(field("Account ID", acctInp));

  body.appendChild(grid);

  const del = h("button", { class: "btn btn-danger btn-sm", style: "margin-top:14px" }, "Delete destination");
  del.onclick = async () => {
    if (!confirm(`Delete destination "${dest.name}"?`)) return;
    await persist("Destination deleted", () => doAction("delete_destination", { destination_id: dest.id }, branch));
    await reload();
  };
  body.appendChild(del);

  card.appendChild(body);
  refreshCluster();
  return card;
}

function field(label, control) {
  return h("div", { class: "tp-field" }, h("label", { class: "tp-lbl" }, label), control);
}

// ── routing graph: clean card nodes + SVG connectors, click-to-connect ───────
function graphSection(st, rerender) {
  const sec = h("div", { class: "tp-card" });
  const hint = pendingSourceId
    ? "Click a destination to connect or disconnect, or the source again to cancel."
    : "Click a source, then a destination, to route events between them.";
  sec.appendChild(
    h(
      "div",
      { class: "tp-card-h" },
      h("h3", {}, "Routing graph"),
      h("span", { class: "tp-card-ha tp-muted", style: "font-size:12px;font-weight:400" }, hint),
    ),
  );

  const body = h("div", { class: "tp-card-b" });

  if (!(st.plan.sources || []).length && !(st.plan.destinations || []).length) {
    body.appendChild(h("div", { class: "tp-row-empty" }, "Add a source and a destination to route between them."));
    sec.appendChild(body);
    return sec;
  }

  const board = h("div", { class: "tp-graph" });

  // SVG overlay — createElementNS (h()/createElement produce invisible SVG).
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("class", "tp-graph-svg");
  board.appendChild(svg);

  const sCol = h("div", { class: "tp-graph-col tp-graph-sources" });
  (st.plan.sources || []).forEach((s) => {
    const node = h("div", {
      class:
        "tp-graph-node tp-graph-source" +
        (pendingSourceId === s.id ? " is-pending" : "") +
        (hoverNodeId === s.id ? " is-hover" : ""),
      dataset: { nodeId: s.id, side: "source" },
    });
    node.appendChild(h("span", { class: "tp-node-name" }, s.name));
    node.appendChild(h("span", { class: "tp-node-meta" }, s.platform_type || "—"));
    node.onclick = () => {
      pendingSourceId = pendingSourceId === s.id ? null : s.id;
      rerender();
    };
    node.onmouseenter = () => { hoverNodeId = s.id; highlight(board, st); };
    node.onmouseleave = () => { hoverNodeId = null; highlight(board, st); };
    sCol.appendChild(node);
  });

  const dCol = h("div", { class: "tp-graph-col tp-graph-dests" });
  (st.plan.destinations || []).forEach((dest) => {
    const node = h("div", {
      class: "tp-graph-node tp-graph-dest" + (hoverNodeId === dest.id ? " is-hover" : ""),
      dataset: { nodeId: dest.id, side: "dest" },
    });
    node.appendChild(h("span", { class: "tp-node-name" }, dest.name));
    node.appendChild(h("span", { class: "tp-node-meta" }, dest.platform || "—"));
    node.onclick = async () => {
      if (!pendingSourceId) return;
      const linked = isLinked(getState().plan, pendingSourceId, dest.id);
      const action = linked ? "disconnect_source_destination" : "connect_source_destination";
      const src = pendingSourceId;
      pendingSourceId = null;
      await persist(linked ? "Unrouted" : "Routed", () =>
        doAction(action, { source_id: src, destination_id: dest.id }, getState().branch),
      );
      await reload();
    };
    node.onmouseenter = () => { hoverNodeId = dest.id; highlight(board, st); };
    node.onmouseleave = () => { hoverNodeId = null; highlight(board, st); };
    dCol.appendChild(node);
  });

  board.appendChild(sCol);
  board.appendChild(dCol);
  body.appendChild(board);
  sec.appendChild(body);
  return sec;
}

/** Draw a cubic-bezier SVG path per routing link, centred on each node's vertical mid. */
function drawConnectors(detailEl, st) {
  const board = detailEl.querySelector(".tp-graph");
  if (!board) return;
  const svg = board.querySelector(".tp-graph-svg");
  if (!svg) return;
  const brect = board.getBoundingClientRect();
  svg.setAttribute("width", String(brect.width));
  svg.setAttribute("height", String(brect.height));
  // Clear existing connectors via SVG DOM (no innerHTML).
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const svgNS = "http://www.w3.org/2000/svg";
  const pos = (id) => {
    const el = board.querySelector(`[data-node-id="${id}"]`);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {
      side: el.dataset.side,
      x: el.dataset.side === "source" ? r.right - brect.left : r.left - brect.left,
      y: r.top - brect.top + r.height / 2,
    };
  };

  routingLinks(st.plan).forEach((l) => {
    const a = pos(l.sourceId);
    const b = pos(l.destinationId);
    if (!a || !b) return;
    const path = document.createElementNS(svgNS, "path");
    const midX = (a.x + b.x) / 2;
    path.setAttribute("d", `M ${a.x} ${a.y} C ${midX} ${a.y}, ${midX} ${b.y}, ${b.x} ${b.y}`);
    path.setAttribute("class", "tp-connector");
    path.dataset.src = l.sourceId;
    path.dataset.dest = l.destinationId;
    svg.appendChild(path);
  });
}

/** Toggle .is-dim on connectors not touching the hovered node, .is-lit on those that do. */
function highlight(board, st) {
  const svg = board.querySelector(".tp-graph-svg");
  if (!svg) return;
  svg.querySelectorAll(".tp-connector").forEach((p) => {
    const touches = !hoverNodeId || p.dataset.src === hoverNodeId || p.dataset.dest === hoverNodeId;
    p.classList.toggle("is-dim", !touches);
    p.classList.toggle("is-lit", !!hoverNodeId && touches);
  });
  const links = routingLinks(st.plan);
  board.querySelectorAll(".tp-graph-node").forEach((node) => {
    const nid = node.dataset.nodeId;
    if (!hoverNodeId) { node.classList.remove("is-hover"); return; }
    const connected =
      nid === hoverNodeId ||
      links.some(
        (l) =>
          (l.sourceId === hoverNodeId && l.destinationId === nid) ||
          (l.destinationId === hoverNodeId && l.sourceId === nid),
      );
    node.classList.toggle("is-hover", connected);
  });
}
