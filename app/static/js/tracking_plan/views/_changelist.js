// app/static/js/tracking_plan/views/_changelist.js
// Shared grouped-diff renderer for Branch review (§5.10) AND Versions compare
// (§5.11). Input is the output of tp/util/diff.groupDiff(diffResp):
//   [{ group, changes: [{ marker, entityType, name, id, fields:[{key, was, now}] }] }]
// opts:
//   summary        : { added, changed, removed } -> renders the summary bar
//   commentCounts  : Map/obj entityKey -> N      -> badge on a row (entityKey = `${entityType}:${id}`)
//   onToggleInline : (change, rowEl, bodyEl) => void  -> called when a row expands,
//                    so review can mount inline comments under it (versions omits this)
//
// Design system: refined to the approved mockup. Each change row is a hairline
// card row (.tp-diff-item) with a colored marker chip (.tp-diff-mark add/chg/rem),
// a mono identifier name, an optional comment badge, and a rotate-on-open chevron.
// Field-level before/after renders as the .tp-fielddiff table.
import { h } from "tp/render";

const MARK_CLASS = { "+": "add", "~": "chg", "-": "rem" };
const MARK_GLYPH = { "+": "+", "~": "~", "-": "−" };

export function entityKey(c) {
  return `${c.entityType}:${c.id || c.name}`;
}

// Small inline-SVG speech bubble for the comment badge (no emoji — sans system).
function commentIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("width", "12");
  svg.setAttribute("height", "12");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M21 11.5a8.4 8.4 0 0 1-12 7.6L3 21l1.9-5.7A8.4 8.4 0 1 1 21 11.5z");
  svg.appendChild(path);
  return svg;
}

// Chevron used on expandable rows (rotates via .is-open on the parent item).
function chevronIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.2");
  svg.setAttribute("width", "13");
  svg.setAttribute("height", "13");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M9 6l6 6-6 6");
  svg.appendChild(path);
  return svg;
}

function summaryBar(s, breakdown) {
  const bar = h("div", { class: "tp-diff-summary" },
    h("span", { class: "tp-diff-stat add" }, `+${s.added} added`),
    h("span", { class: "tp-diff-stat chg" }, `~${s.changed} changed`),
    h("span", { class: "tp-diff-stat rem" }, `−${s.removed} removed`),
  );
  if (breakdown && breakdown.length) {
    bar.appendChild(h("span", { class: "tp-diff-breakdown" }, breakdown.join(" · ")));
  }
  return bar;
}

function fieldVal(v) {
  // Coerce field before/after values to display strings.
  // was/now from fieldDiff are always strings (scalarish), but legacy shape can
  // produce undefined — guard against that so String() never yields "undefined".
  if (v == null || v === "") return "∅"; // ∅
  return String(v);
}

function fieldTable(fields) {
  const rows = (fields || []).map((f) =>
    h("tr", {},
      h("td", { class: "tp-fd-key" }, String(f.key)),
      h("td", { class: "tp-fd-was" }, fieldVal(f.was)),
      h("td", { class: "tp-fd-arrow" }, "→"),
      h("td", { class: "tp-fd-now" }, fieldVal(f.now)),
    ));
  if (!rows.length) return h("div", { class: "tp-muted tp-fd-empty" }, "No field-level changes.");
  return h("table", { class: "tp-fielddiff" },
    h("thead", {}, h("tr", {}, h("th", {}, "Field"), h("th", {}, "Before"), h("th", {}, ""), h("th", {}, "After"))),
    h("tbody", {}, ...rows));
}

function changeRow(c, opts) {
  const cls = MARK_CLASS[c.marker] || "chg";
  const expandable = c.marker === "~" && (c.fields && c.fields.length);
  const count = opts.commentCounts ? (opts.commentCounts[entityKey(c)] || 0) : 0;

  const header = h("div", { class: "tp-diff-item" + (expandable ? " is-expandable" : "") },
    h("span", { class: "tp-diff-mark " + cls }, MARK_GLYPH[c.marker] || c.marker),
    h("span", { class: "tp-diff-name" }, String(c.name)),
    count
      ? h("span", { class: "tp-diff-comments", title: `${count} comment${count === 1 ? "" : "s"}` },
          commentIcon(), String(count))
      : null,
    expandable ? h("span", { class: "tp-diff-chevron" }, chevronIcon()) : null,
  );

  const body = h("div", { class: "tp-diff-body" });
  let open = false;
  if (expandable) {
    const expand = () => {
      open = !open;
      header.classList.toggle("is-open", open);
      body.style.display = open ? "block" : "none";
      if (open && !body.dataset.built) {
        body.appendChild(fieldTable(c.fields));
        if (opts.onToggleInline) opts.onToggleInline(c, header, body); // review mounts inline comments
        body.dataset.built = "1";
      }
    };
    body.style.display = "none";
    header.addEventListener("click", expand);
  }

  return h("div", { class: "tp-diff-rowwrap" }, header, body);
}

export function renderChangeList(grouped, opts = {}) {
  const root = h("div", { class: "tp-diff" });
  if (opts.summary) {
    const breakdown = grouped.filter((g) => g.changes.length).map((g) => `${g.changes.length} ${g.group.toLowerCase()}`);
    root.appendChild(summaryBar(opts.summary, breakdown));
  }
  const nonEmpty = grouped.filter((g) => g.changes.length);
  if (!nonEmpty.length) {
    root.appendChild(h("div", { class: "tp-empty" }, "No differences."));
    return root;
  }
  for (const g of nonEmpty) {
    const grp = h("div", { class: "tp-diff-group" }, h("h3", {}, String(g.group)));
    for (const c of g.changes) grp.appendChild(changeRow(c, opts));
    root.appendChild(grp);
  }
  return root;
}
