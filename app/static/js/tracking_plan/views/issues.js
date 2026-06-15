// app/static/js/tracking_plan/views/issues.js
// Issues view: two internal sub-tabs — Issues (validation findings grouped by
// severity, each row deep-links into the offending event or property) and Rules
// (toggle/configure each validation rule, persist via set_rule_enabled /
// update_rule). The view always fetches a fresh /validate response on mount and
// after every rule toggle/update so finding counts stay live.
//
// Pattern: mountView(container) → cleanup fn, mirroring metrics.js/overview.js.
// Writes go through persist() so the banner and error handling are consistent.

import { getState, subscribe, select, setView } from "tp/state";
import { doAction, validate as apiValidate } from "tp/api";
import { h, mountAll } from "tp/render";
import { persist } from "tp/util/persist";
import { titleCase } from "tp/util/format";

// ---- severity ordering + color keys -----------------------------------------

const SEV_ORDER = ["error", "warning", "info"];

function sevClass(sev) {
  if (sev === "error") return "red";
  if (sev === "warning") return "amber";
  return "sky";
}

// ---- rule-type human labels -------------------------------------------------

const RULE_LABELS = {
  event_name_casing: "Event name casing",
  event_name_regex: "Event name pattern",
  event_requires_description: "Event requires description",
  event_requires_owner: "Event requires owner",
  required_property: "Required property",
  property_type_consistency: "Property type consistency",
  pii_must_be_flagged: "PII must be flagged",
};

function ruleLabel(ruleType) {
  return RULE_LABELS[ruleType] || titleCase(ruleType);
}

// ---- mountView --------------------------------------------------------------

