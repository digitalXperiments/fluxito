// app/static/js/tracking_plan/views/bundles.js
// Drag-reorder bundle builder (spec §5.6): bundle detail with drag-reorder,
// toggle required, remove, add-combobox, and attach-to-event.
import { getState, subscribe, select, reload } from 'tp/state';
import { doAction } from 'tp/api';
import { h, mount } from 'tp/render';

export function mountView(container) {
  // Declare render BEFORE subscribe (avoids TDZ on first notification).
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    const root = h('div', { class: 'tp-pane is-active' });
    root.appendChild(masterPane(st));
    root.appendChild(detailPane(st));
    mount(container, root);
  };
  // Capture unsub; return cleanup so the router can tear this view down.
  const unsub = subscribe(render);
  render();
  return () => { unsub(); };
}

function masterPane(st) {
  const m = h('div', { class: 'tp-master' });
  const head = h('div', { class: 'tp-master-head' });
  const add = h('button', { class: 'btn btn-primary btn-sm btn-block' }, '+ New bundle');
  add.onclick = async () => {
    await doAction('create_bundle', { name: 'New bundle' }, st.branch);
    await reload();
    const fresh = getState().plan.bundles.find((b) => b.name === 'New bundle');
    if (fresh) select('bundle', fresh.id);
  };
  head.appendChild(add);
  m.appendChild(head);
  const list = h('div', { class: 'tp-master-list' });
  const selId = st.selection && st.selection.id;
  const bundles = st.plan.bundles || [];
  if (!bundles.length) {
    list.appendChild(h('div', { class: 'tp-row-empty' }, 'No bundles yet.'));
  }
  bundles.forEach((b) => {
    const row = h('div', { class: 'tp-row' + (selId === b.id ? ' is-active' : '') });
    const main = h('div', { class: 'tp-row-main' });
    main.appendChild(h('div', { class: 'tp-name' }, b.name));
    main.appendChild(h('div', { class: 'tp-row-sub' }, b.description || '—'));
    row.appendChild(main);
    row.appendChild(h('div', { class: 'tp-row-meta' }, `${b.properties.length}p`));
    row.onclick = () => select('bundle', b.id);
    list.appendChild(row);
  });
  m.appendChild(list);
  return m;
}

function detailPane(st) {
  const d = h('div', { class: 'tp-detail' });
  const b = (st.plan.bundles || []).find(
    (x) => x.id === (st.selection && st.selection.id),
  );
  if (!b) {
    const empty = h('div', { class: 'tp-empty' });
    empty.appendChild(h('div', {}, 'Select a bundle to build it.'));
    d.appendChild(empty);
    return d;
  }
  const inner = h('div', { class: 'tp-detail-inner' });

  // ── Header: name input + Save/Delete ──────────────────────────────────
  const head = h('div', { class: 'tp-d-head' });
  const title = h('div', { class: 'tp-d-title' });
  const nameInp = h('input', { class: 'tp-titlefield', value: b.name });
  title.appendChild(nameInp);
  head.appendChild(title);
  const acts = h('div', { class: 'tp-d-actions' });
  const save = h('button', { class: 'btn btn-primary btn-sm' }, 'Save');
  const del = h('button', { class: 'btn btn-ghost btn-sm' }, 'Delete');
  save.onclick = async () => {
    await doAction('update_bundle', { bundle_id: b.id, name: nameInp.value.trim() }, st.branch);
    await reload();
  };
  del.onclick = async () => {
    await doAction('delete_bundle', { bundle_id: b.id }, st.branch);
    select('bundle', null);
    await reload();
  };
  acts.appendChild(save);
  acts.appendChild(del);
  head.appendChild(acts);
  inner.appendChild(head);

  // ── Property list (drag-reorder) ──────────────────────────────────────
  const sec = h('div', { class: 'tp-section' });
  sec.appendChild(h('h3', {}, `Properties (${b.properties.length})`));
  const listEl = h('div', { class: 'tp-bundle-list' });
  const props = b.properties.slice().sort((x, y) => x.sort_order - y.sort_order);
  props.forEach((p, idx) => listEl.appendChild(bundleRow(b, p, idx, props, st.branch)));
  sec.appendChild(listEl);

  // ── Add-combobox over the event-property library ──────────────────────
  const inBundle = new Set(b.properties.map((p) => p.property_id));
  const candidates = (st.plan.properties.event || []).filter((p) => !inBundle.has(p.id));
  const addBar = h('div', { class: 'tp-inline-add' });
  // attrs object {} as second arg — avoids dropped-first-option bug
  const propSel = h('select', {});
  propSel.appendChild(h('option', { value: '' }, 'add a library property…'));
  candidates.forEach((p) =>
    propSel.appendChild(h('option', { value: p.id }, `${p.name} · ${p.data_type}`)),
  );
  const addBtn = h('button', { class: 'btn btn-secondary btn-sm' }, 'Add');
  addBtn.onclick = async () => {
    if (!propSel.value) return;
    await doAction(
      'add_property_to_bundle',
      { bundle_id: b.id, property_id: propSel.value, sort_order: b.properties.length },
      st.branch,
    );
    await reload();
  };
  addBar.appendChild(propSel);
  addBar.appendChild(addBtn);
  sec.appendChild(addBar);
  inner.appendChild(sec);

  // ── Attach bundle to event ────────────────────────────────────────────
  const attachSec = h('div', { class: 'tp-section' });
  attachSec.appendChild(h('h3', {}, 'Attach to event'));
  const aBar = h('div', { class: 'tp-inline-add' });
  // attrs object {} as second arg — avoids dropped-first-option bug
  const evSel = h('select', {});
  evSel.appendChild(h('option', { value: '' }, 'choose an event…'));
  (st.plan.events || []).forEach((e) =>
    evSel.appendChild(h('option', { value: e.id }, e.name)),
  );
  const aBtn = h('button', { class: 'btn btn-secondary btn-sm' }, 'Attach bundle');
  aBtn.onclick = async () => {
    if (!evSel.value) return;
    await doAction(
      'attach_bundle_to_event',
      { event_id: evSel.value, bundle_id: b.id },
      st.branch,
    );
    await reload();
  };
  aBar.appendChild(evSel);
  aBar.appendChild(aBtn);
  attachSec.appendChild(aBar);
  inner.appendChild(attachSec);

  d.appendChild(inner);
  return d;
}

