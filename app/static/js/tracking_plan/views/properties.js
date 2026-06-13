// app/static/js/tracking_plan/views/properties.js
// Property library & editor (spec §5.4): list by kind + search; bespoke editor
// with constraints, nesting, "used by N events", delete-in-use warning, drawer.
import { getState, subscribe, select, setView, reload } from "tp/state";
import { doAction } from "tp/api";
import { h, mount } from "tp/render";
import { mountDrawer } from "tp/comments";
import { typeBadge } from "tp/util/format";
import { isValidRegex, buildConstraints, usedByEvents } from "tp/util/constraints";

const KINDS = [
  ["event", "Event"],
  ["user", "User"],
  ["group", "Group"],
  ["system", "System"],
];
const DATA_TYPES = ["string", "int", "float", "boolean", "object", "array"];

function allProps(plan) {
  const p = plan.properties;
  return [...p.event, ...p.user, ...p.group, ...p.system];
}

export function mountView(container) {
  let search = "";

  // Declare render BEFORE subscribe to avoid TDZ — render is hoisted as a
  // function expression assigned to const, so we must declare it here first,
  // then pass it to subscribe below.
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    const root = h("div", { class: "tp-pane is-active" });
    root.appendChild(masterPanel(st, search, (q) => { search = q; render(); }));
    root.appendChild(detailPanel(st));
    mount(container, root);
  };

  // subscribe AFTER render is defined — no TDZ risk
  subscribe(render);
  render();
}

