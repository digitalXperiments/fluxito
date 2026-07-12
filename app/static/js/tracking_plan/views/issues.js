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
import { doAction, validate as apiValidate, getPid } from "tp/api";
import { h, mountAll } from "tp/render";
import { persist } from "tp/util/persist";
import { titleCase } from "tp/util/format";

// ---- Snooze/Dismiss — CLIENT-SIDE ONLY (see note at buildFindingRow) --------
// Findings come from a live rule scan (tp/api validate()) and have no stable
// server-side row/id — there is no per-finding table to attach a snooze or
// dismiss flag to. We derive a stable key from rule_id + entity + code (stable
// as long as the underlying issue is unchanged) and persist snooze-until /
// dismissed sets in localStorage, scoped per project. TODO: if findings ever
// get a real persisted identity (e.g. a findings table written by the scan
// job), move this to a backend column/table + action so it survives devices.
function findingStoreKey() { return `tp-issues-state:${getPid()}`; }

function findingKey(f) {
  return [f.rule_id || "", f.code || "", f.entity_type || "", f.entity_id || "", f.message || ""].join("|");
}

function loadFindingStore() {
  try {
    const raw = window.localStorage.getItem(findingStoreKey());
    const parsed = raw ? JSON.parse(raw) : {};
    return { snoozed: parsed.snoozed || {}, dismissed: parsed.dismissed || {} };
  } catch {
    return { snoozed: {}, dismissed: {} };
  }
}

function saveFindingStore(store) {
  try { window.localStorage.setItem(findingStoreKey(), JSON.stringify(store)); } catch { /* storage unavailable */ }
}

function snoozeFinding(key) {
  const store = loadFindingStore();
  const until = Date.now() + 7 * 24 * 60 * 60 * 1000;
  store.snoozed[key] = until;
  saveFindingStore(store);
}

function dismissFinding(key) {
  const store = loadFindingStore();
  store.dismissed[key] = true;
  saveFindingStore(store);
}

function isFindingHidden(store, key) {
  if (store.dismissed[key]) return true;
  const until = store.snoozed[key];
  if (until && until > Date.now()) return true;
  return false;
}

// ---- severity ordering + color keys -----------------------------------------

const SEV_ORDER = ["error", "warning", "info"];

function sevClass(sev) {
  if (sev === "error") return "red";
  if (sev === "warning") return "amber";
  return "sky";
}

// ---- rule-type human labels + help text -------------------------------------
//
// Each entry: { label, help }. `help` is optional inline guidance shown beneath
// the rule name in the Rules tab so users know what the control configures.

const RULE_LABELS = {
  event_name_casing: {
    label: "Event name casing",
    help: "Enforce that all event names conform to a chosen casing style (snake_case, camelCase, or Title Case).",
  },
  event_name_regex: {
    label: "Event name pattern",
    help: "Require every event name to match a custom regular expression (e.g. ^[a-z][a-z0-9_]*$).",
  },
  event_name_components: {
    label: "Structured naming convention",
    help: "Define ordered name components (object, action, context…), allowed separators, and casing. The linter rebuilds the expected pattern from these settings.",
  },
  event_requires_description: {
    label: "Event requires description",
    help: "Flag any event that has no description — helps ensure plan documentation is complete.",
  },
  event_requires_owner: {
    label: "Event requires owner",
    help: "Flag events missing a business or technical owner assignment.",
  },
  required_property: {
    label: "Required property",
    help: "Require a named property to be present on every event (or only events in a specific category).",
  },
  property_type_consistency: {
    label: "Property type consistency",
    help: "Flag properties that share a name but appear with different data types across events.",
  },
  pii_must_be_flagged: {
    label: "PII must be flagged",
    help: "Scan property names and descriptions for PII-signalling patterns; require matching properties to have is_pii=true.",
  },
};

function ruleLabel(ruleType) {
  const entry = RULE_LABELS[ruleType];
  return entry ? entry.label : titleCase(ruleType);
}

