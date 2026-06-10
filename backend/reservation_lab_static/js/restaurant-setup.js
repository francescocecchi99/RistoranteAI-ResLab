(function () {
  const LS_PREFIX = "finalCodeRestaurantDraft:";

  const STRIP_TINTS = [
    "rgba(245 236 228 / 0.75)",
    "rgba(235 224 214 / 0.7)",
    "rgba(228 218 206 / 0.65)",
    "rgba(240 228 218 / 0.7)",
    "rgba(232 222 210 / 0.65)",
  ];

  const ACCENT_BORDER = [
    "rgb(166 77 50)",
    "rgb(124 90 72)",
    "rgb(180 120 90)",
    "rgb(140 95 70)",
    "rgb(110 78 62)",
  ];

  function deepClone(o) {
    return JSON.parse(JSON.stringify(o));
  }

  function storageKey(rid) {
    return LS_PREFIX + rid;
  }

  function debounce(fn, ms) {
    let t;
    return function () {
      const a = arguments;
      clearTimeout(t);
      t = setTimeout(() => fn.apply(null, a), ms);
    };
  }

  function toast(msg, ok) {
    const el = document.createElement("div");
    el.className = "lab-toast " + (ok ? "ok" : "err");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  function sortTableIds(ids) {
    return [...ids].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
  }

  function allocStrategyOptions() {
    return [
      "prefer_single_table",
      "minimize_wasted_seats",
      "minimize_number_of_tables_used",
      "preserve_large_tables",
    ];
  }

  function chairLayout(seats) {
    const n = Math.max(1, Math.floor(seats));
    if (n === 1) return { top: 0, bottom: 1, left: 0, right: 0 };
    if (n === 2) return { top: 1, bottom: 1, left: 0, right: 0 };
    if (n === 3) return { top: 1, bottom: 1, left: 1, right: 0 };
    if (n === 4) return { top: 2, bottom: 2, left: 0, right: 0 };
    if (n === 5) return { top: 2, bottom: 2, left: 1, right: 0 };
    if (n === 6) return { top: 3, bottom: 3, left: 0, right: 0 };
    if (n === 7) return { top: 3, bottom: 3, left: 1, right: 0 };
    if (n === 8) return { top: 4, bottom: 4, left: 0, right: 0 };
    const top = Math.ceil(n / 2);
    return { top, bottom: n - top, left: 0, right: 0 };
  }

  function areaStrokeForLocation(zoneList, location) {
    const i = zoneList.indexOf(location);
    const idx = i >= 0 ? i : 0;
    return ACCENT_BORDER[idx % ACCENT_BORDER.length];
  }

  function clampHead(t) {
    return Math.min(2, Math.max(0, Math.floor(Number(t.max_head_seat_extra_capacity) || 0)));
  }

  function chairArc(side, stroke, narrow, dashed) {
    const w = narrow ? 12 : 16;
    const h = narrow ? 5 : 7;
    const ds = dashed ? " lab-chair--head" : "";
    if (side === "top") {
      return (
        '<div class="lab-chair lab-chair--top' +
        ds +
        '" style="width:' +
        w +
        "px;height:" +
        h +
        "px;border-color:" +
        stroke +
        '"></div>'
      );
    }
    if (side === "bottom") {
      return (
        '<div class="lab-chair lab-chair--bottom' +
        ds +
        '" style="width:' +
        w +
        "px;height:" +
        h +
        "px;border-color:" +
        stroke +
        '"></div>'
      );
    }
    if (side === "left") {
      return (
        '<div class="lab-chair lab-chair--left' +
        ds +
        '" style="width:' +
        h +
        "px;height:" +
        w +
        "px;border-color:" +
        stroke +
        '"></div>'
      );
    }
    return (
      '<div class="lab-chair lab-chair--right' +
      ds +
      '" style="width:' +
      h +
      "px;height:" +
      w +
      "px;border-color:" +
      stroke +
      '"></div>'
    );
  }

  function chairPlaceholder(narrow) {
    const w = narrow ? 12 : 16;
    const h = narrow ? 5 : 7;
    return '<div class="lab-chair-ph" style="width:' + h + "px;height:" + w + 'px"></div>';
  }

  function tableCoreClass(L, seats) {
    const tbOnly = L.left === 0 && L.right === 0;
    const twoTb = seats === 2 && tbOnly;
    if (twoTb) return "lab-table-core";
    if (!tbOnly) return "lab-table-core lab-table-core--wide";
    const along = Math.max(L.top, L.bottom);
    if (along <= 2) return "lab-table-core";
    if (along === 3) return "lab-table-core lab-table-core--wide";
    return "lab-table-core lab-table-core--xlarge";
  }

  function buildSeatDiagramHTML(seats, freeStroke, headExtra) {
    const n = Math.max(1, Math.floor(seats));
    const L = chairLayout(n);
    const stroke = freeStroke || "rgb(23 23 23)";
    const fill = "rgb(255 255 255)";
    const nHead = headExtra > 0 ? Math.min(2, Math.floor(headExtra)) : 0;
    const maxAlong = Math.max(L.top, L.bottom, 1);
    const narrow = maxAlong >= 4;
    const headRight = Math.ceil(nHead / 2);
    const headLeft = Math.floor(nHead / 2);
    const phantomLeft = headRight > 0 && headLeft === 0 ? headRight : 0;
    const phantomRight = headLeft > 0 && headRight === 0 ? headLeft : 0;
    const coreCls = tableCoreClass(L, n);
    let html = '<div class="lab-diagram">';
    if (headLeft > 0 || phantomLeft > 0) {
      html += '<div class="lab-chair-stack lab-chair-stack--pull">';
      for (let i = 0; i < headLeft; i++) html += chairArc("left", stroke, narrow, true);
      for (let i = 0; i < phantomLeft; i++) html += chairPlaceholder(narrow);
      html += "</div>";
    }
    html += '<div class="lab-diagram-col">';
    if (L.top > 0) {
      html += '<div class="lab-chair-row">';
      for (let i = 0; i < L.top; i++) html += chairArc("top", stroke, narrow, false);
      html += "</div>";
    }
    html += '<div class="lab-table-row">';
    if (L.left > 0) {
      html += '<div class="lab-chair-stack">';
      for (let i = 0; i < L.left; i++) html += chairArc("left", stroke, narrow, false);
      html += "</div>";
    }
    html +=
      '<div class="' +
      coreCls +
      '" style="border-color:' +
      stroke +
      ";background:" +
      fill +
      '"><span class="lab-table-digit">' +
      n +
      "</span></div>";
    if (L.right > 0) {
      html += '<div class="lab-chair-stack">';
      for (let i = 0; i < L.right; i++) html += chairArc("right", stroke, narrow, false);
      html += "</div>";
    }
    html += "</div>";
    if (L.bottom > 0) {
      html += '<div class="lab-chair-row">';
      for (let i = 0; i < L.bottom; i++) html += chairArc("bottom", stroke, narrow, false);
      html += "</div>";
    }
    html += "</div>";
    if (phantomRight > 0 || headRight > 0) {
      html += '<div class="lab-chair-stack lab-chair-stack--pull-left">';
      for (let i = 0; i < phantomRight; i++) html += chairPlaceholder(narrow);
      for (let i = 0; i < headRight; i++) html += chairArc("right", stroke, narrow, true);
      html += "</div>";
    }
    html += "</div>";
    return html;
  }

  let state = deepClone(window.__INITIAL_BUNDLE__ || {});

  function persistLocal() {
    try {
      localStorage.setItem(storageKey(state.restaurant_id), JSON.stringify(state));
    } catch (e) {
      console.warn(e);
    }
  }

  const persistDebounced = debounce(persistLocal, 400);

  function tryLoadLocal() {
    const raw = localStorage.getItem(storageKey(state.restaurant_id));
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && parsed.restaurant_id === state.restaurant_id) {
        state = parsed;
      }
    } catch (e) {
      console.warn(e);
    }
  }

  function areaNames() {
    return (state.areas || []).map((a) => a.area_name).filter(Boolean);
  }

  function tablesInZone(zone, zoneList, tables) {
    if (zone === "__other__") {
      return sortTableIds(
        tables.filter((t) => !zoneList.includes(String(t.location || ""))).map((t) => t.table_id),
      )
        .map((id) => tables.find((x) => x.table_id === id))
        .filter(Boolean);
    }
    return sortTableIds(tables.filter((t) => t.location === zone).map((t) => t.table_id))
      .map((id) => tables.find((x) => x.table_id === id))
      .filter(Boolean);
  }

  function syncLayoutFromMapGrid() {
    state.floor_layout = state.floor_layout || { tables: {} };
    state.floor_layout.tables = {};
    const zoneList = areaNames();
    const tables = state.tables || [];
    const orphans = tables.filter((t) => !zoneList.includes(String(t.location || "")));
    const strips = orphans.length > 0 ? [...zoneList, "__other__"] : zoneList;
    let stripIdx = 0;
    const cols = 4;
    strips.forEach((zone) => {
      const units = tablesInZone(zone, zoneList, tables);
      units.forEach((t, ii) => {
        const col = ii % cols;
        const row = Math.floor(ii / cols);
        const lay = {
          xPct: 1.5 + col * 24,
          yPct: 0.5 + stripIdx * 22 + row * 10,
          wPct: 21,
          hPct: 9,
        };
        t.layout = { ...lay };
        state.floor_layout.tables[t.table_id] = { ...lay };
      });
      stripIdx++;
    });
  }

  function renderTableMapView(mount) {
    if (!mount) return;
    const zoneList = areaNames();
    const tables = state.tables || [];
    const orphans = tables.filter((t) => !zoneList.includes(String(t.location || "")));
    const strips = orphans.length > 0 ? [...zoneList, "__other__"] : zoneList;
    const notInList =
      (window.labI18n && window.labI18n.t("mapNotInList")) || "Not in list (assign in settings)";
    let html = '<div class="lab-map-scroll">';
    strips.forEach((zone, stripIdx) => {
      const tint = STRIP_TINTS[stripIdx % STRIP_TINTS.length];
      const label = zone === "__other__" ? notInList : esc(zone);
      const units = tablesInZone(zone, zoneList, tables);
      html += '<div class="lab-map-strip" style="background:' + tint + '">';
      html += '<div class="lab-map-strip-label">' + label + "</div>";
      html += '<div class="lab-map-grid">';
      units.forEach((t) => {
        const stroke =
          zone === "__other__"
            ? ACCENT_BORDER[stripIdx % ACCENT_BORDER.length]
            : areaStrokeForLocation(zoneList, t.location);
        const head = clampHead(t);
        const headBadge = head > 0 ? "+" + head : "";
        const disp = (t.name || "").trim() || t.table_id;
        html += '<div class="lab-map-tile" style="border-color:' + stroke + '">';
        html += '<div class="lab-map-tile-head">';
        html += '<span class="lab-map-tile-name" title="' + esc(disp) + '">' + esc(disp) + "</span>";
        html +=
          '<span class="lab-map-tile-meta" title="' +
          esc(t.location) +
          '">' +
          esc(String(t.location)) +
          " · " +
          (t.base_capacity | 0) +
          headBadge +
          "</span>";
        html += "</div>";
        html += buildSeatDiagramHTML(Math.max(1, t.base_capacity | 0), stroke, head);
        html += "</div>";
      });
      html += "</div></div>";
    });
    html += "</div>";
    mount.innerHTML = html;
    syncLayoutFromMapGrid();
  }

  function syncFloorFromTables() {
    syncLayoutFromMapGrid();
  }

  let sectionNavTeardown = null;

  function initSectionNavSpy() {
    if (sectionNavTeardown) {
      sectionNavTeardown();
      sectionNavTeardown = null;
    }
    const root = document.querySelector(".lab-setup-content");
    const aside = document.querySelector(".lab-section-sidebar");
    const links = document.querySelectorAll("[data-section-nav]");
    if (!root || !aside || !links.length) return;

    const sectionIds = ["section-identity", "section-areas", "section-rules", "section-tables", "section-2d-map"];
    const sections = sectionIds.map((id) => document.getElementById(id)).filter(Boolean);
    if (!sections.length) return;

    function setActiveById(id) {
      if (!id || !sectionIds.includes(id)) return;
      links.forEach((l) => {
        const nav = l.getAttribute("data-section-nav");
        l.classList.toggle("is-active", nav === id);
      });
    }

    function onAsideClick(e) {
      const t = e.target.closest("a[data-section-nav]");
      if (!t || !aside.contains(t)) return;
      const id = (t.getAttribute("href") || "").replace(/^#/, "");
      if (id) requestAnimationFrame(() => setActiveById(id));
    }
    aside.addEventListener("click", onAsideClick);

    function refreshActiveByTopmostVisible() {
      const rootRect = root.getBoundingClientRect();
      const topLock = rootRect.top + 10;
      let chosen = sections[0];
      sections.forEach((section) => {
        const r = section.getBoundingClientRect();
        if (r.top <= topLock) chosen = section;
      });
      setActiveById(chosen.id);
    }

    root.addEventListener("scroll", refreshActiveByTopmostVisible, { passive: true });

    const hash = typeof location !== "undefined" ? location.hash.replace(/^#/, "") : "";
    if (hash === "lab-map-2d-title") setActiveById("section-2d-map");
    else if (sectionIds.includes(hash)) setActiveById(hash);
    else refreshActiveByTopmostVisible();

    sectionNavTeardown = () => {
      root.removeEventListener("scroll", refreshActiveByTopmostVisible);
      aside.removeEventListener("click", onAsideClick);
    };
  }

  function render() {
    const root = document.getElementById("setup-root");
    if (!root) return;
    const names = areaNames();
    const strategies = allocStrategyOptions();
    let html = "";

    html += '<section id="section-identity" class="lab-setup-section"><div class="lab-card"><h2 data-i18n="sectionIdentity">Identity</h2><div class="lab-formgrid">';
    html +=
      '<div class="lab-field"><label data-i18n="labelBusinessName">Business</label><input type="text" data-bind="business.business_name" value="' +
      esc(state.business?.business_name || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelRestaurantName">Restaurant</label><input type="text" data-bind="restaurant_name" value="' +
      esc(state.restaurant_name || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelTimezone">TZ</label><input type="text" data-bind="timezone" value="' +
      esc(state.timezone || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelStreet">Street</label><input type="text" data-bind="street" value="' +
      esc(state.street || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label>№</label><input type="text" data-bind="street_number" value="' +
      esc(state.street_number || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label>ZIP</label><input type="text" data-bind="postal_code" value="' +
      esc(state.postal_code || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelCity">City</label><input type="text" data-bind="city" value="' +
      esc(state.city || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelCountry">Country</label><input type="text" data-bind="country" value="' +
      esc(state.country || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelEmailAddress">Email address</label><input type="email" data-bind="email_address" value="' +
      esc(state.email_address || "") +
      '" /></div>';
    html +=
      '<div class="lab-field"><label data-i18n="labelWhatsapp">Whatsapp</label><input type="text" data-bind="whatsapp_phone_number" value="' +
      esc(state.whatsapp_phone_number || "") +
      '" /></div>';
    if (window.__IS_NEW_PROFILE__) {
      html += '<div class="lab-field lab-field--full"><h3 class="lab-setup-subhead" data-i18n="ownerLoginSection">Owner login</h3></div>';
      html +=
        '<div class="lab-field"><label data-i18n="labelOwnerUsername">Login username</label><input type="text" autocomplete="username" data-bind="owner_login.username" value="' +
        esc(state.owner_login?.username || "") +
        '" /></div>';
      html +=
        '<div class="lab-field"><label data-i18n="labelOwnerPassword">Login password</label><input type="password" autocomplete="new-password" data-bind="owner_login.password" value="" placeholder="" data-i18n-placeholder="ownerPasswordPlaceholder" /></div>';
      html += '<p class="lab-muted lab-field--full" data-i18n="ownerPasswordHintNew">Min 8 characters. Used to sign in after the profile is saved.</p>';
    }
    html += "</div></div></section>";

    html += '<section id="section-areas" class="lab-setup-section"><div class="lab-card"><h2 data-i18n="sectionAreas">Areas</h2>';
    (state.areas || []).forEach((a, idx) => {
      html += '<div class="lab-areas-row lab-inline" data-area-idx="' + idx + '">';
      html += '<input type="text" style="flex:1;min-width:140px" data-area-field="area_name" value="' + esc(a.area_name) + '" />';
      html += '<input type="hidden" data-area-field="area_id" value="' + esc(a.area_id) + '" />';
      html += '<button type="button" class="lab-btn lab-btn-secondary lab-area-up" data-i18n="btnUp">Up</button>';
      html += '<button type="button" class="lab-btn lab-btn-secondary lab-area-down" data-i18n="btnDown">Down</button>';
      html += '<button type="button" class="lab-btn lab-btn-secondary lab-area-remove" data-i18n="btnRemove">×</button>';
      html += "</div>";
    });
    html +=
      '<div class="lab-actions" style="margin-top:0.75rem"><button type="button" class="lab-btn lab-btn-secondary" id="btn-add-area" data-i18n="btnAddArea">+ Area</button></div>';
    html += "</div></section>";

    html += '<section id="section-rules" class="lab-setup-section"><div class="lab-card"><h2 data-i18n="sectionRules">Rules</h2><div class="lab-formgrid">';
    html +=
      '<div class="lab-field"><label>High chairs (inventory)</label><input type="number" min="0" data-bind="number_of_high_chairs" value="' +
      (state.number_of_high_chairs | 0) +
      '" /></div>';
    html += '<div class="lab-field"><label>Allocation strategy</label><select data-bind="allocation_strategy">';
    strategies.forEach((s) => {
      html +=
        '<option value="' +
        esc(s) +
        '"' +
        (state.allocation_strategy === s ? " selected" : "") +
        ">" +
        esc(s) +
        "</option>";
    });
    html += "</select></div>";
    html +=
      '<div class="lab-field"><label>Max tables / merge</label><input type="number" min="1" data-bind="max_tables_per_merge" value="' +
      (state.max_tables_per_merge | 0) +
      '" /></div>';
    html +=
      '<div class="lab-field"><label>Max capacity-equivalent moves</label><input type="number" min="0" data-bind="max_capacity_equivalent_moves_allowed" value="' +
      (state.max_capacity_equivalent_moves_allowed | 0) +
      '" /></div>';
    html +=
      '<div class="lab-field"><label><input type="checkbox" data-bind-bool="merge_allowed" ' +
      (state.merge_allowed ? "checked" : "") +
      " /> Merge allowed</label></div>";
    html +=
      '<div class="lab-field"><label><input type="checkbox" data-bind-bool="move_between_areas_allowed" ' +
      (state.move_between_areas_allowed ? "checked" : "") +
      " /> Move between areas</label></div>";
    html +=
      '<div class="lab-field"><label><input type="checkbox" data-bind-bool="default_location_fallback_allowed" ' +
      (state.default_location_fallback_allowed ? "checked" : "") +
      " /> Fallback to other areas</label></div>";
    html += "</div></div></section>";

    html +=
      '<section id="section-tables" class="lab-setup-section"><div class="lab-card lab-card--tables"><div class="lab-card-head"><h2 data-i18n="sectionTables">Tables</h2><span class="lab-pill-count">' +
      (state.tables || []).length +
      "</span></div><div class=\"lab-table-grid\">";
    (state.tables || []).forEach((t, ti) => {
      html += '<div class="lab-table-block" data-table-idx="' + ti + '">';
      html += '<div class="lab-formgrid">';
      html +=
        '<div class="lab-field"><label>id</label><input type="text" data-tf="table_id" value="' +
        esc(t.table_id) +
        '" /></div>';
      html +=
        '<div class="lab-field"><label>Name</label><input type="text" data-tf="name" value="' +
        esc(t.name) +
        '" /></div>';
      html +=
        '<div class="lab-field"><label>Base cap.</label><input type="number" min="1" data-tf="base_capacity" value="' +
        (t.base_capacity | 0) +
        '" /></div>';
      html += '<div class="lab-field"><label>Area</label><select data-tf="location">';
      names.forEach((n) => {
        html += '<option value="' + esc(n) + '"' + (t.location === n ? " selected" : "") + ">" + esc(n) + "</option>";
      });
      html += "</select></div>";
      html +=
        '<div class="lab-field"><label>Head seats max</label><input type="number" min="0" max="2" data-tf="max_head_seat_extra_capacity" value="' +
        (t.max_head_seat_extra_capacity | 0) +
        '" /></div>';
      html +=
        '<div class="lab-field"><label><input type="checkbox" data-tf-bool="movable_to_other_area" ' +
        (t.movable_to_other_area ? "checked" : "") +
        " /> Movable</label></div>";
      html +=
        '<div class="lab-field"><label><input type="checkbox" data-tf-bool="high_chair_compatible" ' +
        (t.high_chair_compatible ? "checked" : "") +
        " /> High chair OK</label></div>";
      html +=
        '<div class="lab-field"><label><input type="checkbox" data-tf-bool="prefer_keep_free" ' +
        (t.prefer_keep_free ? "checked" : "") +
        " /> Prefer keep free</label></div>";
      html += "</div>";
      html +=
        '<div class="lab-field" style="margin-top:0.5rem"><label data-i18n="mergeHint">Mergeable</label><input type="text" style="width:100%" data-tf="mergeable_csv" value="' +
        esc((t.mergeable_with || []).join(", ")) +
        '" placeholder="o5, o6" /></div>';
      html +=
        '<button type="button" class="lab-btn lab-btn-secondary lab-table-remove" style="margin-top:0.5rem" data-i18n="btnRemove">Remove table</button>';
      html += "</div>";
    });
    html += "</div>";
    html +=
      '<div class="lab-actions" style="margin-top:0.75rem"><button type="button" class="lab-btn lab-btn-secondary" id="btn-add-table" data-i18n="btnAddTable">+ Table</button></div>';
    html += "</div></section>";

    html += '<section id="section-2d-map" class="lab-setup-section lab-map-section" aria-labelledby="lab-map-2d-title">';
    html += '<h2 id="lab-map-2d-title" class="lab-map-section-title" data-i18n="sectionMapTitle">2D map</h2>';
    html += '<p class="lab-map-section-desc" data-i18n="sectionMapDesc"></p>';
    html += '<div class="lab-map-static-head">';
    html += '<h3 class="lab-map-static-title" data-i18n="mapStaticTitle"></h3>';
    html += '<p class="lab-map-zone-hint" data-i18n="mapZoneHint"></p>';
    html += "</div>";
    html += '<div id="lab-map-mount"></div>';
    html += "</section>";

    html += '<div class="lab-save-footer">';
    html +=
      '<button type="button" class="lab-btn lab-btn-dark" id="btn-save-db" data-i18n="btnSaveDb">Save to database</button>';
    html += "</div>";

    root.innerHTML = html;
    if (window.labI18n) window.labI18n.apply();
    bind();
    renderTableMapView(document.getElementById("lab-map-mount"));
    persistDebounced();
    initSectionNavSpy();
    if (typeof location !== "undefined") {
      const h = location.hash;
      if (h === "#lab-map-2d-title" || h === "#section-2d-map") {
        requestAnimationFrame(() => {
          document.getElementById("section-2d-map")?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      }
    }
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function bind() {
    document.querySelectorAll("[data-bind]").forEach((el) => {
      const path = el.getAttribute("data-bind");
      el.addEventListener("input", () => {
        if (path === "business.business_name") state.business = state.business || {};
        setPath(state, path, el.value);
        persistDebounced();
        const m = document.getElementById("lab-map-mount");
        if (m) renderTableMapView(m);
      });
    });
    document.querySelectorAll("[data-bind-bool]").forEach((el) => {
      const path = el.getAttribute("data-bind-bool");
      el.addEventListener("change", () => {
        setPath(state, path, el.checked);
        persistDebounced();
        const m = document.getElementById("lab-map-mount");
        if (m) renderTableMapView(m);
      });
    });
    document.querySelectorAll("[data-area-idx]").forEach((row) => {
      const idx = +row.getAttribute("data-area-idx");
      row.querySelector("[data-area-field=area_name]")?.addEventListener("input", (e) => {
        state.areas[idx].area_name = e.target.value;
        persistDebounced();
        render();
      });
      row.querySelector(".lab-area-up")?.addEventListener("click", () => {
        if (idx > 0) {
          const a = state.areas.splice(idx, 1)[0];
          state.areas.splice(idx - 1, 0, a);
          state.areas.forEach((x, i) => (x.sort_order = i));
          persistDebounced();
          render();
        }
      });
      row.querySelector(".lab-area-down")?.addEventListener("click", () => {
        if (idx < state.areas.length - 1) {
          const a = state.areas.splice(idx, 1)[0];
          state.areas.splice(idx + 1, 0, a);
          state.areas.forEach((x, i) => (x.sort_order = i));
          persistDebounced();
          render();
        }
      });
      row.querySelector(".lab-area-remove")?.addEventListener("click", () => {
        if (state.areas.length < 2) return;
        state.areas.splice(idx, 1);
        state.areas.forEach((x, i) => (x.sort_order = i));
        persistDebounced();
        render();
      });
    });
    document.getElementById("btn-add-area")?.addEventListener("click", () => {
      state.areas.push({
        area_id: "area_" + Math.random().toString(36).slice(2, 10),
        area_name: "Area " + (state.areas.length + 1),
        sort_order: state.areas.length,
      });
      persistDebounced();
      render();
    });
    document.querySelectorAll("[data-table-idx]").forEach((block) => {
      const ti = +block.getAttribute("data-table-idx");
      block.querySelectorAll("[data-tf]").forEach((el) => {
        const f = el.getAttribute("data-tf");
        el.addEventListener("input", () => {
          if (f === "mergeable_csv") {
            state.tables[ti].mergeable_with = el.value
              .split(/[,;\s]+/)
              .map((x) => x.trim())
              .filter(Boolean);
            state.tables[ti].mergeable = state.tables[ti].mergeable_with.length > 0;
          } else if (f === "base_capacity" || f === "max_head_seat_extra_capacity") {
            state.tables[ti][f] = +el.value || 0;
          } else {
            state.tables[ti][f] = el.value;
          }
          persistDebounced();
          const m = document.getElementById("lab-map-mount");
          if (m) renderTableMapView(m);
        });
      });
      block.querySelectorAll("[data-tf-bool]").forEach((el) => {
        const f = el.getAttribute("data-tf-bool");
        el.addEventListener("change", () => {
          state.tables[ti][f] = el.checked;
          persistDebounced();
          const m = document.getElementById("lab-map-mount");
          if (m) renderTableMapView(m);
        });
      });
      block.querySelector(".lab-table-remove")?.addEventListener("click", () => {
        const tid = state.tables[ti].table_id;
        state.tables.splice(ti, 1);
        if (state.floor_layout?.tables) delete state.floor_layout.tables[tid];
        persistDebounced();
        render();
      });
    });
    document.getElementById("btn-add-table")?.addEventListener("click", () => {
      const n = state.tables.length + 1;
      const id = "t" + Math.random().toString(36).slice(2, 6);
      const nm = areaNames()[0] || "Indoor";
      state.tables.push({
        table_id: id,
        name: "Table " + n,
        base_capacity: 4,
        location: nm,
        special_attributes: [],
        max_head_seat_extra_capacity: 0,
        movable_to_other_area: false,
        high_chair_compatible: true,
        prefer_keep_free: false,
        mergeable: false,
        merge_group_id: null,
        mergeable_with: [],
        active: true,
        layout: { xPct: 6, yPct: 8, wPct: 14, hPct: 16 },
      });
      persistDebounced();
      render();
    });
    document.getElementById("btn-save-db")?.addEventListener("click", saveDb);
  }

  function setPath(obj, path, val) {
    const parts = path.split(".");
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i++) {
      cur = cur[parts[i]] = cur[parts[i]] || {};
    }
    cur[parts[parts.length - 1]] = val;
  }

  async function saveDb() {
    if (!(state.business?.business_name || "").trim() || !(state.restaurant_name || "").trim()) {
      toast("Fill business name and restaurant name.", false);
      return;
    }
    if (window.__IS_NEW_PROFILE__) {
      state.owner_login = state.owner_login || { username: "", password: "" };
      const ownerUser = (state.owner_login.username || "").trim();
      const ownerPass = state.owner_login.password || "";
      if (!ownerUser || !ownerPass) {
        toast(window.labI18n ? window.labI18n.t("ownerLoginRequired") : "Set owner login username and password.", false);
        return;
      }
      if (ownerPass.length < 8) {
        toast(window.labI18n ? window.labI18n.t("ownerPasswordTooShort") : "Password must be at least 8 characters.", false);
        return;
      }
    }
    const m = document.getElementById("lab-map-mount");
    if (m) renderTableMapView(m);
    else syncFloorFromTables();
    persistLocal();
    const payload = deepClone(state);
    try {
      const res = await fetch("/api/restaurant/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || res.statusText);
      if (state.owner_login) state.owner_login.password = "";
      toast(window.labI18n ? window.labI18n.t("toastSaved") : "Saved", true);
    } catch (e) {
      toast((window.labI18n ? window.labI18n.t("toastError") : "Error") + " " + e.message, false);
    }
  }

  function init() {
    if (!state.restaurant_id) {
      state = {
        restaurant_id: "r_" + Math.random().toString(36).slice(2, 10),
        business: { business_id: "b_" + Math.random().toString(36).slice(2, 10), business_name: "", business_type: "restaurant" },
        restaurant_name: "",
        areas: [{ area_id: "area_" + Math.random().toString(36).slice(2, 8), area_name: "Indoor", sort_order: 0 }],
        tables: [],
        floor_layout: { tables: {} },
        number_of_high_chairs: 0,
        merge_allowed: true,
        max_tables_per_merge: 4,
        move_between_areas_allowed: false,
        max_capacity_equivalent_moves_allowed: 0,
        default_location_fallback_allowed: true,
        allocation_strategy: "prefer_single_table",
        timezone: "Europe/Rome",
        street: "",
        street_number: "",
        postal_code: "",
        city: "",
        country: "",
        email_address: "",
        whatsapp_phone_number: "",
        owner_login: { username: "", password: "" },
      };
    }
    if (!state.owner_login) {
      state.owner_login = { username: "", password: "" };
    }
    tryLoadLocal();
    if (!state.tables || !state.tables.length) {
      state.tables = [
        {
          table_id: "t1",
          name: "Table 1",
          base_capacity: 4,
          location: state.areas[0].area_name,
          max_head_seat_extra_capacity: 0,
          movable_to_other_area: false,
          high_chair_compatible: true,
          prefer_keep_free: false,
          mergeable_with: [],
          mergeable: false,
          layout: { xPct: 6, yPct: 8, wPct: 14, hPct: 16 },
        },
      ];
    }
    state.tables.forEach((t) => {
      if (!t.layout) t.layout = { xPct: 6, yPct: 8, wPct: 14, hPct: 16 };
    });
    render();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
