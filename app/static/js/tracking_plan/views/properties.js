// app/static/js/tracking_plan/views/properties.js
// Property library + editor, redesigned to the approved mockup
// (/tmp/tp_redesign_mockup.html) with the EXPLICIT BUFFERED-SAVE model.
//
// Master: refined list grouped by property kind, mono names, searchable.
//         Shows the FULL shared pool — members appear here too (correct by design).
// Detail: .tp-ed-head with the SAVE CLUSTER, then .tp-card sections —
//   • Type & flags   (name, kind, data_type, List toggle, is_pii toggle)
//   • Constraints    (allowed values / min / max / regex + live regex-valid hint)
//   • Nested members (object props: recursive member tree + industry-standard combo)
//   • Used by N events (event chips → navigate)
//
// DATA_TYPES: string / integer / float / boolean / object  (NO 'array' or 'int').
// "List of X" is expressed as data_type=X, is_list=true via the List toggle.
//
// BUFFERED SAVE (no autosave — nothing persists until "Save changes"):
//   On select, snapshot `server` and `draft = clone(editable fields)`. Render
//   from draft; every edit mutates draft ONLY and re-renders. dirty = !deepEqual.
//   Discard → draft = clone(server). Save → commitProperty() (one update_property
//   with assembled constraints) → reload → re-snapshot. The regex-valid gate
//   disables Save while the regex is invalid. Nested member add/remove stay
//   immediate sub-edits (structural, on the library) — the editable FIELDS buffer.
//
// MEMBER MUTATIONS (immediate, not buffered — structural changes to the library):
//   add_member:     pick existing lib prop OR create new, then link via add_member action.
//   remove_member:  remove_member action.
//   reorder_members: drag rows within the member table → reorder_members action.

import { h, mountAll } from 'tp/render';
import * as state from 'tp/state';
import * as api from 'tp/api';
import { mountDrawer } from 'tp/comments';
import { persist } from 'tp/util/persist';
import { clone, deepEqual, editorHead } from 'tp/util/editor';
import { allProps, typeBadge } from 'tp/util/format';
import { isValidRegex, buildConstraints, usedByEvents } from 'tp/util/constraints';

const KINDS = [
  ['event', 'Event'],
  ['user', 'User'],
  ['group', 'Group'],
  ['system', 'System'],
];

// Canonical data types: 5 base types, no 'array', no 'int'.
const DATA_TYPES = ['string', 'integer', 'float', 'boolean', 'object'];

// data_type → badge color (mono badges, mockup palette).
function typeBadgeClass(dt) {
  if (dt === 'object') return 'tp-badge amber';
  if (dt === 'boolean') return 'tp-badge sky';
  return 'tp-badge ty';
}

// The editable slice we snapshot/diff for buffered save.
function draftOf(p) {
  return clone({
    name: p.name || '',
    kind: p.kind || 'event',
    data_type: p.data_type || 'string',
    is_list: !!p.is_list,
    is_pii: !!p.is_pii,
    description: p.description || '',
    constraints: p.constraints || null,
  });
}

