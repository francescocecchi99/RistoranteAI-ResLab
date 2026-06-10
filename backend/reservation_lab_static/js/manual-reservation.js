(function () {
  var STORAGE_KEY = "rl_manual_reservation_draft";

  function qs(id) {
    return document.getElementById(id);
  }

  function readBootstrap() {
    var el = document.getElementById("manual-bootstrap");
    if (!el) return { areas: [] };
    try {
      return JSON.parse(el.textContent || "{}");
    } catch (e) {
      return { areas: [] };
    }
  }

  function pageRestaurantId() {
    var page = document.querySelector(".lab-manual-page");
    return page && page.getAttribute("data-restaurant-id");
  }

  function saveDraft() {
    var draft = {
      customer_name: (qs("manual-customer-name") && qs("manual-customer-name").value) || "",
      phone_number: (qs("manual-phone") && qs("manual-phone").value) || "",
      date: (qs("manual-date") && qs("manual-date").value) || "",
      time: (qs("manual-time") && qs("manual-time").value) || "",
      party_size: (qs("manual-party") && qs("manual-party").value) || "",
      high_chairs_requested: (qs("manual-highchairs") && qs("manual-highchairs").value) || "0",
      preferred_area: (qs("manual-area") && qs("manual-area").value) || "",
      table_option: (qs("manual-table") && qs("manual-table").value) || "",
      special_requests: (qs("manual-notes") && qs("manual-notes").value) || "",
    };
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
    } catch (e) {}
  }

  function restoreDraft() {
    var raw;
    try {
      raw = sessionStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return;
    }
    if (!raw) return;
    var draft;
    try {
      draft = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (draft.customer_name && qs("manual-customer-name")) qs("manual-customer-name").value = draft.customer_name;
    if (draft.phone_number && qs("manual-phone")) qs("manual-phone").value = draft.phone_number;
    if (draft.date && qs("manual-date")) qs("manual-date").value = draft.date;
    if (draft.party_size && qs("manual-party")) qs("manual-party").value = draft.party_size;
    if (draft.high_chairs_requested != null && qs("manual-highchairs")) qs("manual-highchairs").value = draft.high_chairs_requested;
    if (draft.preferred_area != null && qs("manual-area")) qs("manual-area").value = draft.preferred_area;
    if (draft.special_requests && qs("manual-notes")) qs("manual-notes").value = draft.special_requests;
    if (draft.date) {
      loadTimeSlots(draft.date, draft.time);
      if (draft.time && draft.party_size) {
        loadTableOptions(draft.time, draft.table_option);
      }
    }
  }

  function fetchOptions(params) {
    var rid = pageRestaurantId();
    if (!rid) return Promise.reject();
    var q = new URLSearchParams(params);
    return fetch("/api/restaurant/" + encodeURIComponent(rid) + "/manual-reservation/options?" + q.toString()).then(
      function (r) {
        return r.json();
      }
    );
  }

  function fillTimeSelect(slots, selected) {
    var sel = qs("manual-time");
    if (!sel) return;
    sel.innerHTML = "";
    if (!slots || !slots.length) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No service slots for this date";
      sel.appendChild(empty);
      sel.disabled = true;
      return;
    }
    slots.forEach(function (slot) {
      var opt = document.createElement("option");
      opt.value = slot;
      opt.textContent = slot;
      if (selected === slot) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.disabled = false;
  }

  function fillTableSelect(options, selectedId) {
    var sel = qs("manual-table");
    if (!sel) return;
    sel.innerHTML = "";
    if (!options || !options.length) {
      var empty = document.createElement("option");
      empty.value = "";
      empty.textContent = "No free tables for this slot and party size";
      sel.appendChild(empty);
      sel.disabled = true;
      return;
    }
    options.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt.option_id;
      o.textContent = opt.label;
      o.dataset.tables = JSON.stringify(opt.assigned_tables);
      o.dataset.mergeId = opt.assigned_merge_id || "";
      if (selectedId === opt.option_id) o.selected = true;
      sel.appendChild(o);
    });
    sel.disabled = false;
  }

  function loadTimeSlots(dateVal, selectedTime) {
    return fetchOptions({ date: dateVal }).then(function (data) {
      if (!data.ok) return;
      fillTimeSelect(data.time_slots || [], selectedTime || "");
    });
  }

  function loadTableOptions(selectedTime, selectedTable) {
    var dateVal = qs("manual-date") && qs("manual-date").value;
    var party = qs("manual-party") && qs("manual-party").value;
    var area = qs("manual-area") && qs("manual-area").value;
    var high = qs("manual-highchairs") && qs("manual-highchairs").value;
    if (!dateVal || !selectedTime || !party) {
      fillTableSelect([], "");
      return Promise.resolve();
    }
    return fetchOptions({
      date: dateVal,
      time: selectedTime,
      party_size: party,
      preferred_area: area || "",
      high_chairs_requested: high || "0",
    }).then(function (data) {
      if (!data.ok) return;
      fillTableSelect(data.table_options || [], selectedTable || "");
    });
  }

  function buildCapacityHref() {
    var rid = pageRestaurantId();
    if (!rid) return "#";
    var dateVal = qs("manual-date") && qs("manual-date").value;
    var timeVal = qs("manual-time") && qs("manual-time").value;
    var params = new URLSearchParams({ from: "manual" });
    if (dateVal) params.set("service_date", dateVal);
    if (timeVal) params.set("time", timeVal);
    return "/add-restaurant-details/" + encodeURIComponent(rid) + "/capacity?" + params.toString();
  }

  function updateCapacityLink() {
    var link = qs("manual-view-capacity");
    if (!link) return;
    link.href = buildCapacityHref();
  }

  var form = document.getElementById("lab-manual-reservation-form");
  if (!form) return;

  readBootstrap();
  restoreDraft();
  updateCapacityLink();

  var capLink = qs("manual-view-capacity");
  if (capLink) {
    capLink.addEventListener("click", function () {
      saveDraft();
      capLink.href = buildCapacityHref();
    });
  }

  if (qs("manual-date")) {
    qs("manual-date").addEventListener("change", function () {
      var d = qs("manual-date").value;
      loadTimeSlots(d, "");
      fillTableSelect([], "");
      updateCapacityLink();
      saveDraft();
    });
  }
  if (qs("manual-time")) {
    qs("manual-time").addEventListener("change", function () {
      loadTableOptions(qs("manual-time").value, "");
      updateCapacityLink();
      saveDraft();
    });
  }
  function onPartyAreaChange() {
    if (qs("manual-time") && qs("manual-time").value) {
      loadTableOptions(qs("manual-time").value, "");
    }
    saveDraft();
  }
  ["manual-party", "manual-area", "manual-highchairs"].forEach(function (id) {
    var el = qs(id);
    if (el) {
      el.addEventListener("change", onPartyAreaChange);
      el.addEventListener("input", onPartyAreaChange);
    }
  });

  ["manual-customer-name", "manual-phone", "manual-notes", "manual-table"].forEach(function (id) {
    var el = qs(id);
    if (el) el.addEventListener("input", saveDraft);
    if (el) el.addEventListener("change", saveDraft);
  });

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var restaurantId = pageRestaurantId();
    var errEl = qs("lab-manual-error");
    var okEl = qs("lab-manual-success");
    var tableSel = qs("manual-table");
    if (errEl) errEl.style.display = "none";
    if (okEl) okEl.style.display = "none";
    if (!restaurantId || !tableSel || !tableSel.value) {
      if (errEl) {
        errEl.textContent = "Select a table.";
        errEl.style.display = "";
      }
      return;
    }
    var chosen = tableSel.options[tableSel.selectedIndex];
    var assignedTables;
    try {
      assignedTables = JSON.parse(chosen.getAttribute("data-tables") || "[]");
    } catch (e) {
      assignedTables = [];
    }
    var mergeId = chosen.getAttribute("data-merge-id") || null;
    if (!mergeId) mergeId = null;

    var payload = {
      restaurant_id: restaurantId,
      date: qs("manual-date").value,
      time: qs("manual-time").value,
      customer_name: qs("manual-customer-name").value.trim(),
      phone_number: qs("manual-phone").value.trim() || null,
      party_size: parseInt(qs("manual-party").value, 10),
      high_chairs_requested: parseInt(qs("manual-highchairs").value || "0", 10),
      preferences: {
        preferred_area: qs("manual-area").value.trim() || null,
        special_requests: qs("manual-notes").value.trim() || null,
      },
      assigned_tables: assignedTables,
      assigned_merge_id: mergeId,
      original_agent_text: "manual reservation from owner UI",
    };

    fetch("/api/restaurant/" + encodeURIComponent(restaurantId) + "/manual-reservation", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          if (errEl) {
            errEl.textContent = (res.data && res.data.error) || "Could not save reservation.";
            errEl.style.display = "";
          }
          return;
        }
        try {
          sessionStorage.removeItem(STORAGE_KEY);
        } catch (e) {}
        if (okEl) {
          okEl.textContent =
            "Reservation saved" +
            (res.data.reservation_id ? " (" + res.data.reservation_id + ")." : ".");
          okEl.style.display = "";
        }
        form.reset();
        qs("manual-highchairs").value = "0";
        fillTimeSelect([], "");
        fillTableSelect([], "");
        if (qs("manual-time")) qs("manual-time").disabled = true;
        if (qs("manual-table")) qs("manual-table").disabled = true;
      })
      .catch(function () {
        if (errEl) {
          errEl.textContent = "Network error.";
          errEl.style.display = "";
        }
      });
  });
})();