function bundleRow(bundle, p, idx, ordered, branch) {
  const row = h('div', { class: 'tp-bundle-row', draggable: 'true' });
  row.dataset.idx = String(idx);
  row.appendChild(h('span', { class: 'tp-drag-handle' }, '⠇'));
  // Use h() for user text — never innerHTML (XSS guard)
  row.appendChild(h('div', { class: 'tp-bundle-pname' }, p.name));
  row.appendChild(h('span', { class: 'tp-typebadge' }, p.data_type));

  const reqWrap = h('label', { class: 'tp-checkline' });
  const req = h('input', { type: 'checkbox', checked: !!p.required });
  req.onchange = async () => {
    await doAction(
      'add_property_to_bundle',
      { bundle_id: bundle.id, property_id: p.property_id, required: req.checked, sort_order: p.sort_order },
      branch,
    );
    await reload();
  };
  reqWrap.appendChild(req);
  reqWrap.appendChild(document.createTextNode(' required'));
  row.appendChild(reqWrap);

  const rm = h('button', { class: 'btn btn-ghost btn-sm' }, 'Remove');
  rm.onclick = async () => {
    await doAction(
      'remove_property_from_bundle',
      { bundle_id: bundle.id, property_id: p.property_id },
      branch,
    );
    await reload();
  };
  row.appendChild(rm);

  // ── HTML5 drag-reorder ────────────────────────────────────────────────
  row.ondragstart = (e) => {
    e.dataTransfer.setData('text/plain', String(idx));
    row.classList.add('is-dragging');
  };
  row.ondragend = () => row.classList.remove('is-dragging');
  row.ondragover = (e) => { e.preventDefault(); row.classList.add('is-dragover'); };
  row.ondragleave = () => row.classList.remove('is-dragover');
  row.ondrop = async (e) => {
    e.preventDefault();
    row.classList.remove('is-dragover');
    const from = Number(e.dataTransfer.getData('text/plain'));
    const to = idx;
    if (from === to) return;
    const next = ordered.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    // Re-upsert each row with its new sort_order
    // (add_property_to_bundle is an idempotent upsert).
    for (let i = 0; i < next.length; i++) {
      await doAction(
        'add_property_to_bundle',
        { bundle_id: bundle.id, property_id: next[i].property_id, required: next[i].required, sort_order: i },
        branch,
      );
    }
    await reload();
  };

  return row;
}