export function mountView(container) {
  const layout = h('div', { class: 'tp-master-detail' });
  const master = h('div', { class: 'tp-master' });
  const detail = h('div', { class: 'tp-detail', id: 'tp-prop-detail' });
  layout.appendChild(master);
  layout.appendChild(detail);
  mountAll(container, [layout]);

  let search = '';
  let drawer = null;
  let drawerEntityId = null;

  // Buffered-save working state for the currently selected property.
  let server = null; // last server snapshot (editable slice)
  let draft = null;  // live draft (editable slice) — editor renders from this
  let saving = false;

  // A pure setDirty() notification (same plan ref + same selection) must NOT
  // re-render the detail: that would steal focus from the input being typed in,
  // and because paint() ends with syncDirty() it would recurse. Mirror events.js —
  // track the last (plan, selection) rendered and ignore no-op notifications.
  // Real changes (reload, branch/entity switch) still re-render the detail;
  // buffered field edits refresh only the header in place (refreshHead).
  let lastPlan;
  let lastSelKey;
  const unsub = state.subscribe(() => {
    const st = state.getState();
    const selKey = st.selection.type + ':' + st.selection.id;
    if (st.plan === lastPlan && selKey === lastSelKey) return; // pure dirty toggle
    lastPlan = st.plan;
    lastSelKey = selKey;
    renderList();
    renderDetail();
  });
  renderList();
  renderDetail();

  function plan() { return state.getState().plan; }
  function dirty() { return !!server && !deepEqual(draft, server); }
  function syncDirty() { state.setDirty(dirty()); }

  // ---- MASTER -------------------------------------------------------------
  function renderList() {
    const head = h('div', { class: 'tp-master-head' },
      // Neither the Events nor Properties mockups show a search icon — plain
      // unadorned input (see .tp-search.no-icon in the CSS).
      h('div', { class: 'tp-search no-icon' },
        h('input', {
          class: 'input', placeholder: 'Filter properties…', value: search,
          onInput: (e) => { search = e.target.value; renderListBody(listBody); },
        })),
      h('button', { class: 'btn btn-primary btn-sm btn-block', onClick: newProperty }, '+ New property'));
    const listBody = h('div', { class: 'tp-master-list' });
    renderListBody(listBody);
    mountAll(master, [head, listBody]);
  }

  function renderListBody(listBody) {
    if (!plan()) { mountAll(listBody, [h('div', { class: 'tp-row-empty' }, 'Loading…')]); return; }
    const sel = state.getState().selection;
    const q = search.trim().toLowerCase();
    const nodes = [];
    let any = false;
    KINDS.forEach(([k, label]) => {
      // Show the FULL shared pool — do NOT filter out members (correct by design: all
      // props including member props live in the flat library buckets).
      let items = (plan().properties && plan().properties[k]) || [];
      items = items.slice().sort((a, b) => a.name.localeCompare(b.name));
      if (q) items = items.filter((p) => p.name.toLowerCase().includes(q) || (p.description || '').toLowerCase().includes(q));
      if (!items.length) return;
      any = true;
      nodes.push(h('div', { class: 'tp-grp' }, `${label} properties`));
      items.forEach((p) => {
        const attachments = propertyAttachments(plan(), p);
        const glyph = listGlyph(p, attachments);
        nodes.push(h('div', {
          class: 'tp-ev' + (sel.type === 'property' && sel.id === p.id ? ' is-active' : ''),
          onClick: () => selectProperty(p.id),
        },
          h('div', { class: 'tp-ev-main' },
            h('div', { class: 'tp-ev2-toprow' },
              h('div', { class: 'tp-ev-name' }, p.name),
              glyph ? h('span', { class: 'tp-ev2-glyph', style: { color: glyph.color } }, glyph.label) : null),
            h('div', { class: 'tp-ev-sub' }, propertySubtitle(p, attachments)))));
      });
    });
    if (!any) nodes.push(h('div', { class: 'tp-row-empty' }, q ? 'No matches' : 'No properties yet'));
    mountAll(listBody, nodes);
  }

  // ---- real per-property signal, derived from event-property attachments --
  // A library property carries no coverage/required/example itself — those
  // live on the per-event attachment (event.properties[]). Gather every
  // event that attaches this property (by name) so the list glyph, the
  // Definition grid, "Used by", and the destination-mapping card can all
  // read real, already-loaded data (no extra fetch, no fabrication).
  function propertyAttachments(pl, p) {
    if (!pl || !p || p.kind !== 'event') return [];
    return (pl.events || [])
      .map((e) => {
        const ep = (e.properties || []).find((x) => x.name === p.name);
        return ep ? { event: e, required: !!ep.required, example: ep.example || '', observation: ep.observation || null } : null;
      })
      .filter(Boolean);
  }

  // Worst (lowest) live present_pct across all attachments that have observation
  // data, or null when there's no observation data at all (never fabricated).
  function worstCoverage(attachments) {
    let worst = null;
    attachments.forEach((a) => {
      if (a.observation && a.observation.present_pct != null) {
        if (worst == null || a.observation.present_pct < worst.pct) worst = { pct: a.observation.present_pct, event: a.event };
      }
    });
    return worst;
  }

  // List-row status glyph vocabulary (design: TP Properties list) — ✓ green
  // when every attachment is well-covered, ⚠ N% red for the worst offender.
  // ("NEW" is skipped: unlike events, there's no per-property "unplanned, seen
  // live" record in the data model — only per-event unplanned PARAMS.)
  function listGlyph(p, attachments) {
    const worst = worstCoverage(attachments);
    if (!worst) return null;
    if (worst.pct >= 90) return { label: '✓', color: 'var(--tp-green)' };
    return { label: `⚠ ${worst.pct}%`, color: 'var(--tp-red)' };
  }

  // "number · used by 6 events" (design: TP Properties list row subtitle).
  function propertySubtitle(p, attachments) {
    const typeStr = typeBadge(p.data_type, p.is_list);
    if (p.kind === 'event') {
      const n = attachments.length;
      return `${typeStr} · used by ${n} event${n === 1 ? '' : 's'}`;
    }
    return typeStr;
  }

  function selectProperty(id) {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    state.setDirty(false);
    server = null; draft = null; saving = false;
    state.select('property', id);
  }

  async function newProperty() {
    if (dirty() && !confirm('Discard unsaved changes?')) return;
    try {
      const r = await persist('Property created', () =>
        api.doAction('create_property', { name: 'new_property', data_type: 'string', kind: 'event' }, state.getState().branch));
      await state.reload();
      server = null; draft = null;
      state.setDirty(false);
      state.select('property', r.id);
      setTimeout(() => { const n = detail.querySelector('#pd-name'); if (n) { n.focus(); n.select(); } }, 0);
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // ---- DETAIL / EDITOR ----------------------------------------------------
  function currentProp() {
    const sel = state.getState().selection;
    if (sel.type !== 'property') return null;
    return allProps(plan() || {}).find((x) => x.id === sel.id) || null;
  }

  function renderDetail() {
    if (!plan()) { mountAll(detail, [h('div', { class: 'tp-empty' }, 'Loading…')]); return; }
    const p = currentProp();
    if (!p) {
      if (drawer) { drawer.destroy(); drawer = null; drawerEntityId = null; }
      server = null; draft = null;
      mountAll(detail, [h('div', { class: 'tp-empty' }, 'Select a property to edit its type & constraints, or create one.')]);
      return;
    }
    // (Re)snapshot when the selected property changes (or first render of it).
    if (!server || !draft || drawerEntityId !== p.id) {
      server = draftOf(p);
      draft = clone(server);
      saving = false;
    }
    paint(p);

    // Recreate the Comments drawer only when the selected property changes, so an
    // open panel survives field edits (which re-render the detail).
    if (drawerEntityId !== p.id) {
      if (drawer) { drawer.destroy(); drawer = null; }
      drawer = mountDrawer(document.querySelector('.tp-workspace') || document.body,
        { entityType: 'property', entityId: p.id, branch: state.getState().branch });
      drawerEntityId = p.id;
    }
  }

  // Re-render the editor body FROM draft (no resnapshot). Called after each edit.
  function repaint() {
    const p = currentProp();
    if (p) paint(p);
  }

  function paint(p) {
    const regexValid = isValidRegex((draft.constraints && draft.constraints.regex) || '');
    const inner = h('div', { class: 'tp-detail-inner' });
    inner.appendChild(header(p, regexValid));
    const callout = coverageCallout(p);
    if (callout) inner.appendChild(callout);
    inner.appendChild(definitionGrid(p));
    inner.appendChild(typeFlagsCard(p));
    inner.appendChild(constraintsCard(p));
    inner.appendChild(membersCard(p));
    inner.appendChild(usedByCard(p));
    inner.appendChild(destinationMappingCard(p));
    mountAll(detail, [inner]);
    syncDirty();
  }

  // ---- editor header: mono name + chips + Comments/Delete + save ----
  function header(p, regexValid) {
    const kindLabel = (KINDS.find(([k]) => k === draft.kind) || [null, draft.kind])[1];
    const chips = [
      h('span', { class: 'tp-chip accent' }, kindLabel),
      h('span', { class: typeBadgeClass(draft.data_type) }, typeBadge(draft.data_type, draft.is_list)),
      draft.is_pii ? h('span', { class: 'tp-badge amber' }, 'PII') : null,
    ];
    const cmtBtn = h('button', { class: 'btn btn-ghost btn-sm', onClick: () => drawer && drawer.open() }, '💬 Comments');
    const delBtn = h('button', { class: 'btn btn-danger btn-sm', onClick: () => confirmDelete(p) }, 'Delete');

    const head = editorHead({
      // No 'Property' kicker/eyebrow — the design puts the mono h1 directly at
      // the top of the panel (matches the Event detail treatment).
      name: draft.name || p.name,
      chips,
      actions: [cmtBtn, delBtn],
      dirty: dirty(),
      saving,
      // Save gates on regex validity: drop the handler AND disable the button so
      // an invalid regex can never persist.
      onSave: regexValid ? () => doSave(p) : undefined,
      onDiscard: () => { draft = clone(server); repaint(); },
    });
    // Read-only description subline under the title (design: TP Properties detail).
    const idBlock = head.querySelector('.tp-ed-id');
    if (idBlock) {
      idBlock.appendChild(h('div', { class: 'tp-ed2-subline' }, draft.description || 'No description yet'));
    }
    if (!regexValid) {
      const sb = head.querySelector('.tp-savecluster .btn-primary');
      if (sb) { sb.disabled = true; sb.title = 'Fix the invalid regex to save'; }
    }
    return head;
  }

  // ---- coverage/Flux callout (design: TP Properties detail) — only rendered
  // when a real observation shows a live coverage gap on an attached event. ----
  function coverageCallout(p) {
    const worst = worstCoverage(propertyAttachments(plan(), p));
    if (!worst || worst.pct >= 90) return null;
    const missingPct = Math.round((100 - worst.pct) * 10) / 10;
    return h('div', { class: 'tp-pd-callout' },
      h('span', { class: 'tp-ed2-callout-badge' }, 'F'),
      h('div', { class: 'tp-pd-callout-body' },
        'Missing on ', h('strong', {}, `${missingPct}% of ${worst.event.name} hits`), '.'),
      h('a', {
        class: 'tp-pd-callout-action',
        href: '/ask?q=' + encodeURIComponent(
          `Investigate why "${p.name}" is missing on ${missingPct}% of ${worst.event.name} hits and draft a fix.`),
      }, 'Ask Flux to investigate'));
  }

  // ---- Definition read grid: TYPE / REQUIRED / EXAMPLE / OWNER (design: TP
  // Properties detail). TYPE/constraints reflect the live draft (so editing
  // Type & flags / Constraints below updates this at-a-glance summary
  // immediately); REQUIRED/EXAMPLE are read from real per-event attachments.
  // OWNER has no backing field on TPProperty — shown as '—', never fabricated. ----
  function definitionGrid(p) {
    const attachments = propertyAttachments(plan(), p);
    const typeStr = typeBadge(draft.data_type, draft.is_list);
    const c = draft.constraints || {};
    const bits = [typeStr];
    if (c.allowed_values && c.allowed_values.length) bits.push(c.allowed_values.join(' | '));
    if (c.min != null) bits.push(`≥ ${c.min}`);
    if (c.max != null) bits.push(`≤ ${c.max}`);
    if (c.regex) bits.push(`matches /${c.regex}/`);

    let requiredValue = '—';
    if (attachments.length) {
      const reqCount = attachments.filter((a) => a.required).length;
      requiredValue = reqCount === 0
        ? 'Optional everywhere'
        : reqCount === attachments.length
          ? `Required on all ${attachments.length} event${attachments.length === 1 ? '' : 's'}`
          : `Required on ${reqCount} of ${attachments.length} events`;
    }

    const exampleHit = attachments.find((a) => a.example);
    const exampleValue = exampleHit ? exampleHit.example : '—';

    return h('div', { class: 'tp-pd-section' },
      h('div', { class: 'tp-pd-eyebrow' }, 'Definition'),
      h('div', { class: 'tp-pd-defgrid' },
        defCell('TYPE', bits.join(' · ')),
        defCell('REQUIRED', requiredValue),
        defCell('EXAMPLE', exampleValue),
        defCell('OWNER', '—', true)));
  }
  function defCell(label, value, sans) {
    return h('div', { class: 'tp-pd-defcell' },
      h('div', { class: 'tp-pd-deflabel' }, label),
      h('div', { class: 'tp-pd-defvalue' + (sans ? ' sans' : '') }, value));
  }

  // ---- Destination mapping (design: TP Properties detail) — derived from the
  // real destinations of every event this property is attached to (there is no
  // per-property destination-transformation field in the data model, so we
  // surface the honest signal we do have: which destinations this property
  // reaches, and through which events). ----
  function destinationMappingCard(p) {
    const attachments = propertyAttachments(plan(), p);
    const map = {}; // destination name -> Set(event names)
    attachments.forEach((a) => {
      (a.event.destinations || []).filter((d) => d.enabled !== false).forEach((d) => {
        (map[d.destination] ||= new Set()).add(a.event.name);
      });
    });
    const names = Object.keys(map).sort();
    const body = names.length
      ? names.map((name) => destMapRow(name, map[name]))
      : [h('div', { class: 'tp-pd-empty' }, 'Not mapped to any destination through its attached events.')];
    return h('div', { class: 'tp-pd-section' },
      h('div', { class: 'tp-pd-eyebrow' }, 'Destination mapping'),
      h('div', { class: 'tp-pd-card' }, ...body));
  }
  function destMapRow(name, eventNameSet) {
    const slug = destLogoSlug(name);
    const names = Array.from(eventNameSet);
    const via = names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3} more` : '');
    return h('div', { class: 'tp-pd-destrow' },
      h('span', { class: 'tp-pd-dest-name' },
        h('img', { src: `/static/img/logos/${slug}.svg`, alt: '', class: 'tp-pd-dest-logo' }), name),
      h('span', { class: 'tp-pd-dest-map' }, `via ${via}`));
  }

  // Resolve a destination's logo slug (mirrors events.js destLogoSlug — kept
  // local since it's the only other view needing it).
  function destLogoSlug(name) {
    const vendors = (state.getState().vendors && state.getState().vendors.destinations) || [];
    const hit = vendors.find((v) => (v.display_name || '').toLowerCase() === (name || '').toLowerCase());
    if (hit && hit.slug) return hit.slug;
    const n = (name || '').trim();
    if (n.toLowerCase() === 'google ads') return 'google-ads';
    return n.toLowerCase().replace(/\s+/g, '_');
  }

  // ---- card: Type & flags ----
  function typeFlagsCard(p) {
    const nameInp = h('input', {
      class: 'input mono', id: 'pd-name', value: draft.name,
      onInput: (e) => { draft.name = e.target.value; syncDirty(); refreshHead(); },
    });

    const kindSel = h('select', {
      class: 'select', onChange: (e) => { draft.kind = e.target.value; repaint(); },
    }, ...KINDS.map(([k, label]) => h('option', { value: k, selected: draft.kind === k }, label)));

    const typeSel = h('select', {
      class: 'select',
      onChange: (e) => {
        draft.data_type = e.target.value;
        // When switching away from object, members become invalid — warn but still
        // allow saving (backend will reject if members exist and we'd get a 409).
        repaint();
      },
    }, ...DATA_TYPES.map((t) => h('option', { value: t, selected: draft.data_type === t }, t)));

    // "List" toggle — drives is_list independently of data_type.
    const listToggle = toggleRow('List', 'Values are a list (data_type[])', draft.is_list,
      () => { draft.is_list = !draft.is_list; repaint(); });
    const piiToggle = toggleRow('PII', 'Contains personal data', draft.is_pii,
      () => { draft.is_pii = !draft.is_pii; repaint(); });

    const desc = h('textarea', { class: 'textarea', id: 'pd-desc', placeholder: 'What does this property capture?' });
    desc.value = draft.description || '';
    desc.addEventListener('input', () => { draft.description = desc.value; syncDirty(); refreshHead(); });

    return card('Type & flags', null,
      h('div', { class: 'tp-card-b' },
        h('div', { class: 'tp-field tp-col-2', style: { marginBottom: '16px' } },
          h('label', { class: 'tp-lbl' }, 'Name'), nameInp),
        h('div', { class: 'tp-grid2' },
          field('Kind', kindSel),
          field('Data type', typeSel),
          listToggle,
          piiToggle),
        h('div', { class: 'tp-field tp-col-2', style: { marginTop: '16px' } },
          h('label', { class: 'tp-lbl' }, 'Description'), desc)));
  }

  // Rebuild the sticky header in place after a buffered field edit — refreshes the
  // mono name mirror, chips, and the save cluster (● Unsaved / Save enabled/gated)
  // WITHOUT re-rendering the cards, so the focused input keeps its caret. Every
  // editable input lives in a card (#pd-name, #pd-desc, #pd-enum/min/max/regex),
  // never in the header, so replacing the header never steals focus.
  function refreshHead() {
    const head = detail.querySelector('.tp-ed-head');
    if (!head) return;
    const regexValid = isValidRegex((draft.constraints && draft.constraints.regex) || '');
    head.replaceWith(header(currentProp(), regexValid));
  }

  // ---- card: Constraints (live regex hint, applies vs draft.constraints) ----
  function constraintsCard(p) {
    const c = draft.constraints || {};
    // integer and float support min/max range constraints.
    const numeric = draft.data_type === 'integer' || draft.data_type === 'float';

    const enumInp = h('input', {
      class: 'input mono', id: 'pd-enum', placeholder: 'USD, EUR, GBP',
      value: (c.allowed_values || []).join(', '),
    });
    const minInp = h('input', { class: 'input', id: 'pd-min', type: 'number', value: c.min ?? '', placeholder: 'min' });
    const maxInp = h('input', { class: 'input', id: 'pd-max', type: 'number', value: c.max ?? '', placeholder: 'max' });
    const regexInp = h('input', { class: 'input mono', id: 'pd-regex', placeholder: '^[A-Z]{3}$', value: c.regex || '' });
    const regexHint = h('span', { class: 'tp-regex-hint' });

    const recompute = () => {
      const next = buildConstraints({
        enumRaw: enumInp.value,
        min: minInp.value,
        max: maxInp.value,
        regex: regexInp.value,
      });
      draft.constraints = next;
      syncDirty();
      refreshHead();
    };
    const refreshRegexHint = () => {
      const val = regexInp.value;
      const ok = isValidRegex(val);
      regexHint.textContent = val.trim() ? (ok ? '✓ valid regex' : '✗ invalid regex') : '';
      regexHint.className = 'tp-regex-hint ' + (ok ? 'is-ok' : 'is-bad');
      // Toggle the Save button enabled state without a full repaint.
      const sb = detail.querySelector('.tp-ed-head .tp-savecluster .btn-primary');
      if (sb) {
        if (val.trim() && !ok) { sb.disabled = true; sb.title = 'Fix the invalid regex to save'; }
        else if (dirty()) { sb.disabled = !!saving; sb.removeAttribute('title'); }
      }
    };

    [enumInp, minInp, maxInp].forEach((el) => el.addEventListener('input', recompute));
    regexInp.addEventListener('input', () => { recompute(); refreshRegexHint(); });
    refreshRegexHint();

    const regexField = h('div', { class: 'tp-field tp-col-2' },
      h('label', { class: 'tp-lbl' }, 'Regex / format'), regexInp, regexHint);

    const body = h('div', { class: 'tp-card-b' },
      h('div', { class: 'tp-grid2' },
        h('div', { class: 'tp-field tp-col-2' },
          h('label', { class: 'tp-lbl' }, 'Allowed values (enum) — comma separated'), enumInp),
        field(numeric ? 'Min' : 'Min length', minInp),
        field(numeric ? 'Max' : 'Max length', maxInp),
        regexField));
    return card('Constraints', null, body);
  }

  // ---- card: Nested members (industry-standard shared-pool — only for object props) ----
  //
  // Renders a recursive member tree from the serialized members:[...] array on the
  // server property. Each member row shows name + type badge + Remove. Object-typed
  // members expand their own nested members at the next depth level.
  //
  // Member mutations are IMMEDIATE (not buffered with field edits) — they persist
  // structural changes to the library via doAction (add_member / remove_member /
  // reorder_members) and then call state.reload() + repaint().
  function membersCard(p) {
    const isObject = draft.data_type === 'object';

    if (!isObject) {
      return card('Nested members', null,
        h('div', { class: 'tp-card-b' },
          h('div', { class: 'tp-muted', style: { fontSize: '13px' } },
            'Set the data type to object to add members.')));
    }

    // The serialized members[] tree lives on the server prop (not in draft —
    // members are structural, not buffered-field edits).
    const members = p.members || [];
    const memberCount = countMembers(members);

    const treeEl = h('div', { class: 'tp-members' });
    renderMemberTree(treeEl, members, p, 0);

    const combo = memberCombo(p);

    const note = dirty()
      ? h('div', { class: 'tp-muted', style: { fontSize: '11.5px', marginTop: '8px' } },
        'Member changes save immediately; field edits above stay buffered until you click Save.')
      : null;

    const body = h('div', { class: 'tp-card-b' },
      treeEl,
      combo,
      note);
    return card('Nested members', memberCount ? String(memberCount) : null, body);
  }

  // Count members (direct children only, for the card badge).
  function countMembers(members) {
    return (members || []).length;
  }

  // Recursively render the members[] tree into `container`.
  // depth is used to drive indentation via CSS var (--depth).
  function renderMemberTree(container, members, parentProp, depth) {
    if (!members || !members.length) {
      if (depth === 0) {
        container.appendChild(
          h('div', { class: 'tp-member-row tp-muted', style: { fontSize: '13px', padding: '10px 0' } },
            'No members yet.'));
      }
      return;
    }

    members.forEach((member, idx) => {
      const rowEl = memberRow(member, parentProp, idx, members, depth);
      container.appendChild(rowEl);

      // Recursive expansion: if this member is itself an object, render its
      // own members tree indented one level deeper.
      if (member.data_type === 'object' && member.members && member.members.length) {
        const subContainer = h('div', { class: 'tp-members' });
        renderMemberTree(subContainer, member.members, member, depth + 1);
        container.appendChild(subContainer);
      }
    });
  }

  // Build a single member row. The member object shape from the serializer is:
  //   { member_property_id, name, data_type, is_list, required, sort_order, members:[...] }
  function memberRow(member, parentProp, idx, siblings, depth) {
    const indentPx = depth * 20;
    const dragHandle = h('span', { class: 'tp-grip', title: 'Drag to reorder' }, '⠿');
    const badge = h('span', { class: typeBadgeClass(member.data_type) },
      typeBadge(member.data_type, member.is_list));
    const reqToggle = h('div', {
      class: 'tp-toggle' + (member.required ? ' on' : ''),
      role: 'switch',
      'aria-checked': member.required ? 'true' : 'false',
      title: member.required ? 'Required' : 'Optional',
    });

    const removeBtn = h('button', {
      class: 'btn btn-ghost btn-sm',
      onClick: () => doRemoveMember(parentProp, member),
    }, 'Remove');

    const row = h('div', {
      class: 'tp-member-row',
      style: { '--depth': String(depth), paddingLeft: indentPx + 'px' },
      draggable: 'true',
      dataset: { memberPropertyId: member.member_property_id },
    },
      dragHandle,
      h('span', { class: 'tp-pn', style: { flex: '1' } }, member.name),
      badge,
      reqToggle,
      removeBtn);

    // Drag-to-reorder within this sibling list.
    wireMemberDrag(row, siblings, parentProp);
    return row;
  }

  // Wire drag-and-drop reordering on a member row. On drop, call reorder_members.
  function wireMemberDrag(row, siblings, parentProp) {
    row.addEventListener('dragstart', (ev) => {
      ev.dataTransfer.setData('text/plain', row.dataset.memberPropertyId);
      row.classList.add('is-dragging');
    });
    row.addEventListener('dragend', () => row.classList.remove('is-dragging'));
    row.addEventListener('dragover', (ev) => ev.preventDefault());
    row.addEventListener('drop', async (ev) => {
      ev.preventDefault();
      const fromId = ev.dataTransfer.getData('text/plain');
      const toId = row.dataset.memberPropertyId;
      if (fromId === toId) return;
      const fi = siblings.findIndex((m) => m.member_property_id === fromId);
      const ti = siblings.findIndex((m) => m.member_property_id === toId);
      if (fi < 0 || ti < 0) return;
      const ordered = siblings.slice();
      const [moved] = ordered.splice(fi, 1);
      ordered.splice(ti, 0, moved);
      const orderedIds = ordered.map((m) => m.member_property_id);
      try {
        await persist('Members reordered', () =>
          api.doAction('reorder_members', {
            parent_property_id: parentProp.id,
            ordered_member_ids: orderedIds,
          }, state.getState().branch));
        await state.reload();
        repaint();
      } catch (err) { /* persist already surfaced the error banner */ }
    });
  }

  // Remove a member link (remove_member action — the member prop stays in the library).
  async function doRemoveMember(parentProp, member) {
    try {
      await persist('Member removed', () =>
        api.doAction('remove_member', {
          parent_property_id: parentProp.id,
          member_property_id: member.member_property_id,
        }, state.getState().branch));
      await state.reload();
      repaint();
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // ---- industry-standard "Add member" combo ----------------------------------------
  // Two paths:
  //   (1) Pick an existing library property → add_member action.
  //   (2) Type a new name (+ pick type + List toggle) → create_property then add_member.
  //
  // The combo popup is .tp-combo-pop (CSS owned by C6 — high z-index, no clip).
  function memberCombo(parentProp) {
    // All library props of the same kind that could be members (excluding those
    // already linked and the parent itself to avoid self-reference).
    const existingMemberIds = new Set((parentProp.members || []).map((m) => m.member_property_id));

    function availableLibProps() {
      const poolKind = parentProp.kind || 'event';
      const pool = (plan() && plan().properties && plan().properties[poolKind]) || [];
      return pool.filter((lp) => lp.id !== parentProp.id && !existingMemberIds.has(lp.id));
    }

    const input = h('input', {
      class: 'tp-combo-input',
      placeholder: 'Add member — search library or type a new name…',
    });

    // Inline "create new" controls: type selector + list toggle (shown only for new names).
    const newTypeSel = h('select', { class: 'select', style: { width: '120px' } },
      ...DATA_TYPES.map((t) => h('option', { value: t }, t)));
    const newListLabel = h('label', {
      class: 'tp-muted', style: { fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px' },
    });
    const newListCheck = h('input', { type: 'checkbox' });
    newListLabel.appendChild(newListCheck);
    newListLabel.appendChild(document.createTextNode(' List'));

    const newControls = h('div', {
      class: 'tp-combo-new-controls',
      style: { display: 'none', gap: '6px', alignItems: 'center', flexWrap: 'wrap', marginTop: '4px' },
    }, newTypeSel, newListLabel,
      h('button', {
        class: 'btn btn-secondary btn-sm',
        onClick: () => doCreateAndLink(parentProp, input, newTypeSel, newListCheck),
      }, 'Create & add'));

    const pop = h('div', { class: 'tp-combo-pop', style: { display: 'none' } });
    const wrap = h('div', { class: 'tp-combo', style: { marginTop: '12px' } }, input, newControls, pop);

    function hidePopup() { pop.style.display = 'none'; }
    function showPopup() { pop.style.display = 'block'; }

    function renderPopup(q) {
      const lib = availableLibProps();
      const hits = q
        ? lib.filter((lp) => lp.name.toLowerCase().includes(q.toLowerCase()))
        : lib;
      const limited = hits.slice(0, 10);
      const opts = limited.map((lp) => {
        const alreadyLinked = existingMemberIds.has(lp.id);
        return h('div', {
          class: 'tp-combo-opt' + (alreadyLinked ? ' is-disabled' : ''),
          onMousedown: alreadyLinked ? null : (ev) => { ev.preventDefault(); doLinkExisting(parentProp, lp); },
        },
          h('span', { class: 'tp-pn' }, lp.name),
          h('span', { class: typeBadgeClass(lp.data_type) }, typeBadge(lp.data_type, lp.is_list)));
      });

      // Show "Create new" row if the typed name doesn't exactly match a lib prop.
      const exactMatch = lib.some((lp) => lp.name.toLowerCase() === (q || '').toLowerCase());
      const showCreate = q && !exactMatch;
      if (showCreate) {
        opts.push(h('div', {
          class: 'tp-combo-opt tp-combo-new',
          onMousedown: (ev) => {
            ev.preventDefault();
            // Show inline create controls below the input; hide the popup.
            newControls.style.display = 'flex';
            hidePopup();
          },
        }, `Create new "${q}"`));
      }

      if (!opts.length) {
        mountAll(pop, [h('div', { class: 'tp-muted', style: { padding: '8px 10px' } },
          q ? 'No matches' : 'No library properties to add')]);
      } else {
        mountAll(pop, opts);
      }
      showPopup();
    }

    input.addEventListener('focus', () => renderPopup(input.value.trim()));
    input.addEventListener('input', () => {
      const q = input.value.trim();
      // Show inline controls only if user is explicitly entering a new name.
      if (!q) { newControls.style.display = 'none'; }
      renderPopup(q);
    });
    input.addEventListener('blur', () => setTimeout(() => { hidePopup(); }, 150));

    return wrap;
  }

  // Link an existing library property as a member.
  async function doLinkExisting(parentProp, libProp) {
    const existingMemberIds = new Set((parentProp.members || []).map((m) => m.member_property_id));
    if (existingMemberIds.has(libProp.id)) return; // already linked
    const sortOrder = (parentProp.members || []).length;
    try {
      await persist('Member added', () =>
        api.doAction('add_member', {
          parent_property_id: parentProp.id,
          member_property_id: libProp.id,
          required: false,
          sort_order: sortOrder,
        }, state.getState().branch));
      await state.reload();
      repaint();
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // Create a brand-new property (with chosen type + is_list) then link it as a member.
  async function doCreateAndLink(parentProp, nameInput, typeSel, listCheck) {
    const name = nameInput.value.trim();
    if (!name) { nameInput.focus(); return; }
    const data_type = typeSel.value || 'string';
    const is_list = listCheck.checked;
    const kind = parentProp.kind || 'event';
    const sortOrder = (parentProp.members || []).length;
    try {
      const created = await persist('Member created', () =>
        api.doAction('create_property', { name, data_type, is_list, kind }, state.getState().branch));
      await persist('Member added', () =>
        api.doAction('add_member', {
          parent_property_id: parentProp.id,
          member_property_id: created.id,
          required: false,
          sort_order: sortOrder,
        }, state.getState().branch));
      nameInput.value = '';
      await state.reload();
      repaint();
    } catch (err) { /* persist already surfaced the error banner */ }
  }

  // ---- Used by N events (design: TP Properties detail) — read table with
  // mono event-name links, REQUIRED/OPTIONAL, and live coverage %. ----
  function usedByCard(p) {
    const attachments = propertyAttachments(plan(), p);
    if (!attachments.length) {
      return h('div', { class: 'tp-pd-section' },
        h('div', { class: 'tp-pd-eyebrow' }, 'Used by · 0 events'),
        h('div', { class: 'tp-pd-card' }, h('div', { class: 'tp-pd-empty' }, 'Not attached to any event.')));
    }
    const rows = attachments
      .slice()
      .sort((a, b) => a.event.name.localeCompare(b.event.name))
      .map((a) => usedByRow(a));
    return h('div', { class: 'tp-pd-section' },
      h('div', { class: 'tp-pd-eyebrow' }, `Used by · ${attachments.length} event${attachments.length === 1 ? '' : 's'}`),
      h('div', { class: 'tp-pd-card' }, ...rows));
  }
  function usedByRow(a) {
    const cov = a.observation && a.observation.present_pct != null ? a.observation.present_pct : null;
    const covNode = cov == null
      ? h('span', { class: 'tp-pd-row-cov' }, '—')
      : h('span', { class: 'tp-pd-row-cov ' + (cov >= 90 ? 'good' : 'bad') }, `${cov}%${cov < 90 ? ' ⚠' : ''}`);
    return h('div', { class: 'tp-pd-row' },
      h('a', {
        class: 'tp-pd-row-name', href: '#',
        onClick: (ev) => {
          ev.preventDefault();
          if (dirty() && !confirm('Discard unsaved changes?')) return;
          state.setDirty(false);
          state.setView('events');
          state.select('event', a.event.id);
        },
      }, a.event.name),
      h('span', { class: 'tp-pd-row-req' }, a.required ? 'REQUIRED' : 'OPTIONAL'),
      covNode);
  }

  // ---- commit (buffered save): make server match draft ----
  async function doSave(p) {
    if (saving) return;
    if (!isValidRegex((draft.constraints && draft.constraints.regex) || '')) return;
    saving = true;
    repaint();
    try {
      await persist('Saved', () => commitProperty(p));
      await state.reload();
      const fresh = currentProp();
      server = draftOf(fresh || p);
      draft = clone(server);
      saving = false;
      state.setDirty(false);
      repaint();
    } catch (err) {
      saving = false;
      repaint(); // keep the draft; banner already shown by persist
    }
  }

  // One idempotent update_property carrying the assembled constraints. We rebuild
  // constraints from draft via buildConstraints so the persisted shape matches the
  // service contract (allowed_values/min/max/regex, or null when empty).
  function commitProperty(p) {
    const c = draft.constraints || {};
    const constraints = buildConstraints({
      enumRaw: (c.allowed_values || []).join(', '),
      min: c.min ?? '',
      max: c.max ?? '',
      regex: c.regex || '',
    });
    return api.doAction('update_property', {
      property_id: p.id,
      name: (draft.name || '').trim(),
      kind: draft.kind,
      data_type: draft.data_type,
      is_list: !!draft.is_list,
      is_pii: !!draft.is_pii,
      description: draft.description ? draft.description : null,
      constraints,
    }, state.getState().branch);
  }

  // ---- delete (canonical .modal-backdrop confirm, lists dependent events) ----
  function confirmDelete(p) {
    const events = usedByEvents(plan(), p);
    const overlay = h('div', { class: 'modal-backdrop is-open' });
    const body = events.length
      ? h('div', { class: 'tp-warn' },
        `In use by ${events.length} event${events.length === 1 ? '' : 's'}: ${events.map((e) => e.name).join(', ')}. `
        + 'Deleting also removes those attachments.')
      : h('div', { class: 'tp-muted', style: { fontSize: '13px' } }, 'This property is not attached to any event.');

    const cancel = h('button', { class: 'btn btn-ghost', onClick: () => overlay.remove() }, 'Cancel');
    const del = h('button', {
      class: 'btn btn-danger',
      onClick: async () => {
        overlay.remove();
        try {
          await persist('Property deleted', () => api.doAction('delete_property', { property_id: p.id }, state.getState().branch));
          server = null; draft = null;
          state.setDirty(false);
          state.select('property', null);
          await state.reload();
        } catch (err) { /* persist already surfaced the error banner */ }
      },
    }, 'Delete');

    const modal = h('div', { class: 'modal' },
      h('div', { class: 'modal-header' }, h('div', { class: 'modal-title' }, `Delete property "${p.name}"?`)),
      h('div', { class: 'modal-body' }, body),
      h('div', { class: 'modal-footer' }, cancel, del));
    overlay.appendChild(modal);
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    document.body.appendChild(overlay);
  }

  return () => { unsub(); if (drawer) drawer.destroy(); };
}

// ---- small DOM helpers --------------------------------------------------
function card(title, count, ...bodyNodes) {
  const headChildren = [h('h3', {}, title)];
  if (count) headChildren.push(h('span', { class: 'tp-ct' }, count));
  return h('div', { class: 'tp-card' },
    h('div', { class: 'tp-card-h' }, ...headChildren),
    ...bodyNodes);
}

function field(label, control) {
  return h('div', { class: 'tp-field' }, h('label', { class: 'tp-lbl' }, label), control);
}

// A labeled mockup-style toggle switch (.tp-toggle) inside a .tp-field.
function toggleRow(label, sub, on, onToggle) {
  const sw = h('div', { class: 'tp-toggle' + (on ? ' on' : ''), role: 'switch', 'aria-checked': on ? 'true' : 'false' });
  sw.addEventListener('click', onToggle);
  return h('div', { class: 'tp-field' },
    h('label', { class: 'tp-lbl' }, label),
    h('div', { style: { display: 'flex', alignItems: 'center', gap: '10px' } },
      sw,
      h('span', { class: 'tp-muted', style: { fontSize: '12px' } }, sub)));
}