function masterPanel(st, search, onSearch) {
  const m = h("div", { class: "tp-master" });
  const head = h("div", { class: "tp-master-head" });
  const box = h("div", { class: "tp-search" });
  box.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>`;
  const inp = h("input", { placeholder: "Search properties", value: search });
  inp.oninput = (e) => onSearch(e.target.value);
  box.appendChild(inp);
  head.appendChild(box);

  const add = h("button", { class: "btn btn-primary btn-sm btn-block" }, "+ New property");
  add.onclick = async () => {
    await doAction("create_property", { name: "new_property", data_type: "string", kind: "event" }, st.branch);
    await reload();
    const fresh = getState().plan.properties.event.find((p) => p.name === "new_property");
    if (fresh) select("property", fresh.id);
  };
  head.appendChild(add);
  m.appendChild(head);

  const list = h("div", { class: "tp-master-list" });
  const sel = st.selection && st.selection.id;
  let any = false;
  KINDS.forEach(([k, label]) => {
    let items = st.plan.properties[k] || [];
    if (search) items = items.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));
    if (!items.length) return;
    any = true;
    list.appendChild(h("div", { class: "tp-cat-label" }, `${label} properties`));
    items.forEach((p) => {
      const row = h("div", { class: "tp-row" + (sel === p.id ? " is-active" : "") });
      const main = h("div", { class: "tp-row-main" });
      main.appendChild(h("div", { class: "tp-name" }, p.name));
      main.appendChild(h("div", { class: "tp-row-sub" }, p.description || "—"));
      row.appendChild(main);
      row.appendChild(h("div", { class: "tp-row-meta" }, p.data_type + (p.is_list ? "[]" : "")));
      row.onclick = () => select("property", p.id);
      list.appendChild(row);
    });
  });
  if (!any) list.appendChild(h("div", { class: "tp-row-empty" }, "No properties"));
  m.appendChild(list);
  return m;
}

function detailPanel(st) {
  const d = h("div", { class: "tp-detail" });
  const p = st.selection && st.selection.type === "property"
    ? allProps(st.plan).find((x) => x.id === st.selection.id)
    : null;
  if (!p) {
    const empty = h("div", { class: "tp-empty" });
    empty.appendChild(h("div", {}, "Select a property to edit its type & constraints."));
    d.appendChild(empty);
    return d;
  }
  const c = p.constraints || {};
  const inner = h("div", { class: "tp-detail-inner" });

  // header
  const head = h("div", { class: "tp-d-head" });
  const title = h("div", { class: "tp-d-title" });
  const nameInp = h("input", { class: "tp-titlefield", id: "pd-name", value: p.name });
  title.appendChild(nameInp);
  head.appendChild(title);
  const acts = h("div", { class: "tp-d-actions" });
  const drawerBtn = h("button", { class: "btn btn-ghost btn-sm" }, "💬 Comments");
  const delBtn = h("button", { class: "btn btn-ghost btn-sm" }, "Delete");
  const saveBtn = h("button", { class: "btn btn-primary btn-sm" }, "Save");
  acts.appendChild(drawerBtn);
  acts.appendChild(delBtn);
  acts.appendChild(saveBtn);
  head.appendChild(acts);
  inner.appendChild(head);

  // core fields
  const core = h("div", { class: "tp-section" });
  const grid = h("div", { class: "tp-fieldgrid" });
  grid.appendChild(field("Kind", selectEl("pd-kind", KINDS.map(([k]) => k), p.kind)));
  grid.appendChild(field("Data type", selectEl("pd-type", DATA_TYPES, p.data_type)));
  grid.appendChild(checkField("List / array", "pd-list", "values are a list", p.is_list));
  grid.appendChild(checkField("PII", "pd-pii", "contains personal data", p.is_pii));
  const desc = h("textarea", { id: "pd-desc" }, p.description || "");
  grid.appendChild(field("Description", desc, true));
  core.appendChild(grid);
  inner.appendChild(core);

  // constraint editor
  const cons = h("div", { class: "tp-section" });
  cons.appendChild(h("h3", {}, "Constraints"));
  const cwrap = h("div", { class: "tp-constraints" });
  const enumInp = h("input", {
    id: "pd-enum",
    class: "tp-mono-input",
    value: (c.allowed_values || []).join(", "),
  });
  cwrap.appendChild(field("Allowed values (enum) — comma separated", enumInp, true));
  cwrap.appendChild(field("Min", h("input", { id: "pd-min", type: "number", value: c.min ?? "" })));
  cwrap.appendChild(field("Max", h("input", { id: "pd-max", type: "number", value: c.max ?? "" })));
  const regexInp = h("input", { id: "pd-regex", class: "tp-mono-input", value: c.regex || "" });
  const regexHint = h("span", { class: "tp-regex-hint" }, "");
  const regexField = field("Regex / format", regexInp, true);
  regexField.appendChild(regexHint);
  const checkRegex = () => {
    const val = regexInp.value;
    const ok = isValidRegex(val);
    regexHint.textContent = val.trim() ? (ok ? "✓ valid regex" : "✗ invalid regex") : "";
    regexHint.className = "tp-regex-hint " + (ok ? "is-ok" : "is-bad");
    saveBtn.disabled = !!val.trim() && !ok;
  };
  regexInp.oninput = checkRegex;
  checkRegex();
  cwrap.appendChild(regexField);
  cons.appendChild(cwrap);
  inner.appendChild(cons);

  // nested members (object/array parents)
  inner.appendChild(membersSection(st, p));

  // used-by reverse refs
  inner.appendChild(usedBySection(st, p));

  // wiring
  saveBtn.onclick = async () => {
    const cons2 = buildConstraints({
      enumRaw: enumInp.value,
      min: inner.querySelector("#pd-min").value,
      max: inner.querySelector("#pd-max").value,
      regex: regexInp.value,
    });
    await doAction("update_property", {
      property_id: p.id,
      name: nameInp.value.trim(),
      kind: inner.querySelector("#pd-kind").value,
      data_type: inner.querySelector("#pd-type").value,
      is_list: inner.querySelector("#pd-list").checked,
      is_pii: inner.querySelector("#pd-pii").checked,
      description: desc.value || null,
      constraints: cons2,
    }, st.branch);
    await reload();
  };
  delBtn.onclick = () => confirmDelete(st, p);
  drawerBtn.onclick = () =>
    mountDrawer(document.body, { entityType: "property", entityId: p.id, branch: st.branch });

  d.appendChild(inner);
  return d;
}

function membersSection(st, parent) {
  const sec = h("div", { class: "tp-section" });
  sec.appendChild(h("h3", {}, "Nested members"));
  const isContainer = parent.data_type === "object" || parent.data_type === "array";
  if (!isContainer) {
    sec.appendChild(
      h("div", { class: "tp-muted", style: "font-size:13px" }, "Set data type to object or array to add members.")
    );
    return sec;
  }
  const children = allProps(st.plan).filter((x) => x.parent_property_id === parent.id);
  const table = h("table", { class: "tp-itable" });
  table.innerHTML = `<thead><tr><th>Name</th><th>Type</th><th></th></tr></thead>`;
  const tbody = h("tbody", {});
  if (!children.length) {
    tbody.innerHTML = `<tr><td colspan="3" class="tp-muted" style="padding:14px">No members yet.</td></tr>`;
  } else {
    children.forEach((child) => {
      const tr = h("tr", {});
      tr.innerHTML = `<td class="tp-pname">${child.name}</td><td><span class="tp-typebadge">${child.data_type}</span></td>`;
      const td = h("td", { class: "tp-cell-act" });
      const rm = h("button", { class: "btn btn-ghost btn-sm" }, "Remove");
      rm.onclick = async () => {
        await doAction("delete_property", { property_id: child.id }, st.branch);
        await reload();
      };
      td.appendChild(rm);
      tr.appendChild(td);
      tbody.appendChild(tr);
    });
  }
  table.appendChild(tbody);
  sec.appendChild(table);

  const addRow = h("div", { class: "tp-inline-add" });
  const nm = h("input", { placeholder: "member name", style: "width:150px" });
  const ty = selectEl("", DATA_TYPES, "string");
  const btn = h("button", { class: "btn btn-secondary btn-sm" }, "Add member");
  btn.onclick = async () => {
    const name = nm.value.trim();
    if (!name) return;
    await doAction("create_property", {
      name,
      data_type: ty.value,
      kind: parent.kind,
      parent_property_id: parent.id,
    }, st.branch);
    await reload();
  };
  addRow.appendChild(nm);
  addRow.appendChild(ty);
  addRow.appendChild(btn);
  sec.appendChild(addRow);
  return sec;
}

function usedBySection(st, p) {
  const sec = h("div", { class: "tp-section" });
  const events = usedByEvents(st.plan, p);
  sec.appendChild(h("h3", {}, `Used by ${events.length} event${events.length === 1 ? "" : "s"}`));
  if (!events.length) {
    sec.appendChild(h("div", { class: "tp-muted", style: "font-size:13px" }, "Not attached to any event."));
    return sec;
  }
  const wrap = h("div", { class: "tp-usedby" });
  events.forEach((e) => {
    const chip = h("button", { class: "tp-usedby-chip" }, e.name);
    chip.onclick = () => {
      setView("events");
      select("event", e.id);
    };
    wrap.appendChild(chip);
  });
  sec.appendChild(wrap);
  return sec;
}

function confirmDelete(st, p) {
  const events = usedByEvents(st.plan, p);
  const overlay = h("div", { class: "tp-modal-overlay" });
  const modal = h("div", { class: "tp-modal" });
  modal.appendChild(h("h3", {}, `Delete property "${p.name}"?`));
  if (events.length) {
    modal.appendChild(
      h(
        "div",
        { class: "tp-warn" },
        `In use by ${events.length} event${events.length === 1 ? "" : "s"}: ${events
          .map((e) => e.name)
          .join(", ")}. Deleting also removes those attachments.`
      )
    );
  }
  const row = h("div", { class: "tp-modal-actions" });
  const cancel = h("button", { class: "btn btn-ghost btn-sm" }, "Cancel");
  const del = h("button", { class: "btn btn-danger btn-sm" }, "Delete");
  cancel.onclick = () => overlay.remove();
  del.onclick = async () => {
    overlay.remove();
    await doAction("delete_property", { property_id: p.id }, st.branch);
    select("property", null);
    await reload();
  };
  row.appendChild(cancel);
  row.appendChild(del);
  modal.appendChild(row);
  overlay.appendChild(modal);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}

// ---- small DOM helpers (local) ----
function field(label, control, full) {
  const f = h("div", { class: "tp-field" + (full ? " tp-col-2" : "") });
  f.appendChild(h("label", {}, label));
  f.appendChild(control);
  return f;
}

function checkField(label, id, text, checked) {
  const f = h("div", { class: "tp-field" });
  f.appendChild(h("label", {}, label));
  const wrap = h("label", { class: "tp-checkline" });
  const cb = h("input", { type: "checkbox", id });
  if (checked) cb.checked = true;
  wrap.appendChild(cb);
  wrap.appendChild(document.createTextNode(" " + text));
  f.appendChild(wrap);
  return f;
}

function selectEl(id, opts, selected) {
  // Always pass an attrs object as 2nd arg to h() — never spread opts directly
  // (the first spread item would land in the attrs slot and be silently dropped).
  const s = h("select", id ? { id } : {});
  opts.forEach((o) => {
    const op = h("option", { value: o }, o);
    if (o === selected) op.selected = true;
    s.appendChild(op);
  });
  return s;
}
