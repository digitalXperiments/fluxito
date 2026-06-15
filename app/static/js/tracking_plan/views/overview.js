// app/static/js/tracking_plan/views/overview.js
// Overview — the default dashboard. Read-only: it summarises the plan and links
// into the working views; all writes happen in the target view's own actions.
// There is no buffered save here (no edits) — just the refined design system.
//
// Pattern (mirrors versions.js): mountView subscribes + paints synchronously,
// returns a cleanup fn. paint() builds the whole shell synchronously, then fires
// async fills (validation, activity) fire-and-forget so the page never blanks.
//
// Design: matches the approved mockup (/tmp/tp_redesign_mockup.html). Sentence-case
// Inter Tight for everything human; JetBrains Mono ONLY for identifiers (the plan
// name, branch name, source/destination names). Body sections are .tp-card with
// .tp-card-h headers + .tp-card-b bodies; KPIs are the restyled .tp-kpi cards.
import { h, mount, mountAll } from "tp/render";
import * as state from "tp/state";
import * as api from "tp/api";
import { eventStatus, initials, relativeTime, titleCase } from "tp/util/format";

export function mountView(container) {
  const unsub = state.subscribe(() => paint(container));
  paint(container);
  return () => {
    unsub();
  };
}

function paint(container) {
  const st = state.getState();

  // Guard: no plan loaded yet — show a resting empty state, don't blank.
  if (!st.plan) {
    mount(container, h("div", { class: "tp-empty" }, h("div", {}, "Loading…")));
    return;
  }

  const plan = st.plan;
  const branch = st.branch || "main";

  const wrap = h("div", { class: "tp-overview" });

  // ---- Header: plan name + read-only quick actions ------------------------
  wrap.appendChild(header(plan));

  // ---- KPI grid -----------------------------------------------------------
  const findingsCard = kpiFindings();
  wrap.appendChild(kpiGrid(plan, findingsCard));

  // ---- 2-col card grid ----------------------------------------------------
  const validationBody = h("div", { class: "tp-card-b" }, h("div", { class: "tp-muted" }, "Checking…"));
  const activityBody = h("div", { class: "tp-card-b" }, h("div", { class: "tp-muted" }, "Loading…"));

  wrap.appendChild(
    h(
      "div",
      { class: "tp-ov-grid" },
      ovCard("Validation", validationBody),
      ovCard("Recent activity", activityBody),
      ovCard("Routing", routingBody(plan), { count: (plan.sources || []).length + " sources" }),
      ovCard("Branch & review", branchBody()),
    ),
  );

  mount(container, wrap);

  // ---- async fills (fire-and-forget; each catches its own errors) ---------
  fillValidation(validationBody, findingsCard, branch);
  fillActivity(activityBody, branch);
}

// ---------------------------------------------------------------------------
// Header — plan name (mono identifier) + refined quick-action buttons
// ---------------------------------------------------------------------------

function header(plan) {
  const quick = [
    ["New event", "events"],
    ["New property", "properties"],
    ["New source", "sources"],
  ].map(([label, view]) =>
    h(
      "button",
      { class: "btn btn-secondary btn-sm", onClick: () => state.setView(view) },
      plusIcon(),
      label,
    ),
  );

  return h(
    "div",
    { class: "tp-ov-head" },
    h(
      "div",
      { class: "tp-ov-head-main" },
      h("div", { class: "tp-ed-kicker" }, "Tracking plan"),
      h("h1", { class: "tp-mono" }, plan.plan.name),
    ),
    h("div", { class: "tp-ov-head-actions" }, ...quick),
  );
}

// ---------------------------------------------------------------------------
// KPI grid — restyled .tp-kpi cards (big number, small muted label, hover)
// ---------------------------------------------------------------------------

function propCount(plan) {
  const p = plan.properties || {};
  return (
    (p.event || []).length +
    (p.user || []).length +
    (p.group || []).length +
    (p.system || []).length
  );
}

function kpiCard(label, value, view, extra) {
  const card = h(
    "div",
    { class: "tp-kpi", onClick: () => state.setView(view), role: "button", tabindex: "0" },
    h("div", { class: "tp-kpi-value" }, String(value)),
    h("div", { class: "tp-kpi-label" }, label),
  );
  if (extra) card.appendChild(extra);
  return card;
}

