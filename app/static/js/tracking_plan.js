/* Tracking Plan client app. Refined master-detail UI over the structured
   tracking_plan API. Branch-aware. Vanilla JS, no deps. */
(function () {
  "use strict";
  const root = document.getElementById("tp-app");
  if (!root) return;
  const PID = root.dataset.pid;
  const ME = root.dataset.uid || "";
  const BASE = `/api/projects/${PID}/tracking-plan`;
  const isAdmin = root.dataset.admin === "true";

  // ---- state ----
  const S = {
    branch: "main", branches: [], plan: null, tab: "events",
    selEvent: null, selProp: null, dirtyEvent: false, comments: [], diff: null, versions: [],
  };

  // ---- helpers ----
  const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const initials = (id) => (id || "?").replace(/[^a-z0-9]/gi, "").slice(0, 2).toUpperCase();
  let bannerTimer;
  function banner(msg, kind = "ok") {
    const b = document.getElementById("tp-banner");
    if (!msg) { b.style.display = "none"; b.innerHTML = ""; return; }
    b.className = "tp-banner " + kind; b.style.display = "flex"; b.textContent = msg;
    clearTimeout(bannerTimer);
    if (kind === "ok") bannerTimer = setTimeout(() => banner(""), 3200);
  }

  // ---- API ----
  async function getJSON(path) { const r = await fetch(BASE + path); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status); return r.json(); }
  async function action(act, params = {}) {
    const body = { action: act, params: { ...params, branch: S.branch } };
    const r = await fetch(BASE + "/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    if (j.error) { banner(`${j.error_type}: ${j.message}`, "err"); return null; }
    return j;
  }
  const branchQS = () => (S.branch && S.branch !== "main" ? `?branch=${encodeURIComponent(S.branch)}` : "");

  async function loadPlan() {
    S.plan = await getJSON("/tracking-plan" + branchQS());
    const lb = await action("list_branches"); S.branches = lb ? lb.branches : [{ name: "main", is_main: true }];
    render();
  }
  async function refresh() { S.plan = await getJSON("/tracking-plan" + branchQS()); render(); }

  // ---- lookups ----
  const allProps = () => { const p = S.plan.properties; return [...p.event, ...p.user, ...p.group, ...p.system]; };
  const propByName = (n) => allProps().find((x) => x.name === n);
  const catId = (name) => { const c = S.plan.categories.find((x) => x.name === name); return c ? c.id : null; };
  const destByName = (n) => S.plan.destinations.find((x) => x.name === n);
  const curBranchObj = () => S.branches.find((b) => b.name === S.branch || b.id === S.branch) || { name: S.branch, is_main: S.branch === "main" };

  // ======================================================================
  // RENDER
  // ======================================================================
  function render() {
    if (!S.plan) return;
    root.innerHTML = "";
    root.appendChild(bar());
    root.appendChild(tabs());
    const body = h(`<div class="tp-body"></div>`);
    body.appendChild(pane("events", S.tab === "events", eventsView));
    body.appendChild(pane("properties", S.tab === "properties", propsView));
    body.appendChild(pane("bundles", S.tab === "bundles", bundlesView));
    body.appendChild(pane("sources", S.tab === "sources", sourcesView));
    body.appendChild(pane("destinations", S.tab === "destinations", destsView));
    body.appendChild(pane("metrics", S.tab === "metrics", metricsView));
    body.appendChild(pane("changes", S.tab === "changes", changesView));
    body.appendChild(pane("versions", S.tab === "versions", versionsView));
    root.appendChild(body);
  }
  function pane(name, active, fn) {
    const p = h(`<div class="tp-pane ${active ? "is-active" : ""}" data-pane="${name}"></div>`);
    if (active) try { fn(p); } catch (e) { p.appendChild(h(`<div class="tp-empty">UI error: ${esc(e.message)}</div>`)); console.error(e); }
    return p;
  }
  function setTab(t) { S.tab = t; render(); }

  // ---- branch bar ----
  function bar() {
    const b = curBranchObj();
    const onMain = b.is_main || S.branch === "main";
    const el = h(`<div class="tp-bar">
      <h1>Tracking Plan <span class="tp-sub">structured</span></h1>
      <div class="tp-branchpick" id="tp-bp">
        <button id="tp-bp-btn"><svg class="tp-branch-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="7" r="2.5"/><path d="M6 8.5v7M18 9.5c0 4-6 2-6 6"/></svg><b>${esc(b.name)}</b><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg></button>
        <div class="tp-menu" id="tp-bp-menu"></div>
      </div>
      ${!onMain ? `<span class="tp-review" data-s="${esc(b.review_status || "draft")}">${esc((b.review_status || "draft").replace(/_/g, " "))}</span>` : ""}
      <div class="tp-spacer"></div>
      <div class="tp-bar-actions"></div>
    </div>`);
    // branch menu
    const menu = el.querySelector("#tp-bp-menu");
    S.branches.forEach((br) => {
      const it = h(`<div class="tp-menu-item ${br.name === S.branch ? "is-active" : ""}"><span class="tp-mono">${esc(br.name)}</span>${br.is_main ? '<span class="tp-menu-meta">main</span>' : `<span class="tp-menu-meta">${esc(br.status || "")}</span>`}</div>`);
      it.onclick = () => { S.branch = br.name; S.selEvent = S.selProp = null; S.tab = "events"; menu.classList.remove("is-open"); loadPlan(); };
      menu.appendChild(it);
    });
    menu.appendChild(h(`<div class="tp-menu-sep"></div>`));
    const nb = h(`<div class="tp-menu-item"><span>+ New branch…</span></div>`); nb.onclick = createBranch; menu.appendChild(nb);
    el.querySelector("#tp-bp-btn").onclick = (e) => { e.stopPropagation(); menu.classList.toggle("is-open"); };
    document.addEventListener("click", () => menu.classList.remove("is-open"), { once: true });

    // actions
    const acts = el.querySelector(".tp-bar-actions");
    acts.style.cssText = "display:flex;gap:8px;align-items:center";
    const btn = (label, cls, fn) => { const x = h(`<button class="btn ${cls} btn-sm">${label}</button>`); x.onclick = fn; return x; };
    acts.appendChild(btn("Validate", "btn-ghost", validate));
    const ex = h(`<a class="btn btn-ghost btn-sm" href="${BASE}/export.md${branchQS()}" target="_blank">Export</a>`); acts.appendChild(ex);
    if (onMain) {
      acts.appendChild(btn("Publish", "btn-primary", publish));
    } else {
      acts.appendChild(btn("View changes", "btn-secondary", () => setTab("changes")));
      const b2 = curBranchObj();
      const rs = b2.review_status || "draft";
      if (rs === "draft") acts.appendChild(btn("Request review", "btn-secondary", () => setReview("ready_for_review")));
      if (rs === "ready_for_review" && isAdmin) { acts.appendChild(btn("Approve", "btn-secondary", () => setReview("approved"))); acts.appendChild(btn("Request changes", "btn-ghost", () => setReview("changes_requested"))); }
      if (isAdmin) acts.appendChild(btn("Merge & publish", "btn-primary", mergeBranch));
    }
    return el;
  }

  function tabs() {
    const counts = {
      events: S.plan.events.length, properties: allProps().length, bundles: (S.plan.bundles || []).length,
      sources: S.plan.sources.length, destinations: S.plan.destinations.length, metrics: S.plan.metrics.length,
    };
    const defs = [["events", "Events"], ["properties", "Properties"], ["bundles", "Bundles"], ["sources", "Sources"], ["destinations", "Destinations"], ["metrics", "Metrics"]];
    if (S.branch !== "main") defs.push(["changes", "Changes"]);
    defs.push(["versions", "Versions"]);
    const el = h(`<div class="tp-tabs"></div>`);
    defs.forEach(([k, label]) => {
      const c = counts[k];
      const t = h(`<button class="tp-tab ${S.tab === k ? "is-active" : ""}">${label}${c != null ? `<span class="tp-count">${c}</span>` : ""}</button>`);
      t.onclick = () => setTab(k); el.appendChild(t);
    });
    return el;
  }

  // ======================================================================
  // EVENTS  (master-detail)
  // ======================================================================
  function eventsView(p) {
    const master = h(`<div class="tp-master"></div>`);
    master.appendChild(h(`<div class="tp-master-head">
      <div class="tp-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg><input id="tp-ev-search" placeholder="Search events"/></div>
      <button class="btn btn-primary btn-sm btn-block" id="tp-ev-new">+ New event</button>
    </div>`));
    const list = h(`<div class="tp-master-list" id="tp-ev-list"></div>`);
    master.appendChild(list);
    p.appendChild(master);
    const detail = h(`<div class="tp-detail" id="tp-ev-detail"></div>`);
    p.appendChild(detail);
    renderEventList(); renderEventDetail();
    master.querySelector("#tp-ev-search").oninput = (e) => renderEventList(e.target.value);
    master.querySelector("#tp-ev-new").onclick = newEvent;
  }
  function renderEventList(q = "") {
    const list = document.getElementById("tp-ev-list"); if (!list) return;
    let evs = S.plan.events.slice().sort((a, b) => a.name.localeCompare(b.name));
    if (q) evs = evs.filter((e) => e.name.toLowerCase().includes(q.toLowerCase()) || (e.category || "").toLowerCase().includes(q.toLowerCase()));
    list.innerHTML = "";
    if (!evs.length) { list.appendChild(h(`<div class="tp-row-empty">No events yet</div>`)); return; }
    const byCat = {};
    evs.forEach((e) => { (byCat[e.category || "Uncategorized"] = byCat[e.category || "Uncategorized"] || []).push(e); });
    Object.keys(byCat).sort().forEach((cat) => {
      list.appendChild(h(`<div class="tp-cat-label">${esc(cat)}</div>`));
      byCat[cat].forEach((e) => {
        const verified = e.sources.filter((s) => s.implementation_status === "verified").length;
        const row = h(`<div class="tp-row ${S.selEvent === e.id ? "is-active" : ""}">
          <div class="tp-row-main"><div class="tp-name">${esc(e.name)}</div><div class="tp-row-sub">${esc(e.purpose || e.display_name || "—")}</div></div>
          <div class="tp-row-meta">${e.properties.length}p</div></div>`);
        row.onclick = () => { if (S.dirtyEvent && !confirm("Discard unsaved changes?")) return; S.selEvent = e.id; S.dirtyEvent = false; renderEventList(q); renderEventDetail(); };
        list.appendChild(row);
      });
    });
  }
  function renderEventDetail() {
    const d = document.getElementById("tp-ev-detail"); if (!d) return;
    const e = S.plan.events.find((x) => x.id === S.selEvent);
    if (!e) { d.innerHTML = `<div class="tp-empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg><div>Select an event, or create one.</div></div>`; return; }
    const catOpts = `<option value="">(no category)</option>` + S.plan.categories.map((c) => `<option ${e.category === c.name ? "selected" : ""}>${esc(c.name)}</option>`).join("");
    d.innerHTML = "";
    const inner = h(`<div class="tp-detail-inner"></div>`);
    inner.appendChild(h(`<div class="tp-d-head">
      <div class="tp-d-title"><input class="tp-titlefield" id="ed-name" value="${esc(e.name)}"/></div>
      <div class="tp-d-actions"><span class="tp-dirty-dot"></span>
        <button class="btn btn-ghost btn-sm" id="ed-del">Delete</button>
        <button class="btn btn-primary btn-sm" id="ed-save">Save</button></div>
    </div>`));
    // core fields
    const fields = h(`<div class="tp-section"><div class="tp-fieldgrid">
      <div class="tp-field"><label>Display name</label><input id="ed-display" value="${esc(e.display_name || "")}"/></div>
      <div class="tp-field"><label>Category</label><select id="ed-cat">${catOpts}</select></div>
      <div class="tp-field tp-col-2"><label>Description — when does this fire?</label><textarea id="ed-desc">${esc(e.description || "")}</textarea></div>
      <div class="tp-field"><label>Trigger type</label><input class="tp-mono-input" id="ed-trig" value="${esc(e.trigger_type || "")}" placeholder="click / pageview / …"/></div>
      <div class="tp-field"><label>Purpose / KPI</label><input id="ed-purpose" value="${esc(e.purpose || "")}"/></div>
      <div class="tp-field"><label>Business owner</label><input id="ed-ob" value="${esc(e.owner_business || "")}"/></div>
      <div class="tp-field"><label>Technical owner</label><input id="ed-ot" value="${esc(e.owner_technical || "")}"/></div>
      <div class="tp-field tp-col-2"><label>Tags</label><div class="tp-tags" id="ed-tags"></div></div>
    </div></div>`);
    inner.appendChild(fields);
    // tags
    const tagWrap = fields.querySelector("#ed-tags"); let tags = (e.tags || []).slice();
    const renderTags = () => { tagWrap.querySelectorAll(".tp-chip").forEach((x) => x.remove()); tags.forEach((t, i) => { const c = h(`<span class="tp-chip">${esc(t)}<button>✕</button></span>`); c.querySelector("button").onclick = () => { tags.splice(i, 1); renderTags(); markDirty(); }; tagWrap.insertBefore(c, tagInput); }); };
    const tagInput = h(`<input placeholder="add tag…"/>`); tagWrap.appendChild(tagInput);
    tagInput.onkeydown = (ev) => { if (ev.key === "Enter" && tagInput.value.trim()) { tags.push(tagInput.value.trim()); tagInput.value = ""; renderTags(); markDirty(); } };
    renderTags();

    const markDirty = () => { S.dirtyEvent = true; inner.querySelector(".tp-d-head").parentElement.closest(".tp-detail-inner").classList.add("tp-dirty"); };
    fields.querySelectorAll("input,select,textarea").forEach((x) => x.addEventListener("input", markDirty));
    inner.querySelector("#ed-name").addEventListener("input", markDirty);

    inner.querySelector("#ed-save").onclick = async () => {
      const r = await action("update_event", {
        event_id: e.id, name: inner.querySelector("#ed-name").value.trim(),
        display_name: inner.querySelector("#ed-display").value || null,
        description: inner.querySelector("#ed-desc").value || null,
        category_id: catId(inner.querySelector("#ed-cat").value) || null,
        trigger_type: inner.querySelector("#ed-trig").value || null,
        purpose: inner.querySelector("#ed-purpose").value || null,
        owner_business: inner.querySelector("#ed-ob").value || null,
        owner_technical: inner.querySelector("#ed-ot").value || null,
        tags: tags,
      });
      if (r) { S.dirtyEvent = false; banner("Event saved", "ok"); await refresh(); }
    };
    inner.querySelector("#ed-del").onclick = async () => { if (confirm(`Delete event "${e.name}"?`)) { await action("delete_event", { event_id: e.id }); S.selEvent = null; await refresh(); } };

    // ---- properties section ----
    inner.appendChild(propertiesSection(e));
    // ---- sources section ----
    inner.appendChild(sourcesSection(e));
    // ---- destinations section ----
    inner.appendChild(destSection(e));
    // ---- comments ----
    inner.appendChild(commentsSection("event", e.id));
    d.appendChild(inner);
    loadComments("event", e.id);
  }

  function propertiesSection(e) {
    const sec = h(`<div class="tp-section"><h3>Properties <span class="tp-sec-count">${e.properties.length}</span></h3></div>`);
    const rows = e.properties.map((p) => `<tr>
      <td class="tp-pname">${esc(p.name)}${p.required ? '' : ' <span class="tp-muted" style="font-size:11px">optional</span>'}</td>
      <td><span class="tp-typebadge">${esc(p.data_type)}${p.is_list ? '<span class="tp-list"> []</span>' : ''}</span></td>
      <td><input data-ex="${esc(p.name)}" value="${esc(p.example || "")}" placeholder="example" style="width:100%;font:inherit;font-size:12px;border:1px solid var(--border);border-radius:6px;padding:4px 7px;background:var(--surface);color:var(--text)"/></td>
      <td class="tp-cell-act"><button class="btn btn-ghost btn-sm" data-detach="${esc(p.name)}">Remove</button></td></tr>`).join("");
    const table = h(`<table class="tp-itable"><thead><tr><th>Name</th><th>Type</th><th>Example</th><th></th></tr><tbody>${rows || '<tr><td colspan="4" class="tp-muted" style="padding:14px">No properties attached.</td></tr>'}</tbody></table>`);
    sec.appendChild(table);
    table.querySelectorAll("[data-detach]").forEach((b) => b.onclick = async () => { const pr = propByName(b.dataset.detach); if (pr) { await action("detach_property", { event_id: e.id, property_id: pr.id }); await refresh(); } });
    table.querySelectorAll("[data-ex]").forEach((inp) => inp.onchange = async () => { const pr = propByName(inp.dataset.ex); if (pr) await action("attach_property", { event_id: e.id, property_id: pr.id, required: e.properties.find((x) => x.name === inp.dataset.ex).required, example: inp.value || null }); });
    // add row
    const libOpts = S.plan.properties.event.map((p) => `<option value="${p.id}">${esc(p.name)} · ${esc(p.data_type)}</option>`).join("");
    const add = h(`<div class="tp-inline-add">
      <select id="pa-sel">${libOpts}</select>
      <label><input type="checkbox" id="pa-req"/> required</label>
      <button class="btn btn-secondary btn-sm" id="pa-add">Attach</button>
      <span class="tp-muted" style="margin:0 4px">or</span>
      <input id="pa-new" placeholder="new property name" style="width:150px"/>
      <select id="pa-newtype"><option>string</option><option>int</option><option>float</option><option>boolean</option><option>object</option><option>array</option></select>
      <button class="btn btn-ghost btn-sm" id="pa-create">Create &amp; attach</button>
      ${(S.plan.bundles || []).length ? `<span class="tp-muted" style="margin:0 4px">or bundle</span><select id="pa-bundle">${S.plan.bundles.map((b) => `<option value="${b.id}">${esc(b.name)} (${b.properties.length})</option>`).join("")}</select><button class="btn btn-ghost btn-sm" id="pa-bundleadd">Add bundle</button>` : ""}
    </div>`);
    sec.appendChild(add);
    add.querySelector("#pa-add").onclick = async () => { const pid = add.querySelector("#pa-sel").value; if (pid) { await action("attach_property", { event_id: e.id, property_id: pid, required: add.querySelector("#pa-req").checked }); await refresh(); } };
    add.querySelector("#pa-create").onclick = async () => {
      const name = add.querySelector("#pa-new").value.trim(); if (!name) return;
      const cp = await action("create_property", { name, data_type: add.querySelector("#pa-newtype").value, kind: "event" });
      if (cp) { await action("attach_property", { event_id: e.id, property_id: cp.id, required: add.querySelector("#pa-req").checked }); await refresh(); }
    };
    const bb = add.querySelector("#pa-bundleadd"); if (bb) bb.onclick = async () => { await action("attach_bundle_to_event", { event_id: e.id, bundle_id: add.querySelector("#pa-bundle").value }); await refresh(); };
    return sec;
  }

  function sourcesSection(e) {
    const sec = h(`<div class="tp-section"><h3>Sources &amp; status</h3></div>`);
    if (!S.plan.sources.length) { sec.appendChild(h(`<div class="tp-muted" style="font-size:13px">No sources defined yet — add them in the Sources tab.</div>`)); return sec; }
    const wrap = h(`<div></div>`);
    const cur = {}; e.sources.forEach((s) => cur[s.name] = s.implementation_status);
    S.plan.sources.forEach((s) => {
      const on = s.name in cur;
      const t = h(`<span class="tp-src-toggle ${on ? "is-on" : ""}"><input type="checkbox" data-s="${s.id}" ${on ? "checked" : ""}/><span class="tp-src-name">${esc(s.name)}</span><select data-st="${s.id}">${["planned", "implemented", "verified", "deprecated"].map((x) => `<option ${cur[s.name] === x ? "selected" : ""}>${x}</option>`).join("")}</select></span>`);
      wrap.appendChild(t);
    });
    sec.appendChild(wrap);
    const save = h(`<button class="btn btn-secondary btn-sm" style="margin-top:10px">Update sources</button>`);
    save.onclick = async () => {
      const sources = [...wrap.querySelectorAll("[data-s]:checked")].map((c) => ({ source_id: c.dataset.s, implementation_status: wrap.querySelector(`[data-st="${c.dataset.s}"]`).value }));
      await action("set_event_sources", { event_id: e.id, sources }); await refresh();
    };
    sec.appendChild(save);
    return sec;
  }

  function destSection(e) {
    const sec = h(`<div class="tp-section"><h3>Destination mappings</h3></div>`);
    const rows = e.destinations.map((dd) => `<tr><td class="tp-pname">${esc(dd.destination)}</td><td class="tp-mono">${esc(dd.dest_event_name || e.name)}</td><td class="tp-cell-act"><button class="btn btn-ghost btn-sm" data-unmap="${esc(dd.destination)}">Remove</button></td></tr>`).join("");
    const table = h(`<table class="tp-itable"><thead><tr><th>Destination</th><th>Maps to</th><th></th></tr><tbody>${rows || '<tr><td colspan="3" class="tp-muted" style="padding:14px">Not mapped to any destination.</td></tr>'}</tbody></table>`);
    sec.appendChild(table);
    table.querySelectorAll("[data-unmap]").forEach((b) => b.onclick = async () => { const d = destByName(b.dataset.unmap); if (d) { await action("remove_event_destination", { event_id: e.id, destination_id: d.id }); await refresh(); } });
    if (S.plan.destinations.length) {
      const add = h(`<div class="tp-inline-add"><select id="dm-sel">${S.plan.destinations.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select><input id="dm-name" placeholder="dest event name (optional)" class="tp-mono-input"/><button class="btn btn-secondary btn-sm" id="dm-add">Map</button></div>`);
      sec.appendChild(add);
      add.querySelector("#dm-add").onclick = async () => { await action("set_event_destination", { event_id: e.id, destination_id: add.querySelector("#dm-sel").value, dest_event_name: add.querySelector("#dm-name").value || null }); await refresh(); };
    }
    return sec;
  }

  // ======================================================================
  // COMMENTS
  // ======================================================================
  function commentsSection(type, id) {
    const sec = h(`<div class="tp-section"><h3>Discussion</h3><div class="tp-comments" id="tp-comments"></div>
      <div class="tp-comment-box"><textarea id="tp-cbody" placeholder="Add a comment…  @mention with a user id"></textarea><button class="btn btn-secondary btn-sm" id="tp-cadd">Comment</button></div></div>`);
    sec.querySelector("#tp-cadd").onclick = async () => {
      const body = sec.querySelector("#tp-cbody").value.trim(); if (!body) return;
      await action("add_comment", { entity_type: type, entity_id: id, body });
      sec.querySelector("#tp-cbody").value = ""; loadComments(type, id);
    };
    return sec;
  }
  async function loadComments(type, id) {
    const box = document.getElementById("tp-comments"); if (!box) return;
    const r = await getJSON(`/comments?entity_type=${type}&entity_id=${id}${S.branch !== "main" ? `&branch=${encodeURIComponent(S.branch)}` : ""}`).catch(() => ({ comments: [] }));
    const cs = r.comments || [];
    box.innerHTML = cs.length ? "" : `<div class="tp-muted" style="font-size:13px;padding:6px 0">No comments yet.</div>`;
    const roots = cs.filter((c) => !c.parent_id);
    roots.forEach((c) => { box.appendChild(commentEl(c, type, id)); cs.filter((r2) => r2.parent_id === c.id).forEach((r2) => box.appendChild(commentEl(r2, type, id, true))); });
  }
  function commentEl(c, type, id, reply) {
    const el = h(`<div class="tp-comment ${reply ? "tp-reply" : ""} ${c.resolved ? "is-resolved" : ""}">
      <div class="tp-avatar">${initials(c.author_id)}</div>
      <div class="tp-comment-main">
        <div class="tp-comment-meta"><span class="tp-mono">${esc((c.author_id || "").slice(0, 8))}</span><span>${esc((c.created_at || "").slice(0, 16).replace("T", " "))}</span>${c.resolved ? '<span class="tp-status" data-s="verified">resolved</span>' : ""}</div>
        <div class="tp-comment-body">${esc(c.body)}</div>
        <div class="tp-comment-actions"></div>
      </div></div>`);
    const acts = el.querySelector(".tp-comment-actions");
    const mk = (label, fn) => { const b = h(`<button>${label}</button>`); b.onclick = fn; acts.appendChild(b); };
    if (!reply) mk("Reply", () => { const t = prompt("Reply:"); if (t) action("add_comment", { entity_type: type, entity_id: id, body: t, parent_id: c.id }).then(() => loadComments(type, id)); });
    mk(c.resolved ? "Reopen" : "Resolve", () => action("resolve_comment", { comment_id: c.id, resolved: !c.resolved }).then(() => loadComments(type, id)));
    if (c.author_id === ME || isAdmin) mk("Delete", () => action("delete_comment", { comment_id: c.id }).then(() => loadComments(type, id)));
    return el;
  }

  // ======================================================================
  // PROPERTIES (library, master-detail with constraints)
  // ======================================================================
  function propsView(p) {
    const master = h(`<div class="tp-master"><div class="tp-master-head"><div class="tp-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg><input id="tp-pr-search" placeholder="Search properties"/></div><button class="btn btn-primary btn-sm btn-block" id="tp-pr-new">+ New property</button></div><div class="tp-master-list" id="tp-pr-list"></div></div>`);
    p.appendChild(master); p.appendChild(h(`<div class="tp-detail" id="tp-pr-detail"></div>`));
    renderPropList(); renderPropDetail();
    master.querySelector("#tp-pr-search").oninput = (e) => renderPropList(e.target.value);
    master.querySelector("#tp-pr-new").onclick = async () => { const name = prompt("Property name:"); if (!name) return; const r = await action("create_property", { name, data_type: "string", kind: "event" }); if (r) { S.selProp = r.id; await refresh(); } };
  }
  function renderPropList(q = "") {
    const list = document.getElementById("tp-pr-list"); if (!list) return;
    const byKind = S.plan.properties; const kinds = [["event", "Event"], ["user", "User"], ["group", "Group"], ["system", "System"]];
    list.innerHTML = "";
    let any = false;
    kinds.forEach(([k, label]) => {
      let items = byKind[k]; if (q) items = items.filter((p) => p.name.toLowerCase().includes(q.toLowerCase()));
      if (!items.length) return; any = true;
      list.appendChild(h(`<div class="tp-cat-label">${label} properties</div>`));
      items.forEach((p) => {
        const row = h(`<div class="tp-row ${S.selProp === p.id ? "is-active" : ""}"><div class="tp-row-main"><div class="tp-name">${esc(p.name)}</div><div class="tp-row-sub">${esc(p.description || "—")}</div></div><div class="tp-row-meta">${esc(p.data_type)}${p.is_list ? "[]" : ""}</div></div>`);
        row.onclick = () => { S.selProp = p.id; renderPropList(q); renderPropDetail(); }; list.appendChild(row);
      });
    });
    if (!any) list.appendChild(h(`<div class="tp-row-empty">No properties</div>`));
  }
  function renderPropDetail() {
    const d = document.getElementById("tp-pr-detail"); if (!d) return;
    const p = allProps().find((x) => x.id === S.selProp);
    if (!p) { d.innerHTML = `<div class="tp-empty"><div>Select a property to edit its type &amp; constraints.</div></div>`; return; }
    const c = p.constraints || {};
    const inner = h(`<div class="tp-detail-inner">
      <div class="tp-d-head"><div class="tp-d-title"><input class="tp-titlefield" id="pd-name" value="${esc(p.name)}"/></div>
        <div class="tp-d-actions"><button class="btn btn-ghost btn-sm" id="pd-del">Delete</button><button class="btn btn-primary btn-sm" id="pd-save">Save</button></div></div>
      <div class="tp-section"><div class="tp-fieldgrid">
        <div class="tp-field"><label>Kind</label><select id="pd-kind">${["event", "user", "group", "system"].map((k) => `<option ${p.kind === k ? "selected" : ""}>${k}</option>`).join("")}</select></div>
        <div class="tp-field"><label>Data type</label><select id="pd-type">${["string", "int", "float", "boolean", "object", "array"].map((t) => `<option ${p.data_type === t ? "selected" : ""}>${t}</option>`).join("")}</select></div>
        <div class="tp-field"><label>List / array</label><label style="font-size:13px;font-weight:400;display:flex;gap:7px;align-items:center;margin-top:6px"><input type="checkbox" id="pd-list" ${p.is_list ? "checked" : ""}/> values are a list</label></div>
        <div class="tp-field"><label>PII</label><label style="font-size:13px;font-weight:400;display:flex;gap:7px;align-items:center;margin-top:6px"><input type="checkbox" id="pd-pii" ${p.is_pii ? "checked" : ""}/> contains personal data</label></div>
        <div class="tp-field tp-col-2"><label>Description</label><textarea id="pd-desc">${esc(p.description || "")}</textarea></div>
      </div></div>
      <div class="tp-section"><h3>Constraints</h3><div class="tp-constraints">
        <div class="tp-field tp-col-2" style="grid-column:1/-1"><label>Allowed values (enum) — comma separated, leave blank for none</label><input id="pd-enum" class="tp-mono-input" value="${esc((c.allowed_values || []).join(", "))}"/></div>
        <div class="tp-field"><label>Min</label><input id="pd-min" type="number" value="${c.min ?? ""}"/></div>
        <div class="tp-field"><label>Max</label><input id="pd-max" type="number" value="${c.max ?? ""}"/></div>
        <div class="tp-field tp-col-2" style="grid-column:1/-1"><label>Regex / format</label><input id="pd-regex" class="tp-mono-input" value="${esc(c.regex || "")}"/></div>
      </div></div></div>`);
    d.innerHTML = ""; d.appendChild(inner);
    inner.querySelector("#pd-save").onclick = async () => {
      const enumv = inner.querySelector("#pd-enum").value.split(",").map((s) => s.trim()).filter(Boolean);
      const cons = {};
      if (enumv.length) cons.allowed_values = enumv;
      const mn = inner.querySelector("#pd-min").value, mx = inner.querySelector("#pd-max").value, rx = inner.querySelector("#pd-regex").value.trim();
      if (mn !== "") cons.min = Number(mn); if (mx !== "") cons.max = Number(mx); if (rx) cons.regex = rx;
      const r = await action("update_property", { property_id: p.id, name: inner.querySelector("#pd-name").value.trim(), data_type: inner.querySelector("#pd-type").value, is_list: inner.querySelector("#pd-list").checked, is_pii: inner.querySelector("#pd-pii").checked, description: inner.querySelector("#pd-desc").value || null, constraints: Object.keys(cons).length ? cons : null });
      if (r) { banner("Property saved", "ok"); await refresh(); }
    };
    inner.querySelector("#pd-del").onclick = async () => { if (confirm(`Delete property "${p.name}"?`)) { await action("delete_property", { property_id: p.id }); S.selProp = null; await refresh(); } };
  }

  // ======================================================================
  // BUNDLES / SOURCES / DESTINATIONS / METRICS  (list views)
  // ======================================================================
  function simpleList(p, title, createFn, rowsHtml, wire) {
    const inner = h(`<div class="tp-detail" style="flex:1"><div class="tp-detail-inner" style="max-width:980px">
      <div class="tp-d-head"><div class="tp-d-title"><h2 style="margin:0;font-size:20px">${title}</h2></div><div class="tp-d-actions">${createFn ? `<button class="btn btn-primary btn-sm" id="sl-new">+ New</button>` : ""}</div></div>
      <div class="tp-section">${rowsHtml}</div></div></div>`);
    p.appendChild(inner);
    if (createFn) inner.querySelector("#sl-new").onclick = createFn;
    if (wire) wire(inner);
  }
  function bundlesView(p) {
    const bs = S.plan.bundles || [];
    const rows = bs.map((b) => `<div class="tp-ver-row"><div class="tp-ver-num">${b.properties.length}</div><div class="tp-ver-main"><div class="tp-mono" style="font-weight:600">${esc(b.name)}</div><div class="tp-muted" style="font-size:12px">${esc(b.description || "")} · ${b.properties.map((x) => esc(x.name)).join(", ") || "no properties"}</div></div><button class="btn btn-ghost btn-sm" data-mng="${b.id}">Manage</button><button class="btn btn-ghost btn-sm" data-delb="${b.id}">Delete</button></div>`).join("") || `<div class="tp-row-empty">No bundles. Bundles let you attach a group of properties to events at once.</div>`;
    simpleList(p, "Property bundles", async () => { const n = prompt("Bundle name:"); if (n) { await action("create_bundle", { name: n }); await refresh(); } }, rows, (root2) => {
      root2.querySelectorAll("[data-delb]").forEach((b) => b.onclick = async () => { if (confirm("Delete bundle?")) { await action("delete_bundle", { bundle_id: b.dataset.delb }); await refresh(); } });
      root2.querySelectorAll("[data-mng]").forEach((b) => b.onclick = () => manageBundle(b.dataset.mng));
    });
  }
  async function manageBundle(id) {
    const b = (S.plan.bundles || []).find((x) => x.id === id); if (!b) return;
    const cur = b.properties.map((x) => esc(x.name)).join(", ");
    const pick = prompt(`Bundle "${b.name}" — enter a property name to ADD (current: ${cur || "none"}):`);
    if (!pick) return; const pr = S.plan.properties.event.find((x) => x.name === pick.trim());
    if (!pr) { banner("No event property named " + pick, "err"); return; }
    await action("add_property_to_bundle", { bundle_id: id, property_id: pr.id }); await refresh();
  }
  function sourcesView(p) {
    const rows = S.plan.sources.map((s) => `<div class="tp-ver-row"><div class="tp-ver-main"><div class="tp-mono" style="font-weight:600">${esc(s.name)}</div><div class="tp-muted" style="font-size:12px">${esc(s.platform_type || "—")} → ${s.destinations.length ? s.destinations.map(esc).join(", ") : "<span class='tp-muted'>no routes</span>"}</div></div><select data-route="${s.id}"><option value="">route to…</option>${S.plan.destinations.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select><button class="btn btn-ghost btn-sm" data-dels="${s.id}">Delete</button></div>`).join("") || `<div class="tp-row-empty">No sources yet.</div>`;
    simpleList(p, "Sources", async () => { const n = prompt("Source name:"); if (n) { await action("create_source", { name: n, platform_type: prompt("Platform type (web/ios/android/server)?") || null }); await refresh(); } }, rows, (r) => {
      r.querySelectorAll("[data-dels]").forEach((b) => b.onclick = async () => { if (confirm("Delete source?")) { await action("delete_source", { source_id: b.dataset.dels }); await refresh(); } });
      r.querySelectorAll("[data-route]").forEach((sel) => sel.onchange = async () => { if (sel.value) { await action("connect_source_destination", { source_id: sel.dataset.route, destination_id: sel.value }); await refresh(); } });
    });
  }
  function destsView(p) {
    const rows = S.plan.destinations.map((d) => `<div class="tp-ver-row"><div class="tp-ver-main"><div class="tp-mono" style="font-weight:600">${esc(d.name)}</div><div class="tp-muted" style="font-size:12px">${esc(d.platform)}${d.platform_account_id ? " · " + esc(d.platform_account_id) : ""}</div></div><button class="btn btn-ghost btn-sm" data-deld="${d.id}">Delete</button></div>`).join("") || `<div class="tp-row-empty">No destinations yet.</div>`;
    simpleList(p, "Destinations", async () => { const n = prompt("Destination name:"); if (!n) return; const pl = prompt("Platform (ga4/amplitude/mixpanel/meta/…)?"); if (!pl) return; await action("create_destination", { name: n, platform: pl, platform_account_id: prompt("Account id (optional)?") || null }); await refresh(); }, rows, (r) => {
      r.querySelectorAll("[data-deld]").forEach((b) => b.onclick = async () => { if (confirm("Delete destination?")) { await action("delete_destination", { destination_id: b.dataset.deld }); await refresh(); } });
    });
  }
  function metricsView(p) {
    const rows = S.plan.metrics.map((m) => `<div class="tp-ver-row"><div class="tp-ver-num" style="font-size:11px;text-transform:uppercase">${esc(m.type)}</div><div class="tp-ver-main"><div class="tp-mono" style="font-weight:600">${esc(m.name)}</div><div class="tp-muted" style="font-size:12px">${esc(m.event || "—")} ${esc(m.description || "")}</div></div><button class="btn btn-ghost btn-sm" data-delm="${m.id}">Delete</button></div>`).join("") || `<div class="tp-row-empty">No metrics yet.</div>`;
    simpleList(p, "Metrics", async () => { const n = prompt("Metric name:"); if (!n) return; const ty = prompt("Type (count/sum/unique/average/ratio)?", "count") || "count"; const evn = prompt("Event name (optional)?"); const ev = evn ? S.plan.events.find((e) => e.name === evn.trim()) : null; await action("create_metric", { name: n, type: ty, event_id: ev ? ev.id : null }); await refresh(); }, rows, (r) => {
      r.querySelectorAll("[data-delm]").forEach((b) => b.onclick = async () => { if (confirm("Delete metric?")) { await action("delete_metric", { metric_id: b.dataset.delm }); await refresh(); } });
    });
  }

  // ======================================================================
  // CHANGES (branch diff)  &  VERSIONS
  // ======================================================================
  async function changesView(p) {
    p.appendChild(h(`<div class="tp-diff" id="tp-diff-box"><div class="tp-muted">Loading changes…</div></div>`));
    const box = p.querySelector("#tp-diff-box");
    const diff = await getJSON(`/diff?head=${encodeURIComponent(S.branch)}`).catch((e) => { box.innerHTML = `<div class="tp-empty">${esc(String(e))}</div>`; return null; });
    if (!diff) return;
    const s = diff.summary || { added: 0, removed: 0, changed: 0 };
    box.innerHTML = `<div style="display:flex;align-items:center;gap:14px;margin-bottom:18px"><h2 style="margin:0;font-size:20px">Changes vs <span class="tp-mono">main</span></h2></div>
      <div class="tp-diff-summary"><span class="tp-diff-stat add">+${s.added} added</span><span class="tp-diff-stat chg">~${s.changed} changed</span><span class="tp-diff-stat rem">−${s.removed} removed</span></div>`;
    const groups = [["events", "Events"], ["properties", "Properties"], ["sources", "Sources"], ["destinations", "Destinations"], ["metrics", "Metrics"], ["categories", "Categories"]];
    let anyChange = false;
    groups.forEach(([k, label]) => {
      const g = diff[k]; if (!g) return;
      // properties is nested by kind
      const collect = (obj) => obj;
      let add = [], rem = [], chg = [];
      if (k === "properties") { ["event", "user", "group", "system"].forEach((kind) => { const gk = g[kind] || {}; add = add.concat((gk.added || []).map((x) => x.name)); rem = rem.concat((gk.removed || []).map((x) => x.name)); chg = chg.concat((gk.changed || []).map((x) => x.name)); }); }
      else { add = (g.added || []).map((x) => x.name); rem = (g.removed || []).map((x) => x.name); chg = (g.changed || []).map((x) => x.name); }
      if (!add.length && !rem.length && !chg.length) return; anyChange = true;
      const grp = h(`<div class="tp-diff-group"><h3>${label}</h3></div>`);
      add.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark add">+</span>${esc(n)}</div>`)));
      chg.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark chg">~</span>${esc(n)}</div>`)));
      rem.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark rem">−</span>${esc(n)}</div>`)));
      box.appendChild(grp);
    });
    if (!anyChange) box.appendChild(h(`<div class="tp-empty">No differences from main yet.</div>`));
  }
  async function versionsView(p) {
    p.appendChild(h(`<div class="tp-versions" id="tp-vbox"><div class="tp-muted">Loading…</div></div>`));
    const box = p.querySelector("#tp-vbox");
    const r = await getJSON("/versions").catch(() => ({ versions: [] }));
    const vs = r.versions || [];
    box.innerHTML = `<div class="tp-d-head" style="margin-bottom:16px"><div class="tp-d-title"><h2 style="margin:0;font-size:20px">Published versions</h2><div class="tp-muted" style="font-size:13px;margin-top:3px">Each publish freezes an immutable snapshot of the plan. Downstream audits read the latest.</div></div></div>`;
    if (!vs.length) { box.appendChild(h(`<div class="tp-row-empty">Nothing published yet. Use <b>Publish</b> on the main branch.</div>`)); return; }
    vs.forEach((v, i) => {
      const row = h(`<div class="tp-ver-row"><div class="tp-ver-num">${esc(v.version_number)}</div><div class="tp-ver-main"><div>${esc(v.changelog || "—")}</div><div class="tp-ver-when">${esc((v.published_at || "").replace("T", " ").slice(0, 19))}</div></div>${i < vs.length - 1 ? `<button class="btn btn-ghost btn-sm" data-vdiff="${v.id}" data-vprev="${vs[i + 1].id}">Compare to ${esc(vs[i + 1].version_number)}</button>` : ""}</div>`);
      box.appendChild(row);
    });
    box.querySelectorAll("[data-vdiff]").forEach((b) => b.onclick = () => compareVersions(b.dataset.vprev, b.dataset.vdiff));
  }
  async function compareVersions(baseId, headId) {
    const [a, b] = await Promise.all([getJSON(`/versions/${baseId}`), getJSON(`/versions/${headId}`)]);
    const diff = snapshotDiff(a.snapshot, b.snapshot);
    const box = document.getElementById("tp-vbox");
    const panel = h(`<div style="margin-top:18px;padding:18px;border:1px solid var(--border-strong);border-radius:var(--r-lg);background:var(--surface-2)"><h3 style="margin:0 0 12px;font-size:13px">Diff ${esc(a.version_number)} → ${esc(b.version_number)}</h3></div>`);
    let any = false;
    [["events", "Events"], ["eventProps", "Event properties"]].forEach(([k, label]) => {
      const g = diff[k]; if (!g.added.length && !g.removed.length && !g.changed.length) return; any = true;
      const grp = h(`<div class="tp-diff-group"><h3>${label}</h3></div>`);
      g.added.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark add">+</span>${esc(n)}</div>`)));
      g.changed.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark chg">~</span>${esc(n)}</div>`)));
      g.removed.forEach((n) => grp.appendChild(h(`<div class="tp-diff-item"><span class="tp-diff-mark rem">−</span>${esc(n)}</div>`)));
      panel.appendChild(grp);
    });
    if (!any) panel.appendChild(h(`<div class="tp-muted">No event-level differences.</div>`));
    box.appendChild(panel); panel.scrollIntoView({ behavior: "smooth" });
  }
  function snapshotDiff(a, b) {
    const evA = {}, evB = {}; (a.events || []).forEach((e) => evA[e.name] = e); (b.events || []).forEach((e) => evB[e.name] = e);
    const namesA = new Set(Object.keys(evA)), namesB = new Set(Object.keys(evB));
    const events = { added: [...namesB].filter((n) => !namesA.has(n)), removed: [...namesA].filter((n) => !namesB.has(n)), changed: [...namesB].filter((n) => namesA.has(n) && JSON.stringify(stripIds(evA[n])) !== JSON.stringify(stripIds(evB[n]))) };
    const eventProps = { added: [], removed: [], changed: [] };
    return { events, eventProps };
  }
  const stripIds = (o) => JSON.parse(JSON.stringify(o, (k, v) => (k === "id" ? undefined : v)));

  // ======================================================================
  // BRANCH / PUBLISH ACTIONS
  // ======================================================================
  async function createBranch() {
    const name = prompt("New branch name (e.g. add-checkout-events):"); if (!name) return;
    const desc = prompt("What's this branch for? (optional)") || null;
    const r = await action("create_branch", { name, description: desc });
    if (r) { S.branch = r.name; S.selEvent = S.selProp = null; S.tab = "events"; banner(`Branch "${r.name}" created — edit freely, then merge.`, "ok"); await loadPlan(); }
  }
  async function mergeBranch() {
    const b = curBranchObj(); if (!confirm(`Merge "${b.name}" into main and publish a new version?`)) return;
    const note = prompt("Changelog for this merge:", `Merged ${b.name}`);
    const r = await action("merge_branch", { branch_id: b.id, changelog: note });
    if (r) { banner(`Merged → published ${r.version_number}.`, "ok"); S.branch = "main"; S.selEvent = null; S.tab = "events"; await loadPlan(); }
  }
  async function setReview(status) { const b = curBranchObj(); const r = await action("set_review_status", { branch_id: b.id, review_status: status }); if (r) { banner(`Branch marked ${status.replace(/_/g, " ")}.`, "ok"); await loadPlan(); } }
  async function publish() { const note = prompt("Changelog for this version:"); if (note === null) return; const r = await action("publish", { changelog: note }); if (r) { banner(`Published version ${r.version_number}.`, "ok"); await loadPlan(); } }
  async function validate() {
    const r = await getJSON("/validate" + branchQS());
    const warns = r.findings.filter((f) => f.severity === "warning").length;
    banner(`${r.findings.length} findings · ${warns} warnings · ${r.is_publishable ? "publishable" : "resolve warnings first"}`, warns ? "warn" : "ok");
  }
  async function newEvent() { const name = prompt("Event name (e.g. checkout_completed):"); if (!name) return; const r = await action("create_event", { name }); if (r) { S.selEvent = r.id; await refresh(); } }

  // ---- boot ----
  loadPlan().catch((e) => { root.innerHTML = `<div class="tp-empty">Failed to load: ${esc(String(e))}</div>`; });
})();
