// app/static/js/tracking_plan/views/sources.js
// Sources & Destinations catalog + click-to-connect routing graph (spec §5.7).
import { getState, subscribe, reload } from "tp/state";
import { doAction } from "tp/api";
import { h, mount } from "tp/render";
import { routingLinks, isLinked } from "tp/util/routing";

const PLATFORM_TYPES = ["ios", "android", "web", "server", "warehouse"];

// transient (non-plan) UI state: a pending source selection in the graph + hover
let pendingSourceId = null;
let hoverNodeId = null;

export function mountView(container) {
  // Declare render before subscribe to avoid TDZ (bug pattern 1).
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    const root = h("div", { class: "tp-pane is-active" });
    const detail = h("div", { class: "tp-detail", style: "flex:1" });
    const inner = h("div", { class: "tp-detail-inner", style: "max-width:1040px" });
    inner.appendChild(catalog(st));
    inner.appendChild(graph(st, render));
    detail.appendChild(inner);
    root.appendChild(detail);
    mount(container, root);
    drawConnectors(detail, st);
  };

  // Capture unsub so mountView can return a cleanup (bug pattern 3).
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

function catalog(st) {
  const sec = h("div", { class: "tp-section" });
  sec.appendChild(h("h3", {}, "Catalog"));
  // 2nd arg to h('div', ...) is a plain attrs object — never a node (bug pattern 2).
  const cols = h("div", { class: "tp-catalog" });

  // ── sources column ────────────────────────────────────────────────────
  const sCol = h("div", { class: "tp-catalog-col" });
  sCol.appendChild(h("div", { class: "tp-catalog-head" }, "Sources"));
  (st.plan.sources || []).forEach((s) => {
    const row = h("div", { class: "tp-catalog-row" });
    const nm = h("input", { class: "tp-cat-name", value: s.name });
    const ty = h("select", {});
    ty.appendChild(h("option", { value: "" }, "platform…"));
    PLATFORM_TYPES.forEach((t) => {
      const o = h("option", { value: t }, t);
      if (s.platform_type === t) o.selected = true;
      ty.appendChild(o);
    });
    const save = h("button", { class: "btn btn-ghost btn-sm" }, "Save");
    save.onclick = async () => {
      await doAction(
        "update_source",
        { source_id: s.id, name: nm.value.trim(), platform_type: ty.value || null },
        st.branch,
      );
      await reload();
    };
    const del = h("button", { class: "btn btn-ghost btn-sm" }, "Delete");
    del.onclick = async () => {
      await doAction("delete_source", { source_id: s.id }, st.branch);
      await reload();
    };
    row.appendChild(nm);
    row.appendChild(ty);
    row.appendChild(save);
    row.appendChild(del);
    sCol.appendChild(row);
  });
  const addS = h("button", { class: "btn btn-secondary btn-sm" }, "+ Add source");
  addS.onclick = async () => {
    await doAction("create_source", { name: "New source", platform_type: "web" }, st.branch);
    await reload();
  };
  sCol.appendChild(addS);
  cols.appendChild(sCol);

  // ── destinations column ───────────────────────────────────────────────
  const dCol = h("div", { class: "tp-catalog-col" });
  dCol.appendChild(h("div", { class: "tp-catalog-head" }, "Destinations"));
  (st.plan.destinations || []).forEach((dest) => {
    const row = h("div", { class: "tp-catalog-row" });
    const nm = h("input", { class: "tp-cat-name", value: dest.name });
    const pl = h("input", {
      class: "tp-mono-input",
      value: dest.platform || "",
      placeholder: "platform",
      style: "width:110px",
    });
    const acct = h("input", {
      value: dest.platform_account_id || "",
      placeholder: "account id",
      style: "width:120px",
    });
    const save = h("button", { class: "btn btn-ghost btn-sm" }, "Save");
    save.onclick = async () => {
      await doAction(
        "update_destination",
        {
          destination_id: dest.id,
          name: nm.value.trim(),
          platform: pl.value.trim(),
          platform_account_id: acct.value || null,
        },
        st.branch,
      );
      await reload();
    };
    const del = h("button", { class: "btn btn-ghost btn-sm" }, "Delete");
    del.onclick = async () => {
      await doAction("delete_destination", { destination_id: dest.id }, st.branch);
      await reload();
    };
    row.appendChild(nm);
    row.appendChild(pl);
    row.appendChild(acct);
    row.appendChild(save);
    row.appendChild(del);
    dCol.appendChild(row);
  });
  const addD = h("button", { class: "btn btn-secondary btn-sm" }, "+ Add destination");
  addD.onclick = async () => {
    await doAction("create_destination", { name: "New destination", platform: "ga4" }, st.branch);
    await reload();
  };
  dCol.appendChild(addD);
  cols.appendChild(dCol);

  sec.appendChild(cols);
  return sec;
}