function kpiGrid(plan, findingsCard) {
  const events = plan.events || [];

  // Implementation-status tally over events (verified / implemented / planned).
  const tally = { verified: 0, implemented: 0, planned: 0 };
  events.forEach((ev) => {
    tally[eventStatus(ev)] += 1;
  });
  const total = events.length || 1;
  const bar = h(
    "div",
    { class: "tp-kpi-bar" },
    seg("verified", tally.verified, total),
    seg("implemented", tally.implemented, total),
    seg("planned", tally.planned, total),
  );
  const implCard = h(
    "div",
    { class: "tp-kpi", onClick: () => state.setView("events"), role: "button", tabindex: "0" },
    h("div", { class: "tp-kpi-label" }, "Implementation"),
    bar,
    h(
      "div",
      { class: "tp-kpi-legend" },
      `${tally.verified} verified · ${tally.implemented} implemented · ${tally.planned} planned`,
    ),
  );

  return h(
    "div",
    { class: "tp-ov-kpis" },
    kpiCard("Events", events.length, "events"),
    kpiCard("Properties", propCount(plan), "properties"),
    kpiCard("Sources", (plan.sources || []).length, "sources"),
    kpiCard("Destinations", (plan.destinations || []).length, "sources"),
    kpiCard("Metrics", (plan.metrics || []).length, "events"),
    implCard,
    findingsCard,
  );
}

function seg(kind, count, total) {
  const pct = total ? Math.round((count / total) * 100) : 0;
  return h("span", {
    class: "tp-kpi-seg",
    dataset: { s: kind },
    style: { width: pct + "%" },
    title: `${count} ${kind}`,
  });
}

// Findings KPI: starts "—", value filled by the async validation pass.
function kpiFindings() {
  const value = h("div", { class: "tp-kpi-value" }, "—");
  const card = h(
    "div",
    { class: "tp-kpi", onClick: () => state.setView("events"), role: "button", tabindex: "0" },
    value,
    h("div", { class: "tp-kpi-label" }, "Findings"),
  );
  card._value = value; // handle for the async fill
  return card;
}

// ---------------------------------------------------------------------------
// Card scaffold — the .tp-card primitive (mockup): header + body
// ---------------------------------------------------------------------------
//   title — sentence-case sans heading
//   body  — a .tp-card-b node (filled sync or async)
//   opts.count   — optional count pill (.tp-ct) after the title
//   opts.actions — optional array of action nodes pinned right (.tp-card-ha)
function ovCard(title, body, opts = {}) {
  const head = h("div", { class: "tp-card-h" }, h("h3", {}, title));
  if (opts.count != null) head.appendChild(h("span", { class: "tp-ct" }, String(opts.count)));
  const actions = (opts.actions || []).filter(Boolean);
  if (actions.length) head.appendChild(h("div", { class: "tp-card-ha" }, ...actions));
  return h("div", { class: "tp-card" }, head, body);
}

// ---------------------------------------------------------------------------
// Validation card + Findings KPI (async)
// ---------------------------------------------------------------------------

async function fillValidation(body, findingsCard, branch) {
  let resp;
  try {
    resp = await api.validate(branch);
  } catch (e) {
    mountAll(body, [h("div", { class: "tp-row-empty" }, "Could not validate: " + (e.message || String(e)))]);
    if (findingsCard._value) findingsCard._value.textContent = "—";
    return;
  }

  const findings = resp.findings || [];
  if (findingsCard._value) findingsCard._value.textContent = String(findings.length);

  const nodes = [];

  // Publishable banner — reuse the fully-styled .tp-banner ok/warn variants.
  nodes.push(
    h(
      "div",
      { class: "tp-banner " + (resp.is_publishable ? "ok" : "warn"), style: { margin: "-18px -18px 14px", borderRadius: "0" } },
      resp.is_publishable ? "✓ Publishable" : "⚠ Resolve warnings before publishing",
    ),
  );

  if (!findings.length) {
    nodes.push(h("div", { class: "tp-muted", style: { marginTop: "10px" } }, "No findings — the plan looks complete."));
  } else {
    findings.slice(0, 6).forEach((f) => {
      nodes.push(
        h(
          "div",
          { class: "tp-ov-finding" },
          h("span", { class: "tp-ov-sev", dataset: { s: f.severity || "info" } }, f.severity || "info"),
          h("span", { class: "tp-ov-finding-msg" }, String(f.message || f.code || "")),
        ),
      );
    });
    if (findings.length > 6) {
      nodes.push(h("div", { class: "tp-muted", style: { marginTop: "8px" } }, `+${findings.length - 6} more`));
    }
  }

  nodes.push(footLink("Fix in events", "events"));

  mountAll(body, nodes);
}

