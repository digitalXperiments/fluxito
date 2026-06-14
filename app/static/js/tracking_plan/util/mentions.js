// app/static/js/tracking_plan/util/mentions.js
// Pure @mention parsing + rendering. parseMentions extracts the user ids of any
// member whose display_name appears as an @token; renderMentions returns HTML
// with known mentions highlighted and all other text HTML-escaped.

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]),
  );
}

// Longest names first so "@Ann Lee" wins over a hypothetical "@Ann".
function _sortedMembers(members) {
  return [...(members || [])]
    .filter((m) => m && m.display_name)
    .sort((a, b) => b.display_name.length - a.display_name.length);
}

export function parseMentions(text, members) {
  const t = text || '';
  const ids = new Set();
  for (const m of _sortedMembers(members)) {
    const token = '@' + m.display_name;
    if (t.includes(token)) ids.add(m.id);
  }
  return { mentions: [...ids], text: t };
}

export function renderMentions(text, members) {
  let out = esc(text || '');
  for (const m of _sortedMembers(members)) {
    const token = '@' + m.display_name;
    const escToken = esc(token);
    out = out.split(escToken).join(`<span class="tp-mention">${escToken}</span>`);
  }
  return out;
}
