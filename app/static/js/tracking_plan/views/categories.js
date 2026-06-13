// app/static/js/tracking_plan/views/categories.js
// Categories manager (spec §5.5): event counts, create/rename/recolor/delete
// with delete-warning (events' category nulled).
import { getState, subscribe, reload } from "tp/state";
import { doAction } from "tp/api";
import { h, mount } from "tp/render";

const SWATCHES = ["#4A90E2", "#16a34a", "#d97706", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"];

function countByName(plan) {
  const m = {};
  (plan.events || []).forEach((e) => {
    if (e.category) m[e.category] = (m[e.category] || 0) + 1;
  });
  return m;
}

export function mountView(container) {
  // Declare render before subscribe to avoid TDZ (bug pattern #1).
  const render = () => {
    const st = getState();
    if (!st.plan) return;
    const counts = countByName(st.plan);
    const root = h("div", { class: "tp-pane is-active" });
    const detail = h("div", { class: "tp-detail", style: "flex:1" });
    const inner = h("div", { class: "tp-detail-inner", style: "max-width:760px" });

    const head = h("div", { class: "tp-d-head" });
    const t = h("div", { class: "tp-d-title" });
    t.appendChild(h("h2", { style: "margin:0;font-size:20px" }, "Categories"));
    head.appendChild(t);
    const a = h("div", { class: "tp-d-actions" });
    const create = h("button", { class: "btn btn-primary btn-sm" }, "+ New category");
    create.onclick = async () => {
      await doAction("create_category", { name: "New category", color: SWATCHES[0] }, st.branch);
      await reload();
    };
    a.appendChild(create);
    head.appendChild(a);
    inner.appendChild(head);

    const sec = h("div", { class: "tp-section" });
    const cats = st.plan.categories || [];
    if (!cats.length) {
      sec.appendChild(h("div", { class: "tp-row-empty" }, "No categories yet. Group events with a category."));
    } else {
      cats.forEach((c) => sec.appendChild(categoryRow(c, counts[c.name] || 0, st.branch)));
    }
    inner.appendChild(sec);
    detail.appendChild(inner);
    root.appendChild(detail);
    mount(container, root);
  };

  // Capture unsub so we can return a proper cleanup function (bug pattern #3).
  const unsub = subscribe(render);
  render();

  return () => { unsub(); };
}

function categoryRow(c, count, branch) {
  const row = h("div", { class: "tp-cat-row" });
  const dot = h("span", { class: "tp-cat-dot", style: `background:${c.color || "var(--text-subtle)"}` });
  row.appendChild(dot);
  const nameInp = h("input", { class: "tp-cat-name", value: c.name });
  row.appendChild(nameInp);

  // color picker — swatch buttons, not innerHTML (bug pattern #4 safe)
  const picker = h("div", { class: "tp-cat-swatches" });
  SWATCHES.forEach((col) => {
    const sw = h("button", {
      class: "tp-swatch" + (c.color === col ? " is-active" : ""),
      style: `background:${col}`,
      title: col,
    });
    sw.onclick = async () => {
      await doAction("update_category", { category_id: c.id, color: col }, branch);
      await reload();
    };
    picker.appendChild(sw);
  });
  row.appendChild(picker);

  row.appendChild(h("span", { class: "tp-cat-count" }, `${count} event${count === 1 ? "" : "s"}`));

  const save = h("button", { class: "btn btn-ghost btn-sm" }, "Rename");
  save.onclick = async () => {
    const name = nameInp.value.trim();
    if (!name || name === c.name) return;
    await doAction("update_category", { category_id: c.id, name }, branch);
    await reload();
  };
  row.appendChild(save);

  const del = h("button", { class: "btn btn-ghost btn-sm" }, "Delete");
  del.onclick = () => confirmDeleteCategory(c, count, branch);
  row.appendChild(del);
  return row;
}

function confirmDeleteCategory(c, count, branch) {
  const overlay = h("div", { class: "tp-modal-overlay" });
  const modal = h("div", { class: "tp-modal" });
  // Use h() for user-controlled text — no innerHTML (bug pattern #4)
  modal.appendChild(h("h3", {}, `Delete category "${c.name}"?`));
  if (count) {
    modal.appendChild(
      h(
        "div",
        { class: "tp-warn" },
        `${count} event${count === 1 ? "" : "s"} will be uncategorized (category cleared). The events are not deleted.`
      )
    );
  }
  const actions = h("div", { class: "tp-modal-actions" });
  const cancel = h("button", { class: "btn btn-ghost btn-sm" }, "Cancel");
  const delBtn = h("button", { class: "btn btn-danger btn-sm" }, "Delete");
  cancel.onclick = () => overlay.remove();
  delBtn.onclick = async () => {
    overlay.remove();
    await doAction("delete_category", { category_id: c.id }, branch);
    await reload();
  };
  actions.appendChild(cancel);
  actions.appendChild(delBtn);
  modal.appendChild(actions);
  overlay.appendChild(modal);
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
  document.body.appendChild(overlay);
}