function graph(st, rerender) {
  const sec = h("div", { class: "tp-section" });
  sec.appendChild(h("h3", {}, "Routing graph"));
  const hint = h(
    "div",
    { class: "tp-muted", style: "font-size:12px;margin-bottom:10px" },
    pendingSourceId
      ? "Click a destination to connect/disconnect, or the source again to cancel."
      : "Click a source, then a destination, to route events between them.",
  );
  sec.appendChild(hint);

  const board = h("div", { class: "tp-graph" });

  // SVG overlay for connectors — must be createElementNS, not createElement / h().
  // h() uses document.createElement which produces an HTMLElement, not SVGElement;
  // SVG shape elements created that way are invisible. We inject the SVG overlay
  // directly here and let drawConnectors() populate it after layout.
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
    });
    node.dataset.nodeId = s.id;
    node.dataset.side = "source";
    // Use h() children for safe text insertion — no innerHTML (bug pattern 4).
    node.appendChild(h("span", { class: "tp-node-name" }, s.name));
    node.appendChild(h("span", { class: "tp-node-meta" }, s.platform_type || "—"));
    node.onclick = () => {
      pendingSourceId = pendingSourceId === s.id ? null : s.id;
      rerender();
    };
    node.onmouseenter = () => {
      hoverNodeId = s.id;
      highlight(board, st);
    };
    node.onmouseleave = () => {
      hoverNodeId = null;
      highlight(board, st);
    };
    sCol.appendChild(node);
  });

  const dCol = h("div", { class: "tp-graph-col tp-graph-dests" });
  (st.plan.destinations || []).forEach((dest) => {
    const node = h("div", {
      class: "tp-graph-node tp-graph-dest" + (hoverNodeId === dest.id ? " is-hover" : ""),
    });
    node.dataset.nodeId = dest.id;
    node.dataset.side = "dest";
    node.appendChild(h("span", { class: "tp-node-name" }, dest.name));
    node.appendChild(h("span", { class: "tp-node-meta" }, dest.platform || "—"));
    node.onclick = async () => {
      if (!pendingSourceId) return;
      // isLinked resolves name→id via util/routing internally.
      const linked = isLinked(getState().plan, pendingSourceId, dest.id);
      const action = linked ? "disconnect_source_destination" : "connect_source_destination";
      const src = pendingSourceId;
      pendingSourceId = null;
      await doAction(action, { source_id: src, destination_id: dest.id }, getState().branch);
      await reload();
    };
    node.onmouseenter = () => {
      hoverNodeId = dest.id;
      highlight(board, st);
    };
    node.onmouseleave = () => {
      hoverNodeId = null;
      highlight(board, st);
    };
    dCol.appendChild(node);
  });

  board.appendChild(sCol);
  board.appendChild(dCol);
  sec.appendChild(board);
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
  // Clear existing connectors safely via SVG DOM (no innerHTML to avoid XSS).
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
    // Connector path — createElementNS ensures a real SVGPathElement.
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
  // Also highlight connected nodes when hovering.
  const links = routingLinks(st.plan);
  board.querySelectorAll(".tp-graph-node").forEach((node) => {
    const nid = node.dataset.nodeId;
    if (!hoverNodeId) {
      node.classList.remove("is-hover");
      return;
    }
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
