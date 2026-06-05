(function () {
  const STR = {
    en: {
      kicker: "Hospitality OS",
      appName: "Reservation Lab",
      tableAllocationSubtitle: "Table allocation",
      language: "Language",
      navHome: "Home",
      navAddRestaurant: "Add restaurant details",
      nav2dMap: "2D map",
      homeKicker: "Reservation Lab",
      homeTitle: "Restaurant Voice Agent",
      homeLead: "Open the allocation prototype (Reservation Lab):",
      homeCtaAdd: "Add restaurant details",
      homeOwnerView: "Owner view",
      homeAgentView: "Agent view",
      agentNavOwner: "Owner setup",
      agentNavHere: "Agent requests",
      agentPageTitle: "Voice agent request console",
      agentPageLead:
        "This is not the marketing home—you are in the agent demo. POST JSON like production: check availability, then confirm the pending hold with customer_name.",
      homeCardAgent: "Test agent request",
      homeCardAgentHint: "POST JSON as the voice agent would send it.",
      createStepTitle: "Confirm reservation (step 2)",
      createStepLead: "Pending reservation is created after check. Send final JSON (same payload + customer_name) to confirm.",
      labelCustomerName: "Customer name",
      btnCheck: "Check availability",
      btnCreate: "Create reservation",
      btnConfirm: "Confirm reservation",
      homeFooterHint:
        "If a button spins too long: restart with flask --app app.main_flask_demo run --debug --no-reload (Windows reload quirk) or check Supabase pooler URL.",
      pickTitle: "Add restaurant details",
      pickLead: "Pick a restaurant to edit or create a new one. Drafts are kept in this browser until you save to the database.",
      pickNew: "New restaurant",
      pickEmpty: "No restaurants in the database yet. Create a new one.",
      pickLinkSettings: "Initial settings",
      pickLinkCapacity: "Capacity",
      capacityTitle: "Capacity",
      capacityLead:
        "Choose date and turn to see real occupancy from the database: confirmed reservations, assigned tables, and head seats for the service.",
      capacityBackToList: "← Back to restaurant list",
      capacityDate: "Date",
      capacityTurn: "Turn",
      capacityApply: "Apply",
      capacityOccupied: "Seats occupied (confirmed + pending)",
      capacityFree: "Seats free (base)",
      capacityBaseTotal: "Max base seats (all tables)",
      capacityPendingNote: "Pending reservations (hold tables; not listed as confirmed below):",
      capacityConfirmedTitle: "Confirmed reservations",
      capacityColGuest: "Guest",
      capacityColPhone: "Phone",
      capacityColTime: "Time",
      capacityColParty: "Covers",
      capacityColTables: "Tables",
      capacityColHighchairs: "High chairs",
      capacityCopyAsText: "Copy as text",
      capacityCopyAsImage: "Copy as image",
      capacityCopied: "Copied to clipboard.",
      capacityImageCopied: "Image copied to clipboard.",
      capacityEmptyConfirmed: "No confirmed reservations for this turn.",
      capacityMapTitle: "Occupancy",
      capacityMapDesc:
        "Areas follow settings order. Tiles wrap (min ~9rem). Guest names and seat icons appear only on tables that have a reservation for this turn (confirmed or pending; pending tiles use a dashed outline). Free tables show layout text only—no chair diagram.",
      capacityZoneOther: "Not in list",
      capacityNoTurns: "No service turns configured for this weekday.",
      capacityLoadError: "Could not load capacity.",
      setupBack: "← Back to list",
      setupTitle: "Initial settings",
      setupLead:
        "Define areas, table capacities, merge pairs, and global rules below. The 2D map (same layout as Reservation Lab) updates from your tables and areas. Save to database when ready.",
      sectionMapTitle: "2D map",
      sectionMapDesc:
        "See every table in its zone on a simple floor preview. Placement follows the zone grid; merged booking groups appear on Capacity in the full app.",
      mapStaticTitle: "Static map (layout only — one tile per table)",
      mapZoneHint:
        "Areas stack vertically; tiles wrap (min ~9rem). Colors follow dining area order.",
      mapNotInList: "Not in list (assign in settings)",
      sectionIdentity: "Restaurant identity",
      sectionAreas: "Areas",
      sectionRules: "Restaurant rules",
      sectionTables: "Tables",
      sectionMap: "Floor map",
      navSectionIdentity: "Restaurant identity",
      navSectionAreas: "Areas",
      navSectionRules: "Restaurant rules",
      navSectionTables: "Tables",
      navSection2dMap: "2D map",
      labelBusinessName: "Business name",
      labelRestaurantName: "Restaurant display name",
      labelTimezone: "Timezone",
      labelStreet: "Street",
      labelCity: "City",
      labelCountry: "Country",
      labelEmailAddress: "Email address",
      labelWhatsapp: "Whatsapp",
      btnAddArea: "+ Add area",
      btnAddTable: "+ Add table",
      btnSaveDb: "Save to database",
      btnRemove: "Remove",
      btnUp: "Up",
      btnDown: "Down",
      toastSaved: "Saved to database.",
      toastError: "Save failed.",
      mergeHint: "Mergeable with (table ids, comma-separated)",
    },
    it: {
      kicker: "Hospitality OS",
      appName: "Reservation Lab",
      tableAllocationSubtitle: "Allocazione tavoli",
      language: "Lingua",
      navHome: "Home",
      navAddRestaurant: "Aggiungi dettagli ristorante",
      nav2dMap: "Mappa 2D",
      homeKicker: "Reservation Lab",
      homeTitle: "Restaurant Voice Agent",
      homeLead: "Apri il prototipo di allocazione (Reservation Lab):",
      homeCtaAdd: "Aggiungi dettagli ristorante",
      homeOwnerView: "Vista proprietario",
      homeAgentView: "Vista agente",
      agentNavOwner: "Setup proprietario",
      agentNavHere: "Richieste agente",
      agentPageTitle: "Console richieste agente vocale",
      agentPageLead:
        "Non sei sulla home marketing: questa è la console di prova. Invia il JSON come in produzione (check disponibilità), poi conferma la pending con customer_name.",
      homeCardAgent: "Richiesta agente",
      homeCardAgentHint: "Invia JSON come farebbe l’agente vocale.",
      createStepTitle: "Conferma prenotazione (step 2)",
      createStepLead: "Dopo il check viene creata una prenotazione pending. Invia il JSON finale (stesso payload + customer_name) per confermarla.",
      labelCustomerName: "Nome cliente",
      btnCheck: "Controlla disponibilità",
      btnCreate: "Crea prenotazione",
      btnConfirm: "Conferma prenotazione",
      homeFooterHint:
        "Se un pulsante resta in caricamento: riavvia con flask --app app.main_flask_demo run --debug --no-reload (Windows) o controlla la URI del pooler Supabase.",
      pickTitle: "Aggiungi dettagli ristorante",
      pickLead: "Scegli un ristorante da modificare o creane uno nuovo. La bozza resta in questo browser finché non salvi sul database.",
      pickNew: "Nuovo ristorante",
      pickEmpty: "Nessun ristorante nel database. Creane uno nuovo.",
      pickLinkSettings: "Impostazioni iniziali",
      pickLinkCapacity: "Capacità",
      capacityTitle: "Capacità",
      capacityLead:
        "Scegli data e turno per vedere l’occupazione reale dal database: prenotazioni confermate, tavoli assegnati e capotavola per il servizio.",
      capacityBackToList: "← Torna alla lista ristoranti",
      capacityDate: "Data",
      capacityTurn: "Turno",
      capacityApply: "Applica",
      capacityOccupied: "Posti occupati (confermate + in attesa)",
      capacityFree: "Posti liberi (solo base)",
      capacityBaseTotal: "Posti base massimi (tutti i tavoli)",
      capacityPendingNote: "Prenotazioni in attesa (bloccano i tavoli; non compaiono nella tabella confermate sotto):",
      capacityConfirmedTitle: "Prenotazioni confermate",
      capacityColGuest: "Ospite",
      capacityColPhone: "Telefono",
      capacityColTime: "Ora",
      capacityColParty: "Coperti",
      capacityColTables: "Tavolo",
      capacityColHighchairs: "Seggioloni",
      capacityCopyAsText: "Copia come testo",
      capacityCopyAsImage: "Copia come immagine",
      capacityCopied: "Copiato negli appunti.",
      capacityImageCopied: "Immagine copiata negli appunti.",
      capacityEmptyConfirmed: "Nessuna prenotazione confermata per questo turno.",
      capacityMapTitle: "Occupazione",
      capacityMapDesc:
        "Le aree seguono l’ordine delle impostazioni; le tessere vanno a capo (min ~9rem). Nome ospite e diagramma sedie compaiono solo sui tavoli con prenotazione per questo turno (confermata o in attesa; in attesa = bordo tratteggiato). Tavoli liberi: solo testo, senza sedie (evita di sembrare tutti occupati).",
      capacityZoneOther: "Non in elenco",
      capacityNoTurns: "Nessun turno di servizio configurato per questo giorno della settimana.",
      capacityLoadError: "Impossibile caricare la capacità.",
      setupBack: "← Torna alla lista",
      setupTitle: "Impostazioni iniziali",
      setupLead:
        "Definisci aree, capacità, coppie unibili e regole globali qui sotto. La mappa 2D (come nel Reservation Lab) si aggiorna da tavoli e aree. Salva nel database quando sei pronto.",
      sectionMapTitle: "Mappa 2D",
      sectionMapDesc:
        "Vedi ogni tavolo nella sua zona. Il layout segue la griglia per area; i gruppi uniti da prenotazione compaiono in Capacità nell’app completa.",
      mapStaticTitle: "Mappa statica (solo layout — una tessera per tavolo)",
      mapZoneHint:
        "Le aree sono in strisce verticali; le tessere vanno a capo (min ~9rem). I colori seguono l’ordine delle aree.",
      mapNotInList: "Non in elenco (assegnalo nelle impostazioni)",
      sectionIdentity: "Identità ristorante",
      sectionAreas: "Aree",
      sectionRules: "Regole ristorante",
      sectionTables: "Tavoli",
      sectionMap: "Pianta locale",
      navSectionIdentity: "Identità ristorante",
      navSectionAreas: "Aree",
      navSectionRules: "Regole ristorante",
      navSectionTables: "Tavoli",
      navSection2dMap: "Mappa 2D",
      labelBusinessName: "Ragione sociale / business",
      labelRestaurantName: "Nome ristorante (visualizzato)",
      labelTimezone: "Fuso orario",
      labelStreet: "Via",
      labelCity: "Città",
      labelCountry: "Paese",
      labelEmailAddress: "Email",
      labelWhatsapp: "Whatsapp",
      btnAddArea: "+ Aggiungi area",
      btnAddTable: "+ Aggiungi tavolo",
      btnSaveDb: "Salva nel database",
      btnRemove: "Rimuovi",
      btnUp: "Su",
      btnDown: "Giù",
      toastSaved: "Salvato nel database.",
      toastError: "Salvataggio non riuscito.",
      mergeHint: "Unibile con (id tavolo, separati da virgola)",
    },
  };

  function getLang() {
    return localStorage.getItem("finalCodeLang") || "en";
  }

  function setLang(lang) {
    localStorage.setItem("finalCodeLang", lang);
    document.documentElement.lang = lang === "it" ? "it" : "en";
    apply();
    document.querySelectorAll(".lab-langpill, .lab-mLangBtn").forEach((btn) => {
      const on = btn.getAttribute("data-lang") === lang;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function t(key) {
    const lang = getLang();
    return (STR[lang] && STR[lang][key]) || STR.en[key] || key;
  }

  function apply() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      const k = el.getAttribute("data-i18n");
      if (k) el.textContent = t(k);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
      const k = el.getAttribute("data-i18n-placeholder");
      if (k) el.setAttribute("placeholder", t(k));
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    setLang(getLang());
    document.querySelectorAll(".lab-langpill, .lab-mLangBtn").forEach((btn) => {
      btn.addEventListener("click", () => setLang(btn.getAttribute("data-lang") || "en"));
    });
  });

  window.labI18n = { t, getLang, setLang, apply };
})();
