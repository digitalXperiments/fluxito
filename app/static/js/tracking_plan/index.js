// app/static/js/tracking_plan/index.js
// Entry point: read dataset, boot the store, mount shell + active view, load plan.
import { initApi } from 'tp/api';
import { initState } from 'tp/state';
import * as state from 'tp/state';
import { mountShell } from 'tp/shell';
import { mountActiveView } from 'tp/views/router';

const root = document.getElementById('tp-app');
if (root) {
  initApi(root.dataset);
  initState(root.dataset);
  const viewHost = mountShell(root);
  mountActiveView(viewHost);
  state.reload().catch((e) => {
    const banner = document.getElementById('tp-banner');
    if (banner) { banner.className = 'tp-banner err'; banner.style.display = 'flex'; banner.textContent = 'Failed to load tracking plan: ' + (e && e.message ? e.message : e); }
    console.error(e);
  });
}
