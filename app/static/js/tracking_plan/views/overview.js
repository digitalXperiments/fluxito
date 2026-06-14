// app/static/js/tracking_plan/views/overview.js
// Overview — the default dashboard. Read-only: it summarises the plan and links
// into the working views; all writes happen in the target view's own actions.
//
// Pattern (mirrors versions.js): mountView subscribes + paints synchronously,
// returns a cleanup fn. paint() builds the whole shell synchronously, then fires
// async fills (validation, activity) fire-and-forget so the page never blanks.
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

  // ---- Header: title + read-only quick actions ----------------------------
  wrap.appendChild(header(plan));

  // ---- KPI grid -----------------------------------------------------------
  const findingsCard = kpiFindings();
  wrap.appendChild(kpiGrid(plan, findingsCard));

  // ---- 2-col card grid ----------------------------------------------------
  const validationBody = h("div", { class: "tp-ov-card-body" }, h("div", { class: "tp-muted" }, "Checking…"));
  const activityBody = h("div", { class: "tp-ov-card-body" }, h("div", { class: "tp-muted" }, "Loading…"));

  wrap.appendChild(
    h(
      "div",
      { class: "tp-ov-grid" },
      ovCard("Validation", validationBody),
      ovCard("Recent activity", activityBody),
      ovCard("Routing", routingBody(plan)),
      ovCard("Branch & review", branchBody()),
    ),
  );

  mount(container, wrap);

  // ---- async fills (fire-and-forget; each catches its own errors) ---------
  fillValidation(validationBody, findingsCard, branch);
  fillActivity(activityBody, branch);
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function header(plan) {
  const quick = [
    ["+ Event", "events"],
    ["+ Property", "properties"],
    ["+ Source", "sources"],
    ["+ Metric", "metrics"],
  ].map(([label, view]) =>
    h("button", { class: "btn btn-sm", onClick: () => state.setView(view) }, label),
  );

  return h(
    "div",
    { class: "tp-ov-head" },
    h("div", { class: "tp-ov-head-main" }, h("h1", {}, plan.plan.name)),
    h("div", { class: "tp-ov-head-actions" }, ...quick),
  );
}

// ---------------------------------------------------------------------------
// KPI grid
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
    kpiCard("Metrics", (plan.metrics || []).length, "metrics"),
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
// Card scaffold
// ---------------------------------------------------------------------------

function ovCard(title, body) {
  return h("div", { class: "tp-ov-card" }, h("h3", {}, title), body);
}

// ---------------------------------------------------------------------------
// Validation card + Findings KPI (async)
// ---------------------------------------------------------------------------

async function fillValidation(body, findingsCard, branch) {
  let resp;
  try {
    resp = await api.validate(branch);
  } catch (e) {
    mountAll(body, [h("div", { class: "tp-empty" }, "Could not validate: " + (e.message || String(e)))]);
    if (findingsCard._value) findingsCard._value.textContent = "—";
    return;
  }

  const findings = resp.findings || [];
  if (findingsCard._value) findingsCard._value.textContent = String(findings.length);

  const nodes = [];

  // Publishable banner.
  nodes.push(
    h(
      "div",
      { class: "tp-ov-banner", dataset: { ok: resp.is_publishable ? "1" : "0" } },
      resp.is_publishable ? "✓ Publishable" : "⚠ Resolve warnings before publishing",
    ),
  );

  if (!findings.length) {
    nodes.push(h("div", { class: "tp-row-empty" }, "No findings — the plan looks complete."));
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
      nodes.push(h("div", { class: "tp-muted" }, `+${findings.length - 6} more`));
    }
  }

  nodes.push(
    h(
      "div",
      { class: "tp-ov-card-foot" },
      h("a", { class: "tp-link", href: "#", onClick: navTo("events") }, "Fix in Events"),
    ),
  );

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
    mountAll(body, [h("div", { class: "tp-empty" }, "Could not load activity: " + (e.message || String(e)))]);
    return;
  }

  const rows = resp.activity || resp.items || resp.events || [];
  if (!Array.isArray(rows) || !rows.length) {
    mountAll(body, [h("div", { class: "tp-row-empty" }, "No activity yet.")]);
    return;
  }

  const nodes = rows.slice(0, 8).map((a) =>
    h(
      "div",
      { class: "tp-ov-activity" },
      h("span", { class: "tp-avatar" }, initials(a.actor_id || "?")),
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
// Routing card (sync)
// ---------------------------------------------------------------------------

function routingBody(plan) {
  const sources = plan.sources || [];
  const destinations = plan.destinations || [];
  const body = h("div", { class: "tp-ov-card-body" });

  // Destinations actually routed-to by some source.
  const routed = new Set();
  sources.forEach((s) => (s.destinations || []).forEach((d) => routed.add(d)));

  if (!sources.length) {
    body.appendChild(h("div", { class: "tp-row-empty" }, "No sources yet."));
  } else {
    sources.forEach((s) => {
      const dests = s.destinations || [];
      const row = h(
        "div",
        { class: "tp-ov-route" },
        h("span", { class: "tp-ov-route-src" }, s.name),
        h("span", { class: "tp-ov-route-arrow" }, "→"),
      );
      if (!dests.length) {
        row.appendChild(h("span", { class: "tp-ov-alert" }, "unrouted"));
      } else {
        dests.forEach((d) => row.appendChild(h("span", { class: "tp-chip" }, d)));
      }
      body.appendChild(row);
    });
  }

  // Orphan destinations: defined but no source routes to them.
  const orphans = destinations.filter((d) => !routed.has(d.name));
  if (orphans.length) {
    const orow = h("div", { class: "tp-ov-route" }, h("span", { class: "tp-muted" }, "Orphan destinations:"));
    orphans.forEach((d) => orow.appendChild(h("span", { class: "tp-ov-alert" }, d.name)));
    body.appendChild(orow);
  }

  body.appendChild(
    h(
      "div",
      { class: "tp-ov-card-foot" },
      h("a", { class: "tp-link", href: "#", onClick: navTo("sources") }, "Manage"),
    ),
  );
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

  const body = h("div", { class: "tp-ov-card-body" });

  if (isMain) {
    body.appendChild(h("div", {}, "On main"));
    body.appendChild(
      h(
        "div",
        { class: "tp-ov-card-foot" },
        h("a", { class: "tp-link", href: "#", onClick: navTo("versions") }, "View versions"),
      ),
    );
  } else {
    const status = (branchObj && branchObj.review_status) || "draft";
    body.appendChild(
      h(
        "div",
        { class: "tp-ov-branch" },
        h("span", { class: "tp-mono" }, branchName),
        h("span", { class: "tp-review-pill", dataset: { s: status } }, titleCase(status)),
      ),
    );
    body.appendChild(
      h(
        "div",
        { class: "tp-ov-card-foot" },
        h("a", { class: "tp-link", href: "#", onClick: navTo("review") }, "Open branch review"),
      ),
    );
  }
  return body;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// An anchor click handler that navigates a view without following the href.
function navTo(view) {
  return (e) => {
    e.preventDefault();
    state.setView(view);
  };
}
