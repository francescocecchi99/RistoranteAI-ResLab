(function () {
  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function openModal(modal) {
    if (!modal) return;
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModals() {
    document.querySelectorAll(".lab-modal").forEach(function (m) {
      m.hidden = true;
      m.setAttribute("aria-hidden", "true");
    });
  }

  document.addEventListener("click", function (ev) {
    if (ev.target.closest(".js-close-modal")) {
      closeModals();
      return;
    }

    var transcriptBtn = ev.target.closest(".js-open-transcript");
    if (transcriptBtn) {
      var modal = qs("#lab-transcript-modal");
      var body = qs("#lab-transcript-body");
      var summary = qs("#lab-transcript-summary");
      var url = transcriptBtn.getAttribute("data-transcript-url");
      if (body) body.textContent = "Loading…";
      if (summary) summary.textContent = "";
      openModal(modal);
      if (!url) {
        if (body) body.textContent = "(empty)";
        return;
      }
      fetch(url)
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          if (body) body.textContent = (data && data.transcript) || "(empty)";
          if (summary) {
            var s = (data && data.summary) || "";
            summary.textContent = s ? "Summary: " + s : "";
            summary.style.display = s ? "" : "none";
          }
        })
        .catch(function () {
          if (body) body.textContent = "Could not load transcript.";
        });
      return;
    }

    var playBtn = ev.target.closest(".js-play-recording");
    if (playBtn) {
      var url = playBtn.getAttribute("data-audio-url");
      var audio = qs("#lab-call-audio");
      if (!url || !audio) return;
      audio.pause();
      audio.src = url;
      audio.play().catch(function () {
        window.alert("Could not play recording.");
      });
      return;
    }

    var bookBtn = ev.target.closest(".js-book-manual");
    if (bookBtn) {
      var bookModal = qs("#lab-book-modal");
      var form = qs("#lab-book-form");
      if (!form) return;
      qs("#book-call-log-id", form).value = bookBtn.getAttribute("data-call-id") || "";
      var guest = bookBtn.getAttribute("data-guest") || "";
      if (guest && guest !== "—") qs("#book-customer-name", form).value = guest;
      var phone = bookBtn.getAttribute("data-phone") || "";
      if (phone && phone !== "—") qs("#book-phone", form).value = phone;
      qs("#lab-book-error", form).style.display = "none";
      openModal(bookModal);
    }
  });

  var bookForm = qs("#lab-book-form");
  if (bookForm) {
    bookForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var page = qs(".lab-inbound-page");
      var restaurantId = page && page.getAttribute("data-restaurant-id");
      var callLogId = qs("#book-call-log-id", bookForm).value;
      var errEl = qs("#lab-book-error", bookForm);
      if (!restaurantId || !callLogId) return;

      var payload = {
        restaurant_id: restaurantId,
        date: qs("#book-date", bookForm).value,
        time: qs("#book-time", bookForm).value,
        customer_name: qs("#book-customer-name", bookForm).value.trim(),
        phone_number: qs("#book-phone", bookForm).value.trim() || null,
        party_size: parseInt(qs("#book-party", bookForm).value, 10),
        high_chairs_requested: parseInt(qs("#book-highchairs", bookForm).value || "0", 10),
        preferences: {
          preferred_area: qs("#book-area", bookForm).value.trim() || null,
          special_requests: qs("#book-notes", bookForm).value.trim() || null,
        },
        original_agent_text: "manual booking from inbound calls UI",
      };

      fetch(
        "/api/voice-calls/" + encodeURIComponent(restaurantId) + "/" + encodeURIComponent(callLogId) + "/book-manually",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      )
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          if (!res.ok || !res.data.ok) {
            if (errEl) {
              errEl.textContent = (res.data && res.data.error) || "Booking failed.";
              errEl.style.display = "";
            }
            return;
          }
          closeModals();
          window.location.reload();
        })
        .catch(function () {
          if (errEl) {
            errEl.textContent = "Network error.";
            errEl.style.display = "";
          }
        });
    });
  }
})();