// ---------------------------------------------------------------------------
// Recent activity card (async)
// ---------------------------------------------------------------------------

async function fillActivity(body, branch) {
  let resp;
  try {
    resp = await api.listActivity(null, null, branch);
  } catch (e) {
    mountAll(body, [h("div", { class: "tp-row-empty" }, "Could not load activity: " + (e.message || String(e)))]);
    return;
  }

  const rows = resp.activity || resp.items || resp.events || [];
  if (!Array.isArray(rows) || !rows.length) {
    mountAll(body, [h("div", { class: "tp-muted" }, "No activity yet.")]);
    return;
  }

  const nodes = rows.slice(0, 8).map((a) =>
    h(
      "div",
      { class: "tp-ov-activity" },
      h("span", { class: "tp-avatar tp-avatar-sm" }, initials(a.actor_id || "?")),
      h(
        "div",
        { class: "tp-ov-activity-main" },
        h("div", { class: "tp-ov-activity-summary" }, String(a.summary || a.action || "")),
        h("div", { class: "tp-ov-activity-when" }, relativeTime(a.created_at)),
      ),
    ),
  );
  mountAll(body, nodes);
}

// ---------------------------------------------------------------------------
// Routing card (sync) — source → destinations, with unrouted/orphan alerts
// ---------------------------------------------------------------------------

function routingBody(plan) {
  const sources = plan.sources || [];
  const destinations = plan.destinations || [];
  const body = h("div", { class: "tp-card-b" });

  // Destinations actually routed-to by some source.
  const routed = new Set();
  sources.forEach((s) => (s.destinations || []).forEach((d) => routed.add(d)));

  if (!sources.length) {
    body.appendChild(h("div", { class: "tp-muted" }, "No sources yet."));
  } else {
    sources.forEach((s) => {
      const dests = s.destinations || [];
      const row = h(
        "div",
        { class: "tp-ov-route" },
        h("span", { class: "tp-mono tp-ov-route-src" }, s.name),
        h("span", { class: "tp-ov-route-arrow" }, "→"),
      );
      if (!dests.length) {
        row.appendChild(h("span", { class: "tp-ov-alert" }, "unrouted"));
      } else {
        dests.forEach((d) => row.appendChild(h("span", { class: "tp-chip mono" }, d)));
      }
      body.appendChild(row);
    });
  }

  // Orphan destinations: defined but no source routes to them.
  const orphans = destinations.filter((d) => !routed.has(d.name));
  if (orphans.length) {
    const orow = h("div", { class: "tp-ov-route" }, h("span", { class: "tp-muted" }, "Orphan destinations:"));
    orphans.forEach((d) => orow.appendChild(h("span", { class: "tp-ov-alert tp-mono" }, d.name)));
    body.appendChild(orow);
  }

  body.appendChild(footLink("Manage routing", "sources"));
  return body;
}

// ---------------------------------------------------------------------------
// Branch & review card (sync)
// ---------------------------------------------------------------------------

function branchBody() {
  const st = state.getState();
  const branchName = st.branch || "main";
  const branchObj = (st.branches || []).find((b) => b.name === branchName) || null;
  const isMain = branchName === "main" || (branchObj && branchObj.is_main);

  const body = h("div", { class: "tp-card-b" });

  if (isMain) {
    body.appendChild(
      h(
        "div",
        { class: "tp-ov-route" },
        h("span", { class: "tp-mono tp-ov-branch" }, branchName),
        h("span", { class: "tp-chip accent" }, "Main"),
      ),
    );
    body.appendChild(footLink("View versions", "versions"));
  } else {
    const status = (branchObj && branchObj.review_status) || "draft";
    body.appendChild(
      h(
        "div",
        { class: "tp-ov-route" },
        h("span", { class: "tp-mono tp-ov-branch" }, branchName),
        h("span", { class: "tp-review-pill", dataset: { s: status } }, titleCase(status)),
      ),
    );
    body.appendChild(footLink("Open branch review", "review"));
  }
  return body;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// A card-foot link row that navigates a view without following the href.
function footLink(label, view) {
  return h(
    "div",
    { class: "tp-ov-card-foot" },
    h("a", { class: "tp-link", href: "#", onClick: navTo(view) }, label),
  );
}

// An anchor click handler that navigates a view without following the href.
function navTo(view) {
  return (e) => {
    e.preventDefault();
    state.setView(view);
  };
}

// Plus glyph for the quick-action buttons (sans context — decorative, not an id).
function plusIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2.2");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "M12 5v14M5 12h14");
  svg.appendChild(path);
  return svg;
}
