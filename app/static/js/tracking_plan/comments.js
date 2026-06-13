// app/static/js/tracking_plan/comments.js
// Reusable right-side drawer: Comments (threaded) + Activity (change history).
// mountDrawer(container, {entityType, entityId, branch}) returns { open, close,
// destroy }. Used by every entity view.

import { h, mountAll } from 'tp/render';
import * as api from 'tp/api';
import { isAdmin, myId } from 'tp/state';
import { renderMentions, parseMentions } from 'tp/util/mentions';
import { initials, relativeTime, titleCase } from 'tp/util/format';

export function mountDrawer(container, { entityType, entityId, branch }) {
  let tab = 'comments';
  let members = [];
  let comments = [];
  let activity = [];

  const drawer = h('div', { class: 'tp-drawer' });
  container.appendChild(drawer);

  async function refresh() {
    if (tab === 'comments') {
      const [cm, mem] = await Promise.all([
        api.listComments(entityType, entityId, branch).catch(() => ({ comments: [] })),
        members.length ? Promise.resolve({ members }) : api.members().catch(() => ({ members: [] })),
      ]);
      comments = cm.comments || [];
      members = mem.members || members;
    } else {
      const a = await api.listActivity(entityType, entityId, branch).catch(() => ({ activity: [] }));
      activity = a.activity || [];
    }
    render();
  }

  function render() {
    const tabs = h('div', { class: 'tp-drawer-tabs' },
      tabBtn('comments', `Comments${comments.length ? ' · ' + comments.length : ''}`),
      tabBtn('activity', 'Activity'),
      h('button', { class: 'tp-drawer-close', title: 'Close', onClick: close }, '✕'),
    );
    const bodyNode = tab === 'comments' ? commentsBody() : activityBody();
    mountAll(drawer, [tabs, bodyNode]);
  }

  function tabBtn(name, label) {
    return h('button', {
      class: 'tp-drawer-tab' + (tab === name ? ' is-active' : ''),
      onClick: () => { tab = name; refresh(); },
    }, label);
  }

  function commentEl(c, reply) {
    const meta = h('div', { class: 'tp-comment-meta' },
      h('span', { class: 'tp-mono' }, (c.author_id || '').slice(0, 8)),
      h('span', {}, relativeTime(c.created_at)),
      c.resolved ? h('span', { class: 'tp-status', dataset: { s: 'verified' } }, 'resolved') : null,
    );
    const bodyEl = h('div', { class: 'tp-comment-body', html: renderMentions(c.body, members) });
    const acts = h('div', { class: 'tp-comment-actions' });
    if (!reply) acts.appendChild(actBtn('Reply', () => startReply(c.id)));
    acts.appendChild(actBtn(c.resolved ? 'Reopen' : 'Resolve', () =>
      doAndRefresh('resolve_comment', { comment_id: c.id, resolved: !c.resolved })));
    if (c.author_id === myId() || isAdmin()) {
      acts.appendChild(actBtn('Edit', () => startEdit(c)));
      acts.appendChild(actBtn('Delete', () => doAndRefresh('delete_comment', { comment_id: c.id })));
    }
    return h('div', {
      class: 'tp-comment' + (reply ? ' tp-reply' : '') + (c.resolved ? ' is-resolved' : ''),
    },
      h('div', { class: 'tp-avatar' }, initials(c.author_id)),
      h('div', { class: 'tp-comment-main' }, meta, bodyEl, acts),
    );
  }
  const actBtn = (label, fn) => h('button', { onClick: fn }, label);

  function commentsBody() {
    const list = h('div', { class: 'tp-comments' });
    const roots = comments.filter((c) => !c.parent_id);
    if (!roots.length) {
      list.appendChild(h('div', { class: 'tp-muted', style: { padding: '6px 0' } }, 'No comments yet.'));
    }
    for (const c of roots) {
      list.appendChild(commentEl(c, false));
      comments.filter((r) => r.parent_id === c.id).forEach((r) => list.appendChild(commentEl(r, true)));
    }
    return h('div', { class: 'tp-drawer-body' }, list, composer(null));
  }

  // --- composer with @mention autocomplete ---
  function composer(parentId, initialText) {
    const ta = h('textarea', {
      class: 'tp-cbody',
      placeholder: parentId ? 'Reply… type @ to mention' : 'Add a comment… type @ to mention',
    });
    if (initialText) ta.value = initialText;
    const pop = h('div', { class: 'tp-mention-pop', style: { display: 'none' } });
    const wrap = h('div', { class: 'tp-comment-box' }, ta, pop,
      h('button', { class: 'btn btn-secondary btn-sm', onClick: submit }, parentId ? 'Reply' : 'Comment'),
    );

    ta.addEventListener('input', () => maybeMention(ta, pop));
    ta.addEventListener('keydown', (e) => {
      if (pop.style.display !== 'none' && (e.key === 'Enter' || e.key === 'Tab')) {
        const first = pop.querySelector('.tp-mention-opt');
        if (first) { e.preventDefault(); first.click(); }
      }
    });

    async function submit() {
      const body = ta.value.trim();
      if (!body) return;
      const { mentions } = parseMentions(body, members);
      const params = { entity_type: entityType, entity_id: entityId, body };
      if (parentId) params.parent_id = parentId;
      if (mentions.length) params.mentions = mentions;
      await doAndRefresh('add_comment', params);
    }
    return wrap;
  }

  function maybeMention(ta, pop) {
    const upto = ta.value.slice(0, ta.selectionStart);
    const m = /@([\w ]*)$/.exec(upto);
    if (!m) { pop.style.display = 'none'; return; }
    const q = m[1].toLowerCase();
    const hits = members.filter((mm) => (mm.display_name || '').toLowerCase().includes(q)).slice(0, 6);
    if (!hits.length) { pop.style.display = 'none'; return; }
    mountAll(pop, hits.map((mm) => h('div', {
      class: 'tp-mention-opt',
      onClick: () => {
        const before = ta.value.slice(0, ta.selectionStart).replace(/@[\w ]*$/, '@' + mm.display_name + ' ');
        const after = ta.value.slice(ta.selectionStart);
        ta.value = before + after;
        pop.style.display = 'none';
        ta.focus();
      },
    }, h('span', { class: 'tp-avatar tp-avatar-sm' }, initials(mm.id)), mm.display_name)));
    pop.style.display = 'block';
  }

  function startReply(parentId) {
    const body = commentsBody();
    // append a reply composer under the thread
    const c = composer(parentId);
    // `body` IS the .tp-drawer-body div, so append the reply composer to it directly
    // (querySelector searches descendants and would return null here).
    body.appendChild(c);
    mountAll(drawer, [drawer.querySelector('.tp-drawer-tabs'), body]);
  }
  function startEdit(c) {
    const newBody = prompt('Edit comment:', c.body);
    if (newBody != null && newBody.trim()) doAndRefresh('edit_comment', { comment_id: c.id, body: newBody.trim() });
  }

  function activityBody() {
    const rows = activity.length
      ? activity.map((a) => h('div', { class: 'tp-activity-row' },
          h('div', { class: 'tp-avatar tp-avatar-sm' }, initials(a.actor_id || '?')),
          h('div', { class: 'tp-activity-main' },
            h('div', { class: 'tp-activity-summary' }, a.summary || titleCase(a.action)),
            h('div', { class: 'tp-activity-when' }, relativeTime(a.created_at)),
          )))
      : [h('div', { class: 'tp-muted', style: { padding: '6px 0' } }, 'No activity yet.')];
    return h('div', { class: 'tp-drawer-body' }, ...rows);
  }

  async function doAndRefresh(action, params) {
    try {
      await api.doAction(action, params, branch);
      await refresh();
    } catch (e) {
      window.__tpBanner && window.__tpBanner(`${e.errorType || 'error'}: ${e.message}`, 'err');
    }
  }

  function open() { drawer.classList.add('is-open'); refresh(); }
  function close() { drawer.classList.remove('is-open'); }
  function destroy() { drawer.remove(); }

  return { open, close, destroy, el: drawer };
}
