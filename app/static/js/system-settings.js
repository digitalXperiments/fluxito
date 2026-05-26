(function () {
  "use strict";

  function save(event) {
    event.preventDefault();
    var form = event.target;
    var key = form.getAttribute("data-key");
    var input = form.elements.value;
    var value = input ? input.value : "";
    var saveBtn = form.querySelector('button[type="submit"]');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving...";
    }

    fetch("/api/settings/system/" + encodeURIComponent(key), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value: value }),
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || "Save failed"); });
        return r.json();
      })
      .then(function () {
        if (form.getAttribute("data-secret") === "1" && input) {
          input.value = "";
          input.placeholder = "********";
        }
        var badge = form.querySelector(".sys-badge");
        if (badge) {
          badge.textContent = "db";
          badge.classList.add("is-db");
          badge.classList.remove("is-env");
        }
        Fluxito.toast("Setting saved");
      })
      .catch(function (err) {
        Fluxito.toast(err.message || "Save failed", "error");
      })
      .finally(function () {
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = "Save";
        }
      });
  }

  function reset(key) {
    if (!window.confirm("Reset this setting to its env/default fallback?")) return;
    fetch("/api/settings/system/" + encodeURIComponent(key), {
      method: "DELETE",
      credentials: "same-origin",
    })
      .then(function (r) {
        if (!r.ok) return r.json().then(function (e) { throw new Error(e.detail || "Reset failed"); });
        return r.json();
      })
      .then(function () {
        Fluxito.toast("Setting reset");
        window.location.reload();
      })
      .catch(function (err) {
        Fluxito.toast(err.message || "Reset failed", "error");
      });
  }

  window.SystemSettings = {
    save: save,
    reset: reset,
  };
})();
