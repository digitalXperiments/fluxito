// app/static/js/tracking_plan/util/editor.js
// Shared infrastructure for the EXPLICIT BUFFERED-SAVE model (no autosave).
//
// Every editor view holds a local DRAFT and a SERVER snapshot. The editor
// renders from the draft; every field/collection edit mutates the draft ONLY
// and re-renders. `dirty = !deepEqual(draft, server)`. Saving runs the view's
// commit() (which makes the server match the draft via idempotent/replace-all
// backend actions), reloads, and re-snapshots. Nothing hits the API on edit.
//
// This module owns the small reusable pieces of that pattern:
//   - clone(obj)          deep clone for snapshotting draft/server
//   - deepEqual(a, b)     structural compare to compute `dirty`
//   - isDirty(draft, srv) convenience alias = !deepEqual
//   - saveCluster({...})  the header save UI (● Unsaved / Discard / Save)
//   - editorHead({...})   optional convenience header (.tp-ed-head)

import { h } from 'tp/render';

// Deep clone of plain dicts/arrays (the editable-fields + collections snapshot).
// structuredClone handles dates/maps; JSON round-trip is the fallback for the
// rare environment without it. Our payloads are plain JSON, so both are safe.
export function clone(obj) {
  if (obj == null) return obj;
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(obj);
    } catch (e) {
      /* fall through to JSON */
    }
  }
  return JSON.parse(JSON.stringify(obj));
}

// Structural equality. JSON.stringify compare is acceptable for these plain
// dicts (the draft/server snapshots are JSON-shaped: scalars, arrays, objects).
export function deepEqual(a, b) {
  if (a === b) return true;
  try {
    return JSON.stringify(a) === JSON.stringify(b);
  } catch (e) {
    return false;
  }
}

// Convenience: a view computes `dirty = isDirty(draft, server)` after each edit.
export function isDirty(draft, server) {
  return !deepEqual(draft, server);
}

// The header save cluster (.tp-savecluster).
//   dirty → '● Unsaved' (.tp-unsaved) + 'Discard' ghost (onDiscard)
//           + 'Save changes' primary (onSave; 'Saving…' + disabled when saving)
//   clean → muted 'Saved' span + a disabled 'Save changes' button
export function saveCluster({ dirty, saving, onSave, onDiscard }) {
  const box = h('div', { class: 'tp-savecluster' });
  if (dirty) {
    box.appendChild(h('span', { class: 'tp-unsaved' }, '● Unsaved'));
    box.appendChild(
      h(
        'button',
        {
          class: 'btn btn-ghost btn-sm',
          disabled: !!saving,
          onClick: () => {
            if (!saving && typeof onDiscard === 'function') onDiscard();
          },
        },
        'Discard',
      ),
    );
    box.appendChild(
      h(
        'button',
        {
          class: 'btn btn-primary btn-sm',
          disabled: !!saving,
          onClick: () => {
            if (!saving && typeof onSave === 'function') onSave();
          },
        },
        saving ? 'Saving…' : 'Save changes',
      ),
    );
  } else {
    box.appendChild(h('span', { class: 'tp-saved-muted' }, 'Saved'));
    box.appendChild(h('button', { class: 'btn btn-primary btn-sm', disabled: true }, 'Save changes'));
  }
  return box;
}

// Optional convenience editor header (.tp-ed-head). Views may use this or
// compose saveCluster directly into their own header markup.
//   kicker  — small uppercase label above the name (e.g. 'Event')
//   name    — the mono entity name/id
//   chips   — array of chip DOM nodes (or single node) rendered after the id
//   actions — array of action DOM nodes (e.g. Comments, Delete) before the
//             save cluster, separated by a .tp-divv divider
//   dirty/saving/onSave/onDiscard — forwarded to saveCluster
export function editorHead({ kicker, name, chips, actions, dirty, saving, onSave, onDiscard }) {
  const idBlock = h(
    'div',
    { class: 'tp-ed-id' },
    kicker ? h('div', { class: 'tp-ed-kicker' }, kicker) : null,
    h('div', { class: 'tp-ed-name' }, name == null ? '' : String(name)),
  );

  const chipList = Array.isArray(chips) ? chips.filter(Boolean) : chips ? [chips] : [];
  const chipsNode = chipList.length ? h('div', { class: 'tp-ed-chips' }, ...chipList) : null;

  const actionList = Array.isArray(actions) ? actions.filter(Boolean) : actions ? [actions] : [];
  const actionsNode = h(
    'div',
    { class: 'tp-ed-actions' },
    ...actionList,
    actionList.length ? h('div', { class: 'tp-divv' }) : null,
    saveCluster({ dirty, saving, onSave, onDiscard }),
  );

  return h('div', { class: 'tp-ed-head' }, h('div', { class: 'tp-ed-id-row' }, idBlock, chipsNode, actionsNode));
}