export function mountView(container) {
  // Local view state
  let report = null;        // last /validate response
  let tab = "issues";       // 'issues' | 'rules'
  let loading = false;
  let filterSev = "";
  let filterRule = "";
  let filterCat = "";

  // Track last branch so we refresh when the user switches branch.
  let lastBranch = getState().branch;

  async function refresh() {
    loading = true;
    render();
    try {
      report = await apiValidate(getState().branch);
    } catch (e) {
      report = null;
    }
    loading = false;
    render();
  }

  // Subscribe: re-render on state changes; refresh when branch changes.
  const unsub = subscribe(() => {
    const b = getState().branch;
    if (b !== lastBranch) {
      lastBranch = b;
      filterSev = "";
      filterRule = "";
      filterCat = "";
      report = null;
      refresh();
      return;
    }
    render();
  });

  // ---- render -----------------------------------------------------------------

  function render() {
    const inner = h("div", { class: "tp-issues-wrap" });

    // Sub-tab bar
    inner.appendChild(buildSubtabs());

    if (loading) {
      inner.appendChild(h("div", { class: "tp-empty" }, h("div", {}, "Validating…")));
      mountAll(container, [inner]);
      return;
    }

    if (!report) {
      inner.appendChild(
        h("div", { class: "tp-empty" },
          h("div", {}, "Could not load validation results."),
        ),
      );
      mountAll(container, [inner]);
      return;
    }

    if (tab === "issues") {
      inner.appendChild(buildIssuesTab());
    } else {
      inner.appendChild(buildRulesTab());
    }

    mountAll(container, [inner]);
  }

  // ---- sub-tabs ---------------------------------------------------------------

  function buildSubtabs() {
    const findings = (report && report.findings) || [];
    const errCount = findings.filter((f) => f.severity === "error").length;
    const warnCount = findings.filter((f) => f.severity === "warning").length;
    const infoCount = findings.filter((f) => f.severity === "info").length;
    const totalCount = findings.length;

    const issueLabel =
      report
        ? `Issues (${totalCount}${errCount ? " · " + errCount + " error" + (errCount !== 1 ? "s" : "") : ""}${warnCount ? " · " + warnCount + " warning" + (warnCount !== 1 ? "s" : "") : ""}${infoCount && !errCount && !warnCount ? " · " + infoCount + " info" : ""})`
        : "Issues";

    const rulesCount = (report && report.rules) ? report.rules.length : "";
    const rulesLabel = rulesCount ? `Rules (${rulesCount})` : "Rules";

    return h(
      "div",
      { class: "tp-issues-subtabs" },
      h(
        "button",
        {
          class: "tp-issues-subtab" + (tab === "issues" ? " is-active" : ""),
          onClick: () => { tab = "issues"; render(); },
        },
        issueLabel,
      ),
      h(
        "button",
        {
          class: "tp-issues-subtab" + (tab === "rules" ? " is-active" : ""),
          onClick: () => { tab = "rules"; render(); },
        },
        rulesLabel,
      ),
      h(
        "button",
        {
          class: "btn btn-ghost btn-sm tp-issues-refresh",
          onClick: () => refresh(),
          title: "Re-run validation",
        },
        "↻ Refresh",
      ),
    );
  }

  // ---- Issues tab -------------------------------------------------------------

  function buildIssuesTab() {
    const findings = (report && report.findings) || [];
    const rules = (report && report.rules) || [];
    const plan = getState().plan || {};
    const categories = plan.categories || [];

    const wrap = h("div", { class: "tp-issues-tab" });

    // Publishable banner
    const pub = report.is_publishable;
    wrap.appendChild(
      h(
        "div",
        {
          class: "tp-banner " + (pub ? "ok" : (findings.some((f) => f.severity === "error") ? "err" : "warn")),
          style: { margin: "0 0 0 0", borderRadius: "0", borderLeft: "none", borderRight: "none", borderTop: "none" },
        },
        pub ? "✓ Publishable — no blocking errors" : (findings.some((f) => f.severity === "error") ? "✗ Resolve errors before publishing" : "⚠ Warnings present — plan can still be published"),
      ),
    );

    // Filter bar
    if (findings.length) {
      wrap.appendChild(buildIssueFilters(findings, rules, categories));
    }

    if (!findings.length) {
      wrap.appendChild(
        h(
          "div",
          { class: "tp-empty" },
          h("div", {}, "✓ No findings — the plan looks complete."),
        ),
      );
      return wrap;
    }

    // Apply filters
    let filtered = findings;
    if (filterSev) filtered = filtered.filter((f) => f.severity === filterSev);
    if (filterRule) filtered = filtered.filter((f) => f.rule_id === filterRule);
    if (filterCat) {
      filtered = filtered.filter((f) => {
        // find the event for this finding and check its category
        const ev = (f.entity_type === "event") ? findEventById(plan, f.entity_id) : null;
        return ev && ev.category === filterCat;
      });
    }

    if (!filtered.length) {
      wrap.appendChild(
        h("div", { class: "tp-empty" }, h("div", {}, "No findings match the current filters.")),
      );
      return wrap;
    }

    // Group by severity in order
    const byGroup = {};
    for (const sev of SEV_ORDER) byGroup[sev] = [];
    for (const f of filtered) {
      const s = f.severity || "info";
      if (!byGroup[s]) byGroup[s] = [];
      byGroup[s].push(f);
    }

    const list = h("div", { class: "tp-issues-list" });
    for (const sev of SEV_ORDER) {
      const group = byGroup[sev];
      if (!group.length) continue;

      const ruleLookup = (ruleId) => {
        const r = rules.find((x) => x.id === ruleId);
        return r ? ruleLabel(r.rule_type) : null;
      };

      list.appendChild(
        h(
          "div",
          { class: "tp-issues-group" },
          h(
            "div",
            { class: "tp-issues-group-head" },
            h("span", { class: "tp-badge " + sevClass(sev) }, sev),
            h("span", { class: "tp-issues-group-count" }, group.length + " finding" + (group.length !== 1 ? "s" : "")),
          ),
          ...group.map((f) => buildFindingRow(f, ruleLookup)),
        ),
      );
    }
    wrap.appendChild(list);
    return wrap;
  }

  function buildIssueFilters(findings, rules, categories) {
    // Collect distinct rule IDs that appear in findings
    const ruleIds = [...new Set(findings.map((f) => f.rule_id).filter(Boolean))];
    const catNames = [...new Set(findings
      .filter((f) => f.entity_type === "event")
      .map((f) => {
        const ev = findEventById(getState().plan || {}, f.entity_id);
        return ev && ev.category ? ev.category : null;
      })
      .filter(Boolean))];

    const sevSel = h("select", { class: "select tp-issues-filter-sel" });
    sevSel.appendChild(h("option", { value: "" }, "All severities"));
    for (const s of SEV_ORDER) {
      sevSel.appendChild(h("option", { value: s, selected: filterSev === s }, titleCase(s)));
    }
    sevSel.onchange = () => { filterSev = sevSel.value; render(); };

    const ruleSel = h("select", { class: "select tp-issues-filter-sel" });
    ruleSel.appendChild(h("option", { value: "" }, "All rules"));
    for (const rId of ruleIds) {
      const r = rules.find((x) => x.id === rId);
      const label = r ? ruleLabel(r.rule_type) : rId;
      ruleSel.appendChild(h("option", { value: rId, selected: filterRule === rId }, label));
    }
    ruleSel.onchange = () => { filterRule = ruleSel.value; render(); };

    const catSel = h("select", { class: "select tp-issues-filter-sel" });
    catSel.appendChild(h("option", { value: "" }, "All categories"));
    for (const c of catNames) {
      catSel.appendChild(h("option", { value: c, selected: filterCat === c }, c));
    }
    catSel.onchange = () => { filterCat = catSel.value; render(); };

    return h("div", { class: "tp-issues-filters" }, sevSel, ruleSel, catSel);
  }

  function buildFindingRow(f, ruleLookup) {
    const canLink = f.entity_type && f.entity_id;
    const rLabel = f.rule_id ? ruleLookup(f.rule_id) : null;

    const row = h(
      "div",
      { class: "tp-issue-row" + (canLink ? " tp-issue-row-link" : "") },
      h("span", { class: "tp-badge " + sevClass(f.severity || "info") }, f.severity || "info"),
      h(
        "div",
        { class: "tp-issue-body" },
        h("div", { class: "tp-issue-msg" }, String(f.message || f.code || "")),
        rLabel
          ? h("div", { class: "tp-issue-rule" }, rLabel)
          : null,
        f.suggested_fix
          ? h("div", { class: "tp-issue-fix" }, "Fix: " + f.suggested_fix)
          : null,
      ),
      canLink
        ? h(
            "span",
            { class: "tp-issue-link-arrow tp-muted" },
            "→",
          )
        : null,
    );

    if (canLink) {
      row.onclick = () => openEntity(f);
    }

    return row;
  }

  function openEntity(f) {
    if (f.entity_type === "event") {
      setView("events");
      setTimeout(() => select("event", f.entity_id), 0);
    } else if (f.entity_type === "property") {
      setView("properties");
      setTimeout(() => select("property", f.entity_id), 0);
    }
  }

  // ---- Rules tab --------------------------------------------------------------

  function buildRulesTab() {
    const rules = (report && report.rules) || [];
    const plan = getState().plan || {};

    const wrap = h("div", { class: "tp-issues-tab" });

    if (!rules.length) {
      wrap.appendChild(
        h("div", { class: "tp-empty" }, h("div", {}, "No rules configured for this plan.")),
      );
      return wrap;
    }

    const list = h("div", { class: "tp-rules-list" });
    for (const rule of rules) {
      list.appendChild(buildRuleRow(rule, plan));
    }
    wrap.appendChild(list);
    return wrap;
  }

  function buildRuleRow(rule, plan) {
    const row = h("div", { class: "tp-rule-row" + (!rule.enabled ? " tp-rule-disabled" : "") });

    // Enabled toggle
    const tog = h("button", {
      class: "tp-toggle" + (rule.enabled ? " on" : ""),
      type: "button",
      title: rule.enabled ? "Disable rule" : "Enable rule",
      onClick: () => toggleRule(rule.id, !rule.enabled),
    });

    // Rule label + type kicker
    const meta = h(
      "div",
      { class: "tp-rule-meta" },
      h("div", { class: "tp-rule-label" }, ruleLabel(rule.rule_type)),
      h("div", { class: "tp-rule-type tp-muted" }, rule.rule_type),
    );

    // Severity selector
    const sevSel = h("select", { class: "select tp-rule-sev-sel" });
    for (const s of ["error", "warning", "info"]) {
      sevSel.appendChild(h("option", { value: s, selected: rule.severity === s }, s));
    }
    sevSel.onchange = () => saveRule(rule.id, { severity: sevSel.value });

    // Config controls
    const configCtrl = buildRuleConfig(rule, plan);

    row.appendChild(tog);
    row.appendChild(meta);
    if (configCtrl) row.appendChild(configCtrl);
    row.appendChild(sevSel);

    return row;
  }

  function buildRuleConfig(rule, plan) {
    const cfg = rule.config || {};

    if (rule.rule_type === "event_name_casing") {
      const sel = h("select", { class: "select tp-rule-config-sel" });
      for (const c of ["snake_case", "camelCase", "Title"]) {
        sel.appendChild(h("option", { value: c, selected: (cfg.casing || "snake_case") === c }, c));
      }
      sel.onchange = () => saveRule(rule.id, { config: { casing: sel.value } });
      return wrapConfigField("Casing", sel);
    }

    if (rule.rule_type === "event_name_regex") {
      const inp = h("input", {
        class: "input tp-rule-config-inp",
        type: "text",
        placeholder: "^[a-z_]+$",
        value: cfg.pattern || "",
      });
      inp.onblur = () => {
        if (inp.value !== (cfg.pattern || "")) {
          saveRule(rule.id, { config: { pattern: inp.value } });
        }
      };
      return wrapConfigField("Pattern", inp);
    }

    if (rule.rule_type === "required_property") {
      const eventProps = (plan.properties && plan.properties.event) || [];
      const propSel = h("select", { class: "select tp-rule-config-sel" });
      propSel.appendChild(h("option", { value: "" }, "(pick property)"));
      for (const p of eventProps) {
        propSel.appendChild(h("option", { value: p.name, selected: (cfg.property_name || "") === p.name }, p.name));
      }
      const applyToSel = h("select", { class: "select tp-rule-config-sel" });
      for (const a of ["all", "category"]) {
        applyToSel.appendChild(h("option", { value: a, selected: (cfg.applies_to || "all") === a }, a));
      }
      const onSave = () => saveRule(rule.id, { config: { property_name: propSel.value, applies_to: applyToSel.value } });
      propSel.onchange = onSave;
      applyToSel.onchange = onSave;
      return h("div", { class: "tp-rule-config" },
        wrapConfigField("Property", propSel),
        wrapConfigField("Applies to", applyToSel),
      );
    }

    if (rule.rule_type === "event_requires_owner") {
      const bizChk = h("input", { type: "checkbox", checked: !!cfg.business });
      const techChk = h("input", { type: "checkbox", checked: !!cfg.technical });
      const onSave = () => saveRule(rule.id, { config: { business: bizChk.checked, technical: techChk.checked } });
      bizChk.onchange = onSave;
      techChk.onchange = onSave;
      return h("div", { class: "tp-rule-config" },
        h("label", { class: "tp-checkline" }, bizChk, "Business owner"),
        h("label", { class: "tp-checkline" }, techChk, "Technical owner"),
      );
    }

    // No configurable options for other types — return null
    return null;
  }

  function wrapConfigField(label, ctrl) {
    return h("div", { class: "tp-rule-config" },
      h("span", { class: "tp-rule-config-label tp-muted" }, label),
      ctrl,
    );
  }

  // ---- persistence helpers ----------------------------------------------------

  async function saveRule(ruleId, patch) {
    try {
      await persist(
        "Rule updated",
        () => doAction("update_rule", { rule_id: ruleId, ...patch }, getState().branch),
      );
      await refresh();
    } catch (e) { /* persist surfaced the banner */ }
  }

  async function toggleRule(ruleId, enabled) {
    try {
      await persist(
        "Rule " + (enabled ? "enabled" : "disabled"),
        () => doAction("set_rule_enabled", { rule_id: ruleId, enabled }, getState().branch),
      );
      await refresh();
    } catch (e) { /* persist surfaced the banner */ }
  }

  // ---- helpers ----------------------------------------------------------------

  function findEventById(plan, id) {
    if (!id || !plan) return null;
    return (plan.events || []).find((e) => e.id === id) || null;
  }

  // ---- initial load -----------------------------------------------------------
  refresh();

  return () => { unsub(); };
}