function ruleHelp(ruleType) {
  const entry = RULE_LABELS[ruleType];
  return (entry && entry.help) || null;
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

    // Page header (design: TP Issues — kicker/h1/lede, same treatment as
    // Review/Versions) — was missing entirely.
    inner.appendChild(
      h("div", { class: "tp-issues-head" },
        h("div", { class: "tp-review-kicker" }, "Issues"),
        h("h1", { class: "tp-review-h1" }, "Where plan and reality ", h("em", {}, "disagree.")),
        h("p", { class: "tp-review-lede" }, "Flux re-checks these rules on every scan. Fix them, snooze them, or turn the rule off.")),
    );

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
    const totalCount = findings.length;

    // Compact "ISSUES · 6" / "RULES · 18" pills (design: TP Issues tabs) — the
    // CSS uppercases via text-transform, so the JS strings stay sentence-case.
    const issueLabel = report ? `Issues · ${totalCount}` : "Issues";
    const rulesCount = (report && report.rules) ? report.rules.length : null;
    const rulesLabel = rulesCount != null ? `Rules · ${rulesCount}` : "Rules";

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
    const allFindings = (report && report.findings) || [];
    const store = loadFindingStore();
    const snoozedOrDismissedCount = allFindings.filter((f) => isFindingHidden(store, findingKey(f))).length;
    const findings = allFindings.filter((f) => !isFindingHidden(store, findingKey(f)));
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
          // No margin here — .tp-issues-tab > * centers it in the page column.
          style: { borderRadius: "0", borderLeft: "none", borderRight: "none", borderTop: "none" },
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
    wrap.appendChild(scanFooter(rules.length, snoozedOrDismissedCount));
    return wrap;
  }

  // Scan footer line (design: "LAST FULL SCAN … · N RULES · NEXT SCAN IN …").
  // We don't have a scan-cadence/timestamp backend field, so this shows the
  // real, available numbers only: active rule count + how many findings are
  // currently hidden by a snooze/dismiss.
  function scanFooter(ruleCount, hiddenCount) {
    const bits = [`${ruleCount} RULE${ruleCount === 1 ? "" : "S"}`];
    if (hiddenCount) bits.push(`${hiddenCount} SNOOZED/DISMISSED`);
    return h("div", { class: "tp-issues-scanfooter" },
      h("span", { class: "tp-issues-scanfooter-line" }),
      h("span", { class: "tp-issues-scanfooter-text" }, bits.join(" · ")),
      h("span", { class: "tp-issues-scanfooter-line" }));
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
    const key = findingKey(f);

    // Two-line finding (design: TP Issues row) — bold title + a descriptive
    // sentence below it. Real findings only carry one message + an optional
    // suggested_fix (no separate short-title field), so the title stays the
    // message and the description line surfaces suggested_fix when present —
    // real data, using the previously-dead .tp-issue-msg class.
    const body = h(
      "div",
      { class: "tp-issue-body", onClick: canLink ? () => openEntity(f) : null, style: canLink ? { cursor: "pointer" } : null },
      h("div", { class: "tp-issue-head" },
        h("span", { class: "tp-issue-title" }, String(f.message || f.code || "")),
        rLabel ? h("span", { class: "tp-issue-rulechip" }, "RULE: " + rLabel.toUpperCase()) : null),
      f.suggested_fix ? h("div", { class: "tp-issue-msg" }, f.suggested_fix) : null,
      canLink ? h("div", { class: "tp-issue-rule" }, "Click to open →") : null,
    );

    // Primary action: "Ask Flux to fix" (pre-seeds a Conversation with the
    // finding — no direct fix endpoint exists, this is the same pattern used
    // elsewhere in the app for Flux hand-off) when there's something to fix;
    // otherwise "Open" to jump straight to the offending entity.
    const askText = `Fix this tracking-plan issue: ${f.message || f.code || ""}`
      + (f.suggested_fix ? ` Suggested fix: ${f.suggested_fix}` : "");
    const primaryBtn = h("a", {
      class: "tp-issue-act-primary",
      href: "/ask?q=" + encodeURIComponent(askText),
      onClick: (e) => e.stopPropagation(),
    }, "Ask Flux to fix");

    const snoozeBtn = h("button", {
      class: "tp-issue-act-secondary",
      onClick: (e) => { e.stopPropagation(); snoozeFinding(key); render(); },
    }, "Snooze");
    const dismissBtn = h("button", {
      class: "tp-issue-act-secondary",
      onClick: (e) => { e.stopPropagation(); dismissFinding(key); render(); },
    }, "Dismiss");

    const actions = h("div", { class: "tp-issue-actions" }, primaryBtn, snoozeBtn, dismissBtn);

    return h("div", { class: "tp-issue-row", dataset: { sev: f.severity || "info" } }, body, actions);
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

  // buildRuleRow — wrapping flex layout:
  //   Line 1: toggle + meta (label, kicker, help)
  //   Line 2 (wraps): .tp-rule-controls cluster with all config fields + severity
  //
  // Each config field uses a light .tp-rule-field span (label + control inline),
  // NOT the old nested .tp-rule-config-inside-.tp-rule-config pattern.
  function buildRuleRow(rule, plan) {
    const row = h("div", { class: "tp-rule-row" + (!rule.enabled ? " tp-rule-disabled" : "") });

    // Enabled toggle
    const tog = h("button", {
      class: "tp-toggle" + (rule.enabled ? " on" : ""),
      type: "button",
      title: rule.enabled ? "Disable rule" : "Enable rule",
      onClick: () => toggleRule(rule.id, !rule.enabled),
    });

    // Rule label + type kicker + optional help text — all on their own meta block
    const help = ruleHelp(rule.rule_type);
    const meta = h(
      "div",
      { class: "tp-rule-meta" },
      h("div", { class: "tp-rule-label" }, ruleLabel(rule.rule_type)),
      h("div", { class: "tp-rule-type tp-muted" }, rule.rule_type),
      help ? h("div", { class: "tp-rule-help tp-muted" }, help) : null,
    );

    // Controls cluster: config fields + severity selector, all wrapped together
    // so they reflow as a unit at narrow widths.
    const controls = h("div", { class: "tp-rule-controls" });

    // Per-rule config fields (flat — no nesting)
    const configFields = buildRuleConfigFields(rule, plan);
    for (const field of configFields) {
      controls.appendChild(field);
    }

    // Severity selector — always present per rule
    const sevSel = h("select", { class: "select tp-rule-sev-sel" });
    for (const s of ["error", "warning", "info"]) {
      sevSel.appendChild(h("option", { value: s, selected: rule.severity === s }, s));
    }
    sevSel.onchange = () => saveRule(rule.id, { severity: sevSel.value });
    controls.appendChild(ruleField("Severity", sevSel));

    row.appendChild(tog);
    row.appendChild(meta);
    row.appendChild(controls);

    return row;
  }

  // ruleField — lightweight label+control pair using .tp-rule-field span.
  // Replaces the old wrapConfigField() which produced nested .tp-rule-config divs.
  function ruleField(label, ctrl) {
    return h(
      "span",
      { class: "tp-rule-field" },
      h("span", { class: "tp-rule-field-label tp-muted" }, label),
      ctrl,
    );
  }

  // buildRuleConfigFields — returns an array of .tp-rule-field elements (flat).
  // Each rule type produces its own set of fields; the caller appends them all
  // into the single .tp-rule-controls cluster alongside the severity selector.
  function buildRuleConfigFields(rule, plan) {
    const cfg = rule.config || {};
    const fields = [];

    // ---- event_name_casing: casing style picker --------------------------------
    if (rule.rule_type === "event_name_casing") {
      const sel = h("select", { class: "select tp-rule-config-sel" });
      for (const c of ["snake_case", "camelCase", "Title"]) {
        sel.appendChild(h("option", { value: c, selected: (cfg.casing || "snake_case") === c }, c));
      }
      sel.onchange = () => saveRule(rule.id, { config: { casing: sel.value } });
      fields.push(ruleField("Casing", sel));
    }

    // ---- event_name_regex: regex pattern input ---------------------------------
    else if (rule.rule_type === "event_name_regex") {
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
      fields.push(ruleField("Pattern", inp));
    }

    // ---- event_name_components: structured naming rule -------------------------
    // Backend config shape (see rules.py):
    //   { components: ["object","action"], separators: ["_"], casing: "lower",
    //     min_parts: 2, max_parts: null }
    // "components" = ordered label slots for each name segment.
    // "separators" = list of allowed separator strings (we expose as first item).
    // "casing" = token casing (lower | upper | snake_case | camelCase | TitleCase | any).
    else if (rule.rule_type === "event_name_components") {
      const components = Array.isArray(cfg.components) ? [...cfg.components] : ["object", "action"];
      // separators is a list; expose the first one in the UI as the chosen separator
      const separators = Array.isArray(cfg.separators) ? cfg.separators : ["_"];
      const casing = cfg.casing || "lower";

      const saveComponents = () => saveRule(rule.id, {
        config: { ...cfg, components: [...components], separators: [...separators], casing },
      });

      // Components editor: ordered tags with add/remove
      const compWrap = h("div", { class: "tp-tag-list" });

      const refreshCompTags = () => {
        mountAll(compWrap, components.map((comp, idx) => {
          const rm = h("button", { class: "tp-tag-rm", type: "button", title: "Remove" }, "×");
          rm.onclick = () => {
            components.splice(idx, 1);
            refreshCompTags();
            saveComponents();
          };
          return h("span", { class: "tp-tag" }, comp, rm);
        }));
      };
      refreshCompTags();

      const compInp = h("input", {
        class: "input tp-rule-config-inp",
        type: "text",
        placeholder: "add component…",
        style: "width:120px",
      });
      const commitCompInp = () => {
        const val = compInp.value.trim();
        if (val && !components.includes(val)) {
          components.push(val);
          refreshCompTags();
          saveComponents();
        }
        compInp.value = "";
      };
      compInp.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commitCompInp(); }
      };
      compInp.onblur = () => { if (compInp.value.trim()) commitCompInp(); };

      const compBlock = h("div", { class: "tp-tag-editor" }, compWrap, compInp);
      fields.push(ruleField("Components", compBlock));

      // Separator picker (sets separators[0] and keeps any extras)
      const sepSel = h("select", { class: "select tp-rule-config-sel" });
      for (const sep of ["_", "-", ".", "/", " ", ""]) {
        const label = sep === "" ? "(none)" : sep === " " ? "(space)" : `"${sep}"`;
        sepSel.appendChild(h("option", { value: sep, selected: (separators[0] || "_") === sep }, label));
      }
      sepSel.onchange = () => {
        separators[0] = sepSel.value;
        saveRule(rule.id, { config: { ...cfg, components: [...components], separators: [...separators], casing } });
      };
      fields.push(ruleField("Separator", sepSel));

      // Casing picker — matches backend CASING_CHOICES
      const casSel = h("select", { class: "select tp-rule-config-sel" });
      for (const cas of ["lower", "upper", "snake_case", "camelCase", "TitleCase", "any"]) {
        casSel.appendChild(h("option", { value: cas, selected: casing === cas }, cas));
      }
      casSel.onchange = () => saveRule(rule.id, {
        config: { ...cfg, components: [...components], separators: [...separators], casing: casSel.value },
      });
      fields.push(ruleField("Casing", casSel));
    }

    // ---- required_property: property name + applies-to (all | category) -------
    else if (rule.rule_type === "required_property") {
      const eventProps = (plan.properties && plan.properties.event) || [];
      const categories = plan.categories || [];

      const propSel = h("select", { class: "select tp-rule-config-sel" });
      propSel.appendChild(h("option", { value: "" }, "(pick property)"));
      for (const p of eventProps) {
        propSel.appendChild(h("option", { value: p.name, selected: (cfg.property_name || "") === p.name }, p.name));
      }

      const applyToSel = h("select", { class: "select tp-rule-config-sel" });
      applyToSel.appendChild(h("option", { value: "all" }, "all events"));
      applyToSel.appendChild(h("option", { value: "category", selected: (cfg.applies_to || "all") === "category" }, "category"));

      // Category picker — only visible when applies_to === 'category'
      const catSel = h("select", { class: "select tp-rule-config-sel" });
      catSel.appendChild(h("option", { value: "" }, "(pick category)"));
      for (const cat of categories) {
        const catName = typeof cat === "string" ? cat : (cat.name || "");
        catSel.appendChild(h("option", {
          value: catName,
          selected: catName === (cfg.scope_category || ""),
        }, catName));
      }

      const catFieldSpan = ruleField("Category", catSel);
      catFieldSpan.style.display = (cfg.applies_to || "all") === "category" ? "" : "none";

      const onSave = () => {
        const appliesTo = applyToSel.value;
        catFieldSpan.style.display = appliesTo === "category" ? "" : "none";
        // scope_category_id is resolved server-side from the category name;
        // we send scope_category_id by finding the matching category object.
        const matchCat = categories.find((c) => {
          const n = typeof c === "string" ? c : (c.name || "");
          return n === catSel.value;
        });
        const scopeCatId = matchCat && typeof matchCat === "object" ? (matchCat.id || null) : null;
        saveRule(rule.id, {
          config: { property_name: propSel.value, applies_to: appliesTo, scope_category: catSel.value },
          scope_category_id: scopeCatId,
        });
      };
      propSel.onchange = onSave;
      applyToSel.onchange = onSave;
      catSel.onchange = onSave;

      fields.push(ruleField("Property", propSel));
      fields.push(ruleField("Applies to", applyToSel));
      fields.push(catFieldSpan);
    }

    // ---- event_requires_owner: business/technical checkboxes -------------------
    else if (rule.rule_type === "event_requires_owner") {
      const bizChk = h("input", { type: "checkbox", checked: !!cfg.business });
      const techChk = h("input", { type: "checkbox", checked: !!cfg.technical });
      const onSave = () => saveRule(rule.id, { config: { business: bizChk.checked, technical: techChk.checked } });
      bizChk.onchange = onSave;
      techChk.onchange = onSave;

      const bizLabel = h("label", { class: "tp-checkline" }, bizChk, "Business");
      const techLabel = h("label", { class: "tp-checkline" }, techChk, "Technical");
      const chkWrap = h("span", { class: "tp-rule-chk-group" }, bizLabel, techLabel);
      fields.push(ruleField("Require", chkWrap));
    }

    // ---- pii_must_be_flagged: PII pattern tag editor ---------------------------
    // Backend config shape: { patterns: ["email", "ssn", "phone", ...] }
    // The backend stores the list as config.patterns. Tags are substring/regex
    // patterns; the linter requires is_pii=true on any property whose name or
    // description matches a pattern.
    else if (rule.rule_type === "pii_must_be_flagged") {
      // cfg.patterns is the canonical server key (see rules.py DEFAULT_RULES)
      const patterns = Array.isArray(cfg.patterns) ? [...cfg.patterns] : [];

      const tagList = h("div", { class: "tp-tag-list" });

      const savePii = () => saveRule(rule.id, { config: { ...cfg, patterns: [...patterns] } });

      const refreshTags = () => {
        mountAll(tagList, patterns.map((pat, idx) => {
          const rm = h("button", { class: "tp-tag-rm", type: "button", title: "Remove pattern" }, "×");
          rm.onclick = () => {
            patterns.splice(idx, 1);
            refreshTags();
            savePii();
          };
          return h("span", { class: "tp-tag" }, pat, rm);
        }));
      };
      refreshTags();

      const patInp = h("input", {
        class: "input tp-rule-config-inp",
        type: "text",
        placeholder: "add pattern…",
        style: "width:120px",
      });
      const commitPatInp = () => {
        const val = patInp.value.trim();
        if (val && !patterns.includes(val)) {
          patterns.push(val);
          refreshTags();
          savePii();
        }
        patInp.value = "";
      };
      patInp.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === ",") {
          e.preventDefault();
          commitPatInp();
        }
      };
      patInp.onblur = () => commitPatInp();

      const tagEditor = h("div", { class: "tp-tag-editor" }, tagList, patInp);
      fields.push(ruleField("PII patterns", tagEditor));
    }

    return fields;
  }

  // ---- persistence helpers ----------------------------------------------------

  // saveRule sends update_rule with optional scope_category_id at the top level.
  // patch may contain: { config?, severity?, scope_category_id? }
  async function saveRule(ruleId, patch) {
    const { scope_category_id, ...rest } = patch;
    const payload = { rule_id: ruleId, ...rest };
    if (scope_category_id !== undefined) {
      payload.scope_category_id = scope_category_id;
    }
    try {
      await persist(
        "Rule updated",
        () => doAction("update_rule", payload, getState().branch),
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
