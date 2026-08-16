/* Fluxito hosted-dashboard SDK. The host overwrites this file. Do not vendor it. */
(function (root) {
  var PARENT_ORIGIN = "__FLUXITO_PARENT_ORIGIN__";
  var token = null;
  var waiting = [];
  var bannerTimer = null;

  function accept(ev) {
    if (!ev || !ev.data || ev.data.type !== "fluxito-embed") return;
    if (PARENT_ORIGIN && ev.origin !== PARENT_ORIGIN) return;
    if (typeof ev.data.token !== "string" || !ev.data.token) return;
    token = ev.data.token;
    var cbs = waiting.slice();
    waiting = [];
    for (var i = 0; i < cbs.length; i++) cbs[i]();
    hideBanner();
  }

  function hideBanner() {
    if (bannerTimer) {
      clearTimeout(bannerTimer);
      bannerTimer = null;
    }
    var el = document.getElementById("fluxito-open-from-host");
    if (el && el.parentNode) el.parentNode.removeChild(el);
  }

  function showBanner() {
    if (token || document.getElementById("fluxito-open-from-host")) return;
    var el = document.createElement("div");
    el.id = "fluxito-open-from-host";
    el.setAttribute("role", "status");
    el.style.cssText =
      "position:fixed;inset:auto 16px 16px 16px;z-index:2147483647;padding:12px 14px;" +
      "background:#161c24;color:#e8eef7;font:13px/1.4 system-ui,sans-serif;" +
      "border:1px solid #2a3340;border-radius:10px;";
    el.textContent = "Open this dashboard from Fluxito to load live data.";
    document.body.appendChild(el);
  }

  root.addEventListener("message", accept);
  try {
    if (root.parent && root.parent !== root) {
      root.parent.postMessage({ type: "fluxito-ready" }, PARENT_ORIGIN || "*");
    }
  } catch (e) {}
  bannerTimer = setTimeout(showBanner, 2000);

  function whenReady() {
    return new Promise(function (resolve, reject) {
      if (token) {
        resolve();
        return;
      }
      var done = false;
      var timer = setTimeout(function () {
        if (done) return;
        done = true;
        reject({
          error: true,
          error_type: "not_hosted",
          message: "Open this dashboard from Fluxito to load live data.",
        });
      }, 8000);
      waiting.push(function () {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve();
      });
    });
  }

  function query(alias, action, params) {
    return whenReady()
      .then(function () {
        return fetch("/query", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: "Bearer " + token,
          },
          body: JSON.stringify({
            alias: alias,
            action: action,
            params: params || {},
          }),
        });
      })
      .then(function (resp) {
        return resp.json().catch(function () {
          return {
            error: true,
            error_type: "bad_response",
            message: "Data plane returned non-JSON.",
          };
        });
      })
      .catch(function (err) {
        if (err && err.error) return err;
        return { error: true, error_type: "transport", message: String(err).slice(0, 300) };
      });
  }

  function rows(result) {
    if (!result || typeof result !== "object" || result.error) return [];
    if (Array.isArray(result.rows) && result.rows[0] && typeof result.rows[0] === "object") {
      if (!("dimension_values" in result.rows[0]) && !("metric_values" in result.rows[0])) {
        return result.rows.slice();
      }
    }
    var dimH = result.dimension_headers || result.dimensions || [];
    var metH = result.metric_headers || result.metrics || [];
    var raw = result.rows || result.data || [];
    if (Array.isArray(dimH) && Array.isArray(metH) && Array.isArray(raw)) {
      var out = [];
      for (var i = 0; i < raw.length; i++) {
        var row = raw[i];
        if (!row || typeof row !== "object") continue;
        var dims = row.dimension_values || row.dimensions || [];
        var mets = row.metric_values || row.metrics || [];
        var item = {};
        for (var d = 0; d < dimH.length; d++) item[String(dimH[d])] = dims[d];
        for (var m = 0; m < metH.length; m++) item[String(metH[m])] = mets[m];
        if (Object.keys(item).length) out.push(item);
      }
      if (out.length) return out;
    }
    if (Array.isArray(result.data)) {
      return result.data.filter(function (r) {
        return r && typeof r === "object";
      });
    }
    return [];
  }

  root.fluxito = {
    query: query,
    rows: rows,
    whenReady: whenReady,
  };
})(window);
