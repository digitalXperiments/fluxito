// app/static/js/tracking_plan/api.js
// Fetch wrappers over the tracking-plan HTTP API. CSRF is attached by the global
// fetch wrapper in base.html, so these are plain fetch calls.

let PID = '';
let BASE = '';

export function initApi(dataset) {
  PID = dataset.pid;
  BASE = `/api/projects/${PID}/tracking-plan`;
}

export function getPid() { return PID; }

async function getJSON(path) {
  const r = await fetch(BASE + path);
  if (!r.ok) {
    let detail = r.status;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(String(detail));
  }
  return r.json();
}

const branchQS = (b) => (b && b !== 'main' ? `?branch=${encodeURIComponent(b)}` : '');

export const getPlan = (branch) => getJSON(branchQS(branch));
export const validate = (branch) => getJSON('/validate' + branchQS(branch));
export const branches = () => getJSON('/branches');
export const versions = () => getJSON('/versions');
export const version = (id) => getJSON(`/versions/${id}`);

export const diff = (head, base) =>
  getJSON(`/diff?head=${encodeURIComponent(head)}` + (base ? `&base=${encodeURIComponent(base)}` : ''));

export const vendors = () => getJSON('/vendors');

export function listComments(entityType, entityId, branch) {
  const q = new URLSearchParams();
  if (entityType) q.set('entity_type', entityType);
  if (entityId) q.set('entity_id', entityId);
  if (branch && branch !== 'main') q.set('branch', branch);
  return getJSON('/comments?' + q.toString());
}

export function listActivity(entityType, entityId, branch) {
  const q = new URLSearchParams();
  if (entityType) q.set('entity_type', entityType);
  if (entityId) q.set('entity_id', entityId);
  if (branch && branch !== 'main') q.set('branch', branch);
  return getJSON('/activity?' + q.toString());
}

// Members endpoint lives at /api/projects/{pid}/members (Plan 1), NOT under /tracking-plan.
export async function members() {
  const r = await fetch(`/api/projects/${PID}/members`);
  if (!r.ok) return { members: [] };
  return r.json();
}

// All writes go here. Injects the active branch into params (api_action pops it).
// Throws Error(.errorType, .message) on the {error:true,...} shape; returns JSON on success.
export async function doAction(action, params = {}, branch) {
  const body = { action, params: { ...params } };
  if (branch) body.params.branch = branch;
  const r = await fetch(BASE + '/action', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const j = await r.json();
  if (j && j.error) {
    const err = new Error(j.message || j.error_type || 'Action failed');
    err.errorType = j.error_type;
    err.message = j.message || j.error_type || 'Action failed';
    throw err;
  }
  return j;
}
