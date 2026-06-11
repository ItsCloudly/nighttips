/* WM26 – Vanilla-JS-SPA ohne Build-Step (SPEC 2):
   Live-Ticker (SSE), Pins + Push, Teams/Spieler-Lupen, News, Bonusfragen,
   Registrierung, Nutzerverwaltung, Team-Tabs im Spiel-Detail. */
"use strict";

const zustand = {
  nutzer: null,
  spiele: [],
  teams: [],
  wettbewerbe: [],
  pins: { spiel: new Set(), team: new Set() },
  zeitraum: "gesamt",
  runde: "",
  spieleTag: "alle",
  kalenderMonat: null,
  filterStatus: "alle",
  filterGruppe: "",
  filterTeam: "",
  teamSuche: "",
  newsTeam: "",
  newsTag: "",
  newsItems: [],
  teaserItems: [],
  onboardingSuche: "",
  turnierModus: "gruppen",
  lupeSpielId: null,
  eventSource: null,
  pushAktiv: false,
};

/* ---------- Hilfen ---------- */

function el(id) {
  return document.getElementById(id);
}

async function api(pfad, optionen = {}) {
  const antwort = await fetch(pfad, {
    headers: { "Content-Type": "application/json" },
    ...optionen,
  });
  if (antwort.status === 401) {
    zeigeLogin();
    throw new Error("Nicht angemeldet");
  }
  if (!antwort.ok) {
    let detail = `Fehler ${antwort.status}`;
    try {
      const daten = await antwort.json();
      if (daten.detail) detail = typeof daten.detail === "string" ? daten.detail : detail;
    } catch {
      /* Antwort ohne JSON-Körper */
    }
    throw new Error(detail);
  }
  if (antwort.status === 204) return null;
  return antwort.json();
}

function escapeHtml(text) {
  return String(text ?? "").replace(
    /[&<>"']/g,
    (zeichen) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[zeichen]
  );
}

// News-Links stammen aus externen RSS-Feeds (nicht vertrauenswürdig). Nur echte
// http(s)-Adressen als Linkziel zulassen — verhindert javascript:/data:-URIs.
function sichereUrl(url) {
  const wert = String(url ?? "").trim();
  return /^https?:\/\//i.test(wert) ? wert : "#";
}

function lokaleUhrzeit(isoUtc) {
  return new Date(isoUtc).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

function lokalerTag(isoUtc) {
  return new Date(isoUtc).toLocaleDateString("de-DE", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function istVorbei(spiel) {
  return spiel.status !== "geplant" || new Date(spiel.anstoss_utc) <= new Date();
}

function istHeute(isoUtc) {
  return new Date(isoUtc).toDateString() === new Date().toDateString();
}

let toastTimer = null;
function toast(text, istFehler = false) {
  const box = el("toast");
  box.textContent = text;
  box.classList.toggle("fehler", istFehler);
  box.classList.remove("mit-bild");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.hidden = true;
  }, 3200);
}

/* Toast mit Illustration (Erfolgsmomente) — bild ist ein fester Asset-Name */
function bildToast(bild, text) {
  const box = el("toast");
  box.innerHTML = `<img src="/illustrationen/${bild}.webp" alt="">${escapeHtml(text)}`;
  box.classList.remove("fehler");
  box.classList.add("mit-bild");
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.hidden = true;
    box.classList.remove("mit-bild");
  }, 3200);
}

/* Empty-State-Block: Asset + spielerischer Einzeiler */
function emptyStateHtml(bild, titel, text, klein = false) {
  return `<div class="karte empty-state${klein ? " klein" : ""}">
    <img src="/illustrationen/${bild}.webp" alt="" loading="lazy">
    <strong>${titel}</strong>
    <p class="hinweis">${text}</p>
  </div>`;
}

function fehlerAnzeigen(fehler) {
  if (fehler.message !== "Nicht angemeldet") {
    console.error(fehler);
    toast(fehler.message, true);
  }
}

/* ---------- Ansichten umschalten ---------- */

const ANSICHTEN = ["login", "onboarding", "heute", "spiele", "rangliste", "turnier", "teams", "news", "mehr"];
// Teams/News leben unter "Mehr" — der Tab bleibt dann aktiv markiert
const NAV_ZUORDNUNG = { teams: "mehr", news: "mehr" };

function zeigeAnsicht(name) {
  for (const ansicht of ANSICHTEN) {
    el(`view-${ansicht}`).hidden = ansicht !== name;
  }
  el("leiste").hidden = name === "login" || name === "onboarding";
  const navName = NAV_ZUORDNUNG[name] ?? name;
  for (const knopf of document.querySelectorAll("#leiste button")) {
    knopf.classList.toggle("aktiv", knopf.dataset.view === navName);
  }
  window.scrollTo(0, 0);
  if (name === "heute") heuteRendern().catch(fehlerAnzeigen);
  if (name === "rangliste") ranglisteLaden().catch(fehlerAnzeigen);
  if (name === "turnier") turnierRendern().catch(fehlerAnzeigen);
  if (name === "teams") teamsRendern();
  if (name === "news") newsLaden().catch(fehlerAnzeigen);
  if (name === "mehr") verwaltungLaden().catch(fehlerAnzeigen);
}

function zeigeLogin() {
  zustand.nutzer = null;
  if (zustand.eventSource) {
    zustand.eventSource.close();
    zustand.eventSource = null;
  }
  zeigeAnsicht("login");
}

/* ---------- Login ---------- */

async function loginAbsenden(ereignis) {
  ereignis.preventDefault();
  const fehler = el("loginFehler");
  fehler.hidden = true;
  try {
    zustand.nutzer = await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        anzeigename: el("loginName").value.trim(),
        pin: el("loginPin").value,
      }),
    });
    el("loginPin").value = "";
    await appStarten();
  } catch (fehlerObjekt) {
    fehler.textContent = fehlerObjekt.message;
    fehler.hidden = false;
  }
}

/* ---------- Live-Updates (SSE) ---------- */

function liveVerbinden() {
  if (zustand.eventSource) zustand.eventSource.close();
  const quelle = new EventSource("/api/stream");
  zustand.eventSource = quelle;

  const spielUpdate = (daten, flash) => {
    const spiel = zustand.spiele.find((s) => s.id === daten.id);
    if (!spiel) return;
    spiel.status = daten.status;
    spiel.tore_heim = daten.tore_heim;
    spiel.tore_gast = daten.tore_gast;
    spiel.tippbar = daten.status === "geplant" && new Date(daten.anstoss_utc) > new Date();
    if (!el("view-spiele").hidden) spieleRendern(flash ? daten.id : null);
    if (!el("view-heute").hidden) heuteRendern().catch(() => {});
    if (zustand.lupeSpielId === daten.id) lupeStandAktualisieren(daten, flash);
  };

  quelle.addEventListener("score", (e) => spielUpdate(JSON.parse(e.data), true));
  quelle.addEventListener("status", (e) => spielUpdate(JSON.parse(e.data), false));
  quelle.addEventListener("ereignis", (e) => {
    const daten = JSON.parse(e.data);
    if (zustand.lupeSpielId === daten.spiel_id) tickerEintragEinfuegen(daten);
  });
  quelle.onerror = () => {
    /* EventSource verbindet selbst neu (retry); kein Handlungsbedarf */
  };
}

/* ---------- Pins ---------- */

async function pinsLaden() {
  const pins = await api("/api/pins");
  zustand.pins.spiel = new Set(pins.filter((p) => p.typ === "spiel").map((p) => p.ref_id));
  zustand.pins.team = new Set(pins.filter((p) => p.typ === "team").map((p) => p.ref_id));
}

async function pinUmschalten(typ, refId) {
  const gesetzt = zustand.pins[typ].has(refId);
  try {
    await api(`/api/pins/${typ}/${refId}`, { method: gesetzt ? "DELETE" : "PUT" });
    if (gesetzt) zustand.pins[typ].delete(refId);
    else zustand.pins[typ].add(refId);
    if (!gesetzt && Notification?.permission === "default" && !zustand.pushAktiv) {
      toast("Tipp: Aktiviere Push unter „Mehr“ für Tor-Alarme 📣");
    }
  } catch (fehler) {
    fehlerAnzeigen(fehler);
  }
}

function pinKnopf(typ, refId) {
  const gesetzt = zustand.pins[typ].has(refId);
  return `<button class="pin-knopf${gesetzt ? " gepinnt" : ""}" data-pin-typ="${typ}"
    data-pin-ref="${refId}" aria-label="${gesetzt ? "Pin entfernen" : "Pinnen"}"
    title="${gesetzt ? "Pin entfernen" : "Pinnen"}">${gesetzt ? "★" : "☆"}</button>`;
}

/* ---------- Spiele ---------- */

async function spieleLaden() {
  [zustand.spiele, zustand.teams] = await Promise.all([api("/api/spiele"), api("/api/teams")]);
  await pinsLaden();
  filterFuellen();
  spieleRendern();
}

function filterFuellen() {
  const runden = [...new Set(zustand.spiele.map((spiel) => spiel.runde))];
  el("filterGruppe").innerHTML =
    '<option value="">Alle Runden</option>' +
    runden.map((runde) => `<option value="${escapeHtml(runde)}">${escapeHtml(runde)}</option>`).join("");
  el("filterGruppe").value = zustand.filterGruppe;
  const teamOptionen =
    '<option value="">Alle Teams</option>' +
    zustand.teams
      .map((team) => `<option value="${team.id}">${escapeHtml(team.name)}</option>`)
      .join("");
  el("filterTeam").innerHTML = teamOptionen;
  el("filterTeam").value = zustand.filterTeam;
  el("newsTeamFilter").innerHTML = teamOptionen;
  el("newsTeamFilter").value = zustand.newsTeam;

  const rundenWahl = el("ranglisteRunde");
  rundenWahl.innerHTML = runden
    .map((runde) => `<option value="${escapeHtml(runde)}">${escapeHtml(runde)}</option>`)
    .join("");
  if (zustand.runde) rundenWahl.value = zustand.runde;
}

function spielIstGepinnt(spiel) {
  return (
    zustand.pins.spiel.has(spiel.id) ||
    zustand.pins.team.has(spiel.heim?.id) ||
    zustand.pins.team.has(spiel.gast?.id)
  );
}

function lokalesDatum(isoUtc) {
  return new Date(isoUtc).toLocaleDateString("en-CA"); // YYYY-MM-DD, lokale Zone
}

function spielStatusGruppe(spiel) {
  if (spiel.status === "live" || spiel.status === "halbzeit") return "live";
  if (spiel.status === "beendet" || spiel.status === "abgesagt") return "beendet";
  return "anstehend";
}

function gefilterteSpiele() {
  return zustand.spiele.filter((spiel) => {
    if (zustand.spieleTag !== "alle" && lokalesDatum(spiel.anstoss_utc) !== zustand.spieleTag) {
      return false;
    }
    if (zustand.filterStatus === "gepinnt") {
      if (!spielIstGepinnt(spiel)) return false;
    } else if (zustand.filterStatus !== "alle" && spielStatusGruppe(spiel) !== zustand.filterStatus) {
      return false;
    }
    if (zustand.filterGruppe && spiel.runde !== zustand.filterGruppe) return false;
    if (zustand.filterTeam) {
      const teamId = Number(zustand.filterTeam);
      if (spiel.heim?.id !== teamId && spiel.gast?.id !== teamId) return false;
    }
    return true;
  });
}

/* Sprechende Tageslabels: Heute/Morgen/Gestern, sonst "Mi, 18.06." */
function tagLabel(datum) {
  const heute = new Date();
  const tagDiff = Math.round(
    (new Date(`${datum}T12:00:00`) - new Date(heute.toLocaleDateString("en-CA") + "T12:00:00")) / 864e5
  );
  if (tagDiff === 0) return "Heute";
  if (tagDiff === 1) return "Morgen";
  if (tagDiff === -1) return "Gestern";
  return new Date(`${datum}T12:00:00`).toLocaleDateString("de-DE", {
    weekday: "short",
    day: "2-digit",
    month: "2-digit",
  });
}

/* ----- Spieltag-Kalender (v0.1.1) -----
   Monatsraster statt Chip-Leiste: Tage mit Spielen sind wählbar (Punkte
   deuten die Spielanzahl an), ein erneuter Tap auf den gewählten Tag springt
   zurück auf "Alle Tage". */

const KALENDER_SVG =
  '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="6" width="16" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M4 10.5h16M9 4v4M15 4v4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

function spieleProTag() {
  const tage = new Map();
  for (const spiel of zustand.spiele) {
    const datum = lokalesDatum(spiel.anstoss_utc);
    tage.set(datum, (tage.get(datum) ?? 0) + 1);
  }
  return tage;
}

function kalenderMonatKlemmen(monat, tage) {
  const monate = [...new Set([...tage.keys()].map((datum) => datum.slice(0, 7)))].sort();
  if (!monate.length) return monat;
  if (monat < monate[0]) return monate[0];
  if (monat > monate[monate.length - 1]) return monate[monate.length - 1];
  return monat;
}

function kalenderRendern() {
  const tage = spieleProTag();
  const heuteDatum = new Date().toLocaleDateString("en-CA");
  zustand.kalenderMonat = kalenderMonatKlemmen(
    zustand.kalenderMonat ?? heuteDatum.slice(0, 7),
    tage
  );
  const monat = zustand.kalenderMonat;
  const gewaehlt = zustand.spieleTag;

  const anzahlText = (anzahl) => `${anzahl} ${anzahl === 1 ? "Spiel" : "Spiele"}`;
  const zusammenfassung =
    gewaehlt === "alle"
      ? `Alle Tage · ${anzahlText(zustand.spiele.length)}`
      : `${tagLabel(gewaehlt)} · ${anzahlText(tage.get(gewaehlt) ?? 0)}`;
  el("kalenderZusammenfassung").innerHTML =
    `<span class="kalender-icon" aria-hidden="true">${KALENDER_SVG}</span>` +
    `<span>${escapeHtml(zusammenfassung)}</span>`;

  const monate = [...new Set([...tage.keys()].map((datum) => datum.slice(0, 7)))].sort();
  el("kalenderMonat").textContent = new Date(`${monat}-01T12:00:00`).toLocaleDateString("de-DE", {
    month: "long",
    year: "numeric",
  });
  el("kalenderZurueck").disabled = !monate.length || monat <= monate[0];
  el("kalenderVor").disabled = !monate.length || monat >= monate[monate.length - 1];

  const erster = new Date(`${monat}-01T12:00:00`);
  const versatz = (erster.getDay() + 6) % 7; // Wochenstart Montag
  const tageImMonat = new Date(erster.getFullYear(), erster.getMonth() + 1, 0).getDate();
  const zellen = [];
  for (let leer = 0; leer < versatz; leer++) {
    zellen.push('<span class="kalender-tag leer" aria-hidden="true"></span>');
  }
  for (let tag = 1; tag <= tageImMonat; tag++) {
    const datum = `${monat}-${String(tag).padStart(2, "0")}`;
    const anzahl = tage.get(datum) ?? 0;
    const klassen = ["kalender-tag"];
    if (datum === heuteDatum) klassen.push("heute");
    if (datum === gewaehlt) klassen.push("gewaehlt");
    if (anzahl) {
      const punkte = "<i></i>".repeat(Math.min(anzahl, 3));
      zellen.push(`<button type="button" class="${klassen.join(" ")}" data-kalender-tag="${datum}"
        aria-pressed="${datum === gewaehlt}"
        aria-label="${tagLabel(datum)}, ${anzahlText(anzahl)}">
        ${tag}<span class="kalender-punkte" aria-hidden="true">${punkte}</span></button>`);
    } else {
      zellen.push(`<span class="${klassen.join(" ")} ohne">${tag}</span>`);
    }
  }
  el("kalenderTage").innerHTML = zellen.join("");
  el("kalenderAlle").hidden = gewaehlt === "alle";
}

function kalenderMonatWechseln(richtung) {
  const basis = new Date(`${zustand.kalenderMonat}-01T12:00:00`);
  basis.setMonth(basis.getMonth() + richtung);
  zustand.kalenderMonat = basis.toLocaleDateString("en-CA").slice(0, 7);
  kalenderRendern();
}

function duellTeamHtml(team) {
  const flagge = team?.flagge_url
    ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="" loading="lazy">`
    : '<span class="platzhalter-flagge"></span>';
  const name = team?.name
    ? `<span class="team-name">${escapeHtml(team.name)}</span>`
    : '<span class="team-name offen">Noch offen</span>';
  return `<div class="duell-team">${flagge}${name}</div>`;
}

function duellMitteHtml(spiel, flashId) {
  if (spiel.tippbar) {
    const eingabe = (seite, wert, team) => `<input class="tipp-eingabe" type="number" min="0"
      max="99" inputmode="numeric" data-spiel="${spiel.id}" data-seite="${seite}"
      value="${wert ?? ""}" aria-label="Tipp ${escapeHtml(team?.name ?? seite)}">`;
    return `<div class="tipp-paar">
        ${eingabe("heim", spiel.mein_tipp?.tipp_heim, spiel.heim)}
        <span class="tipp-doppelpunkt" aria-hidden="true">:</span>
        ${eingabe("gast", spiel.mein_tipp?.tipp_gast, spiel.gast)}
      </div>
      <button class="tipp-speichern klein primaer" data-spiel="${spiel.id}" hidden>Speichern</button>`;
  }
  if (spiel.tore_heim !== null || spiel.status !== "geplant") {
    const flash = flashId === spiel.id ? " flash" : "";
    return `<div class="duell-stand${flash}">${spiel.tore_heim ?? "–"} : ${spiel.tore_gast ?? "–"}</div>`;
  }
  return `<div class="duell-zeit">${lokaleUhrzeit(spiel.anstoss_utc)}</div>`;
}

function spielKarte(spiel, flashId) {
  const kopfTeile = [
    `${lokaleUhrzeit(spiel.anstoss_utc)} Uhr`,
    escapeHtml(spiel.runde),
    spiel.stadion ? escapeHtml(spiel.stadion) : null,
    spiel.hat_notiz
      ? '<span class="notiz-marker" title="Du hast eine Notiz zu diesem Spiel">✎</span>'
      : null,
  ].filter(Boolean);

  let badge = "";
  if (spiel.status === "live" || spiel.status === "halbzeit") {
    const label = spiel.status === "halbzeit" ? "HALBZEIT" : "LIVE";
    badge = `<span class="badge live"><span class="live-punkt"></span>${label}</span>`;
  } else if (spiel.status === "abgesagt") {
    badge = '<span class="badge">abgesagt</span>';
  } else if (spiel.tippbar && !spiel.mein_tipp) {
    const stunden = (new Date(spiel.anstoss_utc) - new Date()) / 36e5;
    if (stunden < 24) badge = '<span class="badge tipp-offen">noch tippen!</span>';
  }

  let fuss = "";
  if (spiel.mein_tipp && spiel.tippbar) {
    fuss = "<span>Tipp gespeichert ✓ — änderbar bis zum Anpfiff</span>";
  } else if (spiel.mein_tipp) {
    const punkte =
      spiel.mein_tipp.punkte !== null
        ? ` <span class="punkte-chip">+${spiel.mein_tipp.punkte}</span>`
        : "";
    fuss = `<span>Mein Tipp: <strong>${spiel.mein_tipp.tipp_heim}:${spiel.mein_tipp.tipp_gast}</strong>${punkte}</span>`;
  } else if (!spiel.tippbar) {
    fuss = "<span>Kein Tipp abgegeben</span>";
  }
  const detail =
    spiel.status !== "abgesagt"
      ? `<button class="detail-knopf klein" data-spiel="${spiel.id}">Details</button>`
      : "";

  const favorit = spielIstGepinnt(spiel) ? " favorit" : "";
  return `<article class="karte spiel${favorit}" data-spiel="${spiel.id}">
    <div class="spiel-kopf">
      <span class="links">${kopfTeile.join(" · ")}</span>
      <span class="links">${badge}${pinKnopf("spiel", spiel.id)}</span>
    </div>
    <div class="duell">
      ${duellTeamHtml(spiel.heim)}
      <div class="duell-mitte">${duellMitteHtml(spiel, flashId)}</div>
      ${duellTeamHtml(spiel.gast)}
    </div>
    <div class="spiel-fuss">${fuss}<span class="fuss-knoepfe">${detail}</span></div>
  </article>`;
}

let countdownTimer = null;

function countdownText(bisMs) {
  const sekunden = Math.max(0, Math.floor(bisMs / 1000));
  const h = Math.floor(sekunden / 3600);
  const m = Math.floor((sekunden % 3600) / 60);
  const s = sekunden % 60;
  if (h >= 48) return `${Math.floor(h / 24)} Tage`;
  const fuelle = (zahl) => String(zahl).padStart(2, "0");
  return `${fuelle(h)}:${fuelle(m)}:${fuelle(s)}`;
}

function countdownStarten() {
  clearInterval(countdownTimer);
  countdownTimer = setInterval(() => {
    const karte = el("heuteInhalt").querySelector("[data-anstoss]");
    if (!karte) {
      clearInterval(countdownTimer);
      return;
    }
    const rest = new Date(karte.dataset.anstoss) - new Date();
    const zeit = karte.querySelector(".hero-countdown");
    if (zeit) zeit.textContent = countdownText(rest);
    if (rest <= 0) {
      clearInterval(countdownTimer);
      spieleLaden()
        .then(() => heuteRendern())
        .catch(fehlerAnzeigen);
    }
  }, 1000);
}

/* ---------- Heute (Dashboard) ---------- */

function heroTeamHtml(team) {
  const flagge = team?.flagge_url
    ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="" loading="lazy">`
    : '<span class="platzhalter-flagge"></span>';
  const name = team?.name ?? "Noch offen";
  return `<div class="hero-team">${flagge}<span class="hero-team-name">${escapeHtml(name)}</span></div>`;
}

function heuteHeroHtml() {
  const live = zustand.spiele.find((s) => s.status === "live" || s.status === "halbzeit");
  const spiel =
    live ??
    zustand.spiele.find((s) => s.status === "geplant" && new Date(s.anstoss_utc) > new Date());
  if (!spiel) return "";
  const istLive = Boolean(live);
  const mitte = istLive
    ? `<div class="hero-stand">${spiel.tore_heim ?? 0} : ${spiel.tore_gast ?? 0}</div>
       <span class="badge live"><span class="live-punkt"></span>${
         spiel.status === "halbzeit" ? "HALBZEIT" : "LIVE"
       }</span>`
    : `<div class="hero-countdown">${countdownText(new Date(spiel.anstoss_utc) - new Date())}</div>
       <span class="hero-label">Nächster Anpfiff</span>`;
  return `<button class="heute-hero" data-spiel-lupe="${spiel.id}"
      ${istLive ? "" : `data-anstoss="${spiel.anstoss_utc}"`} aria-label="Zum Spiel">
    <div class="hero-meta">${escapeHtml(spiel.runde)}${
      spiel.stadion ? ` · ${escapeHtml(spiel.stadion)}` : ""
    }</div>
    <div class="hero-duell">
      ${heroTeamHtml(spiel.heim)}
      <div class="hero-mitte">${mitte}</div>
      ${heroTeamHtml(spiel.gast)}
    </div>
  </button>`;
}

function quickKachelnHtml() {
  const kacheln = [
    ["turnier", "Turnierbaum", '<path d="M4 5h5v4H4zM4 15h5v4H4zM15 10h5v4h-5zM9 7h3v5h3M9 17h3v-5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>'],
    ["rangliste", "Rangliste", '<path d="M6 4h12v3a4 4 0 0 1-4 4h-4A4 4 0 0 1 6 7Z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 11v4m-3 5h6m-3-5v5" stroke="currentColor" stroke-width="1.8"/>'],
    ["bonus", "Bonusfragen", '<rect x="5" y="5" width="14" height="14" rx="4" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M9.8 9.5a2.2 2.2 0 1 1 3 2c-.7.4-.8.9-.8 1.5m0 2.6v.1" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'],
    ["news", "News", '<rect x="4" y="5" width="16" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M7.5 9h9M7.5 12.5h9M7.5 16h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'],
  ];
  return `<div class="quick-kacheln">${kacheln
    .map(
      ([ziel, label, pfad]) => `<button class="quick-kachel" data-quick="${ziel}">
        <svg viewBox="0 0 24 24" aria-hidden="true">${pfad}</svg><span>${label}</span></button>`
    )
    .join("")}</div>`;
}

async function heuteRendern() {
  el("heuteGruss").textContent = `Moin, ${zustand.nutzer?.anzeigename ?? ""}!`;
  el("heuteDatum").textContent = new Date().toLocaleDateString("de-DE", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  const teile = [];
  const hero = heuteHeroHtml();
  // Ohne anstehendes Spiel (z. B. nach dem Finale): Stadion-Ruhebild als Bühne
  teile.push(
    hero ||
      `<div class="heute-hero stadion-ruhe">
        <img src="/illustrationen/stadium-hero.webp" alt="" loading="lazy">
        <div class="hero-label">Gerade kein Spiel — das Stadion ruht</div>
      </div>`
  );
  const offen = zustand.spiele.filter((s) => s.tippbar && !s.mein_tipp);
  if (offen.length) {
    teile.push(`<button class="karte heute-tipps" data-quick="tippen">
      <span class="heute-tipps-zahl">${offen.length}</span>
      <span class="heute-tipps-text"><strong>${
        offen.length === 1 ? "Offener Tipp" : "Offene Tipps"
      }</strong><br>
      <span class="hinweis-zeile">Jetzt tippen — bis zum Anpfiff änderbar</span></span>
      <span class="heute-tipps-pfeil" aria-hidden="true">›</span>
    </button>`);
  }
  teile.push(quickKachelnHtml());
  const heutige = zustand.spiele.filter(
    (s) => istHeute(s.anstoss_utc) && spielStatusGruppe(s) !== "live"
  );
  if (heutige.length) {
    teile.push('<h2 class="tages-titel">Heute</h2>');
    for (const spiel of heutige.slice(0, 6)) teile.push(spielKarte(spiel, null));
  }
  el("heuteInhalt").innerHTML = teile.join("");
  countdownStarten();
  newsTeaserLaden().catch(() => {});
}

async function newsTeaserLaden() {
  const items = await api("/api/news?limit=6");
  if (!items.length || el("view-heute").hidden) return;
  zustand.teaserItems = items;
  const karten = items
    .map(
      (item, index) => `<button class="teaser-karte" data-teaser-reader="${index}">
        <span class="teaser-quelle">${escapeHtml(item.feed_titel ?? "News")}</span>
        <span class="teaser-titel">${escapeHtml(item.titel)}</span>
      </button>`
    )
    .join("");
  el("heuteInhalt").insertAdjacentHTML(
    "beforeend",
    `<h2 class="tages-titel">News</h2><div class="teaser-leiste">${karten}</div>`
  );
}

function spieleRendern(flashId = null) {
  const liste = el("spieleListe");
  kalenderRendern();
  const spiele = gefilterteSpiele();
  const standardFilter =
    zustand.filterStatus === "alle" && !zustand.filterGruppe && !zustand.filterTeam;
  if (!spiele.length) {
    if (zustand.filterStatus === "live") {
      liste.innerHTML = emptyStateHtml(
        "empty-no-live",
        "Gerade kein Live-Spiel",
        "Das Flutlicht macht Pause — der nächste Anpfiff kommt bestimmt."
      );
    } else if (zustand.spieleTag !== "alle") {
      liste.innerHTML = emptyStateHtml(
        "empty-no-matches",
        "Spielfrei",
        "An diesem Tag ruht der Ball. Such dir einen anderen Tag aus!"
      );
    } else {
      liste.innerHTML = emptyStateHtml(
        "empty-no-matches",
        "Keine Spiele gefunden",
        "Für diesen Filter ist nichts dabei — probier eine andere Kombination."
      );
    }
    return;
  }
  const stuecke = [];
  // Gepinnte Sektion oben (SPEC 5.1), nur in der Standardansicht
  if (standardFilter && zustand.spieleTag === "alle") {
    const gepinnt = spiele.filter((s) => spielIstGepinnt(s) && !istVorbei(s));
    if (gepinnt.length) {
      stuecke.push('<h2 class="tages-titel">★ Gepinnt</h2>');
      for (const spiel of gepinnt) stuecke.push(spielKarte(spiel, flashId));
    }
  }
  if (zustand.spieleTag === "alle") {
    // Über alle Tage: Tages-Überschriften wie gehabt
    let letzterTag = "";
    for (const spiel of spiele) {
      const tag = lokalerTag(spiel.anstoss_utc);
      if (tag !== letzterTag) {
        stuecke.push(`<h2 class="tages-titel">${tag}</h2>`);
        letzterTag = tag;
      }
      stuecke.push(spielKarte(spiel, flashId));
    }
  } else {
    // Einzelner Tag: nach Gruppe/Runde gebündelt, Header einklappbar
    const runden = new Map();
    for (const spiel of spiele) {
      if (!runden.has(spiel.runde)) runden.set(spiel.runde, []);
      runden.get(spiel.runde).push(spiel);
    }
    for (const [runde, rundenSpiele] of runden) {
      stuecke.push(`<details class="runden-gruppe" open>
        <summary class="runden-kopf">${escapeHtml(runde)}
          <span class="runden-anzahl">${rundenSpiele.length}</span></summary>
        <div class="liste">${rundenSpiele.map((spiel) => spielKarte(spiel, flashId)).join("")}</div>
      </details>`);
    }
  }
  liste.innerHTML = stuecke.join("");
}

async function tippSpeichern(spielId, karte) {
  const heim = karte.querySelector('input[data-seite="heim"]');
  const gast = karte.querySelector('input[data-seite="gast"]');
  if (heim.value === "" || gast.value === "") {
    toast("Bitte beide Tore-Felder ausfüllen.", true);
    return;
  }
  try {
    await api("/api/tipps", {
      method: "POST",
      body: JSON.stringify({
        spiel_id: spielId,
        tipp_heim: Number(heim.value),
        tipp_gast: Number(gast.value),
      }),
    });
    bildToast("success-tipp", "Tipp gespeichert ✓");
    await spieleLaden();
    // Tipp-Plopp: die frisch gespeicherte Karte federt kurz
    document.querySelector(`.spiel[data-spiel="${spielId}"]`)?.classList.add("plopp");
  } catch (fehler) {
    fehlerAnzeigen(fehler);
    await spieleLaden();
  }
}

function spieleEreignisse() {
  el("spieleListe").addEventListener("input", (ereignis) => {
    const eingabe = ereignis.target.closest(".tipp-eingabe");
    if (!eingabe) return;
    const karte = eingabe.closest(".spiel");
    karte.querySelector(".tipp-speichern").hidden = false;
  });
  el("spieleListe").addEventListener("click", (ereignis) => {
    const speichern = ereignis.target.closest(".tipp-speichern");
    if (speichern) {
      tippSpeichern(Number(speichern.dataset.spiel), speichern.closest(".spiel"));
      return;
    }
    const pin = ereignis.target.closest(".pin-knopf");
    if (pin) {
      pinUmschalten(pin.dataset.pinTyp, Number(pin.dataset.pinRef)).then(() => spieleRendern());
      return;
    }
    const detail = ereignis.target.closest(".detail-knopf");
    if (detail) spielLupeOeffnen(Number(detail.dataset.spiel)).catch(fehlerAnzeigen);
  });
  for (const knopf of el("spieleFilterStatus").querySelectorAll("button")) {
    knopf.addEventListener("click", () => {
      zustand.filterStatus = knopf.dataset.status;
      for (const anderer of el("spieleFilterStatus").querySelectorAll("button")) {
        anderer.classList.toggle("aktiv", anderer === knopf);
        anderer.setAttribute("aria-selected", anderer === knopf ? "true" : "false");
      }
      spieleRendern();
    });
  }
  el("kalenderTage").addEventListener("click", (ereignis) => {
    const knopf = ereignis.target.closest("[data-kalender-tag]");
    if (!knopf) return;
    const datum = knopf.dataset.kalenderTag;
    // Toggle-Logik: gewählten Tag erneut antippen = zurück zu "Alle Tage"
    zustand.spieleTag = zustand.spieleTag === datum ? "alle" : datum;
    el("spieleKalender").open = false;
    spieleRendern();
  });
  el("kalenderZurueck").addEventListener("click", () => kalenderMonatWechseln(-1));
  el("kalenderVor").addEventListener("click", () => kalenderMonatWechseln(1));
  el("kalenderAlle").addEventListener("click", () => {
    zustand.spieleTag = "alle";
    el("spieleKalender").open = false;
    spieleRendern();
  });
  el("filterGruppe").addEventListener("change", (ereignis) => {
    zustand.filterGruppe = ereignis.target.value;
    spieleRendern();
  });
  el("filterTeam").addEventListener("change", (ereignis) => {
    zustand.filterTeam = ereignis.target.value;
    spieleRendern();
  });
}

function heuteEreignisse() {
  el("heuteInhalt").addEventListener("input", (ereignis) => {
    const eingabe = ereignis.target.closest(".tipp-eingabe");
    if (!eingabe) return;
    eingabe.closest(".spiel").querySelector(".tipp-speichern").hidden = false;
  });
  el("heuteInhalt").addEventListener("click", (ereignis) => {
    const speichern = ereignis.target.closest(".tipp-speichern");
    if (speichern) {
      tippSpeichern(Number(speichern.dataset.spiel), speichern.closest(".spiel")).then(() =>
        heuteRendern()
      );
      return;
    }
    const pin = ereignis.target.closest(".pin-knopf");
    if (pin) {
      pinUmschalten(pin.dataset.pinTyp, Number(pin.dataset.pinRef)).then(() => heuteRendern());
      return;
    }
    const lupe = ereignis.target.closest("[data-spiel-lupe]");
    if (lupe) {
      spielLupeOeffnen(Number(lupe.dataset.spielLupe)).catch(fehlerAnzeigen);
      return;
    }
    const detail = ereignis.target.closest(".detail-knopf");
    if (detail) {
      spielLupeOeffnen(Number(detail.dataset.spiel)).catch(fehlerAnzeigen);
      return;
    }
    const teaser = ereignis.target.closest("[data-teaser-reader]");
    if (teaser) {
      const item = zustand.teaserItems[Number(teaser.dataset.teaserReader)];
      if (item) newsReaderOeffnen(item);
      return;
    }
    const quick = ereignis.target.closest("[data-quick]");
    if (!quick) return;
    const ziel = quick.dataset.quick;
    if (ziel === "tippen") {
      zustand.spieleTag = "alle";
      zustand.filterStatus = "anstehend";
      for (const knopf of el("spieleFilterStatus").querySelectorAll("button")) {
        knopf.classList.toggle("aktiv", knopf.dataset.status === "anstehend");
      }
      zeigeAnsicht("spiele");
      spieleRendern();
    } else if (ziel === "bonus") {
      zeigeAnsicht("rangliste");
      setTimeout(() => el("bonusBereich").scrollIntoView({ behavior: "smooth" }), 350);
    } else {
      zeigeAnsicht(ziel);
    }
  });
}

/* ---------- Lupe (Overlay) ---------- */

function lupeOeffnen(html) {
  const lupe = el("lupe");
  lupe.classList.remove("schliesst");
  el("lupeInhalt").innerHTML = html;
  el("lupeInhalt").scrollTop = 0;
  lupe.hidden = false;
  document.body.style.overflow = "hidden";
}

function lupeSchliessen() {
  const lupe = el("lupe");
  if (lupe.hidden || lupe.classList.contains("schliesst")) return;
  zustand.lupeSpielId = null;
  clearInterval(lupeCountdownTimer);
  notizSofortSpeichern(); // ungespeicherte Notiz nicht verlieren
  // Erst die Schließ-Animation (Blatt gleitet nach unten), dann verstecken
  lupe.classList.add("schliesst");
  setTimeout(() => {
    lupe.classList.remove("schliesst");
    lupe.hidden = true;
    el("lupeInhalt").innerHTML = "";
    document.body.style.overflow = "";
  }, 230);
}

/* Bottom-Sheet am Griff nach unten ziehen, um es zu schließen (mobil) */
function lupeZiehenEinrichten() {
  const blatt = document.querySelector(".lupe-blatt");
  const griff = blatt.querySelector(".lupe-griff");
  let startY = null;
  griff.addEventListener("pointerdown", (ereignis) => {
    if (matchMedia("(min-width: 720px)").matches) return;
    startY = ereignis.clientY;
    blatt.style.transition = "none";
    griff.setPointerCapture(ereignis.pointerId);
  });
  griff.addEventListener("pointermove", (ereignis) => {
    if (startY === null) return;
    const delta = Math.max(0, ereignis.clientY - startY);
    blatt.style.transform = `translate(-50%, ${delta}px)`;
  });
  const loslassen = (ereignis) => {
    if (startY === null) return;
    const delta = Math.max(0, ereignis.clientY - startY);
    startY = null;
    blatt.style.transition = "transform 0.3s cubic-bezier(0.34, 1.45, 0.5, 1)";
    blatt.style.transform = "";
    if (delta > 110) lupeSchliessen();
  };
  griff.addEventListener("pointerup", loslassen);
  griff.addEventListener("pointercancel", loslassen);
}

function lupeEreignisse() {
  lupeZiehenEinrichten();
  el("lupe").addEventListener("click", (ereignis) => {
    if (ereignis.target.closest("[data-lupe-schliessen]")) {
      lupeSchliessen();
      return;
    }
    const readerAa = ereignis.target.closest("[data-reader-aa]");
    if (readerAa) {
      // Aa-Regler: Schriftgröße s -> m -> l rotieren
      const reader = el("lupeInhalt").querySelector(".reader");
      const index = READER_GROESSEN.indexOf(reader.dataset.groesse);
      reader.dataset.groesse = READER_GROESSEN[(index + 1) % READER_GROESSEN.length];
      return;
    }
    const spielLupe = ereignis.target.closest("[data-spiel-lupe]");
    if (spielLupe) {
      // Mini-Scroller: anderes Spiel in derselben Lupe öffnen
      spielLupeOeffnen(Number(spielLupe.dataset.spielLupe)).catch(fehlerAnzeigen);
      return;
    }
    const pillTab = ereignis.target.closest("[data-pill-tab]");
    if (pillTab) {
      for (const anderer of el("lupeInhalt").querySelectorAll(".pill-tab")) {
        anderer.classList.toggle("aktiv", anderer === pillTab);
        anderer.setAttribute("aria-selected", anderer === pillTab ? "true" : "false");
      }
      for (const panel of el("lupeInhalt").querySelectorAll(".lupe-panel")) {
        const zeigen = panel.dataset.panel === pillTab.dataset.pillTab;
        panel.hidden = !zeigen;
        if (zeigen) {
          // Crossfade + Lift beim Tab-Wechsel neu anstoßen
          panel.style.animation = "none";
          void panel.offsetWidth;
          panel.style.animation = "";
        }
      }
      return;
    }
    const teamTab = ereignis.target.closest("[data-team-tab]");
    if (teamTab) {
      for (const anderer of el("lupeInhalt").querySelectorAll(".team-tab")) {
        anderer.classList.toggle("aktiv", anderer === teamTab);
        anderer.setAttribute("aria-selected", anderer === teamTab ? "true" : "false");
      }
      teamTabLaden(Number(teamTab.dataset.teamTab)).catch(fehlerAnzeigen);
      return;
    }
    const wbKnopf = ereignis.target.closest("[data-wettbewerb]");
    if (wbKnopf) {
      wettbewerbGewaehlt(wbKnopf.dataset.wettbewerb);
      return;
    }
    const teamKnopf = ereignis.target.closest("[data-team-lupe]");
    if (teamKnopf) {
      teamLupeOeffnen(Number(teamKnopf.dataset.teamLupe)).catch(fehlerAnzeigen);
      return;
    }
    const spielerKnopf = ereignis.target.closest("[data-spieler-lupe]");
    if (spielerKnopf) {
      spielerLupeOeffnen(Number(spielerKnopf.dataset.spielerLupe)).catch(fehlerAnzeigen);
      return;
    }
    const pin = ereignis.target.closest(".pin-knopf");
    if (pin) {
      const typ = pin.dataset.pinTyp;
      const ref = Number(pin.dataset.pinRef);
      pinUmschalten(typ, ref).then(() => {
        const gesetzt = zustand.pins[typ].has(ref);
        pin.classList.toggle("gepinnt", gesetzt);
        pin.textContent = gesetzt ? "★" : "☆";
        spieleRendern();
      });
    }
  });
  document.addEventListener("keydown", (ereignis) => {
    if (ereignis.key === "Escape" && !el("lupe").hidden) lupeSchliessen();
  });
}

/* ---------- Spiel-Lupe ---------- */

const TICKER_SYMBOL = {
  tor: "⚽",
  eigentor: "⚽",
  elfmeter: "⚽",
  gelb: "🟨",
  gelbrot: "🟥",
  rot: "🟥",
  wechsel: "🔁",
  var: "📺",
  anpfiff: "▶",
  halbzeit: "⏸",
  abpfiff: "🏁",
  freitext: "·",
};

function analyseAlsHtml(markdown) {
  // Bewusst minimal: Überschriften, fett, Listen, Absätze — kein HTML aus den Daten.
  const zeilen = escapeHtml(markdown).split(/\r?\n/);
  const teile = [];
  let liste = false;
  for (const roh of zeilen) {
    const zeile = roh.trim();
    const fett = zeile.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    if (zeile.startsWith("- ") || zeile.startsWith("* ")) {
      if (!liste) {
        teile.push("<ul>");
        liste = true;
      }
      teile.push(`<li>${fett.slice(2)}</li>`);
      continue;
    }
    if (liste) {
      teile.push("</ul>");
      liste = false;
    }
    if (!zeile) continue;
    if (zeile.startsWith("#")) {
      teile.push(`<h4>${fett.replace(/^#+\s*/, "")}</h4>`);
    } else {
      teile.push(`<p>${fett}</p>`);
    }
  }
  if (liste) teile.push("</ul>");
  return teile.join("");
}

function tickerEintragHtml(eintrag, neu = false) {
  const minute = eintrag.minute !== null && eintrag.minute !== undefined ? `${eintrag.minute}’` : "";
  const symbol = TICKER_SYMBOL[eintrag.typ] ?? "·";
  const teile = [];
  if (eintrag.spieler_name) teile.push(`<strong>${escapeHtml(eintrag.spieler_name)}</strong>`);
  if (eintrag.typ === "wechsel" && eintrag.spieler2_name) {
    teile.push(`für ${escapeHtml(eintrag.spieler2_name)}`);
  }
  if (eintrag.text) teile.push(escapeHtml(eintrag.text));
  if (!teile.length) teile.push(escapeHtml(eintrag.typ));
  const quelle = eintrag.quelle === "admin" ? ' <span class="quelle-admin">· manuell</span>' : "";
  return `<div class="ticker-eintrag${neu ? " neu" : ""}" data-ereignis="${eintrag.id}">
    <span class="ticker-minute">${minute}</span>
    <span class="ticker-symbol">${symbol}</span>
    <span class="ticker-text">${teile.join(" ")}${quelle}</span>
  </div>`;
}

function tickerEintragEinfuegen(eintrag) {
  const ticker = el("lupeInhalt").querySelector(".ticker");
  if (!ticker) return;
  const leer = ticker.querySelector(".hinweis");
  if (leer) leer.remove();
  ticker.insertAdjacentHTML("afterbegin", tickerEintragHtml(eintrag, true));
}

function lupeStandAktualisieren(daten, flash) {
  const stand = el("lupeInhalt").querySelector(".lupe-stand");
  if (!stand) return;
  const rollen = stand.querySelectorAll("[data-odometer]");
  const klein =
    daten.tore_heim !== null && daten.tore_heim <= 9 && daten.tore_gast !== null && daten.tore_gast <= 9;
  if (rollen.length === 2 && klein) {
    // Odometer: die Torzahlen rollen vertikal auf den neuen Wert
    rollen[0].style.transform = `translateY(-${daten.tore_heim}em)`;
    rollen[1].style.transform = `translateY(-${daten.tore_gast}em)`;
  } else {
    stand.textContent = `${daten.tore_heim ?? "–"} : ${daten.tore_gast ?? "–"}`;
  }
  if (flash) {
    stand.classList.remove("flash");
    void stand.offsetWidth; // Animation neu starten
    stand.classList.add("flash");
  }
}

function lupeTeamHtml(team) {
  if (!team) return '<div class="lupe-team"><span class="name offen">Noch offen</span></div>';
  const flagge = team.flagge_url
    ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="">`
    : '<span class="platzhalter-flagge"></span>';
  return `<div class="lupe-team" role="button" data-team-lupe="${team.id}">
    ${flagge}<span class="name">${escapeHtml(team.name)}</span></div>`;
}

/* Hauptfarbe je Nation (Trikot-/Flaggenfarbe) für den Hero-Verlauf.
   Wird per color-mix stark auf die dunkle Fläche gedimmt; fehlt ein Code,
   springt das Akzent-Cyan als Fallback ein. */
const TEAM_FARBEN = {
  ALG: "#1c8a44", ARG: "#6cace4", AUS: "#ffcd00", AUT: "#ef3340", BEL: "#e30613",
  BIH: "#002f6c", BRA: "#ffdf00", CAN: "#d80621", CIV: "#ff8200", COD: "#007fff",
  COL: "#fcd116", CPV: "#003da5", CRO: "#ed1c24", CUR: "#002b7f", CZE: "#d7141a",
  ECU: "#ffdd00", EGY: "#ce1126", ENG: "#e8e8e8", ESP: "#aa151b", FRA: "#0055a4",
  GER: "#e8e8e8", GHA: "#ce1126", HAI: "#00209f", IRN: "#239f40", IRQ: "#ce1126",
  JOR: "#c8102e", JPN: "#002fa7", KOR: "#cd2e3a", KSA: "#006c35", MAR: "#c1272d",
  MEX: "#006847", NED: "#ff6f00", NOR: "#ba0c2f", NZL: "#e8e8e8", PAN: "#da121a",
  PAR: "#d52b1e", POR: "#da291c", QAT: "#8a1538", RSA: "#007749", SCO: "#003087",
  SEN: "#00853f", SUI: "#da291c", SWE: "#ffcd00", TUN: "#e70013", TUR: "#e30a17",
  URY: "#7bafd4", USA: "#002868", UZB: "#0099b5",
};

function lupePhase(detail) {
  if (detail.status === "live" || detail.status === "halbzeit") return "live";
  if (detail.status === "beendet") return "nach";
  return "vor";
}

function odometerZiffer(wert) {
  if (wert === null || wert === undefined) return "–";
  if (wert > 9) return String(wert);
  const ziffern = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((z) => `<span>${z}</span>`).join("");
  return `<span class="odometer"><span class="odometer-rolle" data-odometer
    style="transform: translateY(-${wert}em)">${ziffern}</span></span>`;
}

function lupeHeroHtml(detail, phase) {
  const heimFarbe = TEAM_FARBEN[detail.heim?.fifa_code];
  const gastFarbe = TEAM_FARBEN[detail.gast?.fifa_code];
  const verlauf = `linear-gradient(120deg,
    color-mix(in srgb, ${heimFarbe ?? "#46c8ff"} 24%, var(--ns-surface-1)),
    var(--ns-surface-1) 44% 56%,
    color-mix(in srgb, ${gastFarbe ?? "#46c8ff"} 24%, var(--ns-surface-1)))`;
  let mitte = "";
  if (phase === "vor") {
    mitte = `<div class="lupe-stand" data-lupe-anstoss="${detail.anstoss_utc}">
        ${countdownText(new Date(detail.anstoss_utc) - new Date())}</div>
      <span class="hero-label">Anpfiff ${lokaleUhrzeit(detail.anstoss_utc)} Uhr</span>`;
  } else if (phase === "live") {
    mitte = `<div class="lupe-stand">${odometerZiffer(detail.tore_heim)}<span class="stand-punkt">:</span>${odometerZiffer(detail.tore_gast)}</div>
      <span class="badge live"><span class="live-punkt"></span>${
        detail.status === "halbzeit" ? "HALBZEIT" : "LIVE"
      }</span>`;
  } else if (detail.status === "abgesagt") {
    mitte = `<div class="lupe-stand">–</div><span class="badge">Abgesagt</span>`;
  } else {
    const zusatz =
      detail.ergebnis_nach === "elfmeterschiessen"
        ? " n. E."
        : detail.ergebnis_nach === "120"
          ? " n. V."
          : "";
    mitte = `<div class="lupe-stand">${detail.tore_heim ?? "–"} : ${detail.tore_gast ?? "–"}</div>
      <span class="badge">Endstand${zusatz}</span>`;
  }
  return `<header class="lupe-hero" style="background: ${verlauf}">
    <div class="hero-meta">${escapeHtml(detail.runde)}${
      detail.stadion ? ` · ${escapeHtml(detail.stadion)}` : ""
    } · ${lokalerTag(detail.anstoss_utc)} ${pinKnopf("spiel", detail.id)}</div>
    <div class="lupe-duell">
      ${lupeTeamHtml(detail.heim)}
      <div class="hero-mitte">${mitte}</div>
      ${lupeTeamHtml(detail.gast)}
    </div>
  </header>`;
}

function punkteBannerHtml(detail) {
  if (!detail.mein_tipp) {
    return '<div class="punkte-banner leer">Kein Tipp abgegeben</div>';
  }
  const punkte = detail.mein_tipp.punkte;
  if (punkte === null || punkte === undefined) return "";
  const klasse = punkte >= 4 ? " gold" : punkte > 0 ? "" : " leer";
  const text = punkte >= 4 ? "Volltreffer!" : punkte > 0 ? "Punkte geholt" : "Diesmal daneben";
  const bild = punkte >= 4 ? '<img src="/illustrationen/volltreffer.webp" alt="">' : "";
  return `<div class="punkte-banner${klasse}">
    ${bild}<span>${text}</span><strong>Du: +${punkte}</strong>
    <span class="rang-detail">(Tipp ${detail.mein_tipp.tipp_heim}:${detail.mein_tipp.tipp_gast})</span>
  </div>`;
}

function lupeScrollerHtml(detail) {
  const andere = zustand.spiele.filter((s) => s.id !== detail.id && istHeute(s.anstoss_utc));
  if (!andere.length) return "";
  return `<div class="lupe-scroller" aria-label="Andere Spiele heute">${andere
    .map((s) => {
      const live = s.status === "live" || s.status === "halbzeit";
      const stand =
        s.tore_heim !== null && s.status !== "geplant"
          ? `${s.tore_heim}:${s.tore_gast}`
          : lokaleUhrzeit(s.anstoss_utc);
      return `<button class="scroller-spiel" data-spiel-lupe="${s.id}">
        ${live ? '<span class="live-punkt"></span>' : ""}
        ${escapeHtml(s.heim?.fifa_code ?? "?")} ${stand} ${escapeHtml(s.gast?.fifa_code ?? "?")}
      </button>`;
    })
    .join("")}</div>`;
}

function pillTabsHtml(aktiv) {
  const tabs = [
    ["tipps", "Tipps"],
    ["ticker", "Ticker"],
    ["teams", "Teams"],
    ["statistik", "Statistik"],
  ];
  return `<div class="pill-tabs" role="tablist" aria-label="Bereich wählen">${tabs
    .map(
      ([id, label]) => `<button class="pill-tab${id === aktiv ? " aktiv" : ""}"
        data-pill-tab="${id}" role="tab" aria-selected="${id === aktiv}">${label}</button>`
    )
    .join("")}</div>`;
}

function ringTrioHtml(detail) {
  const verteilung = detail.tipp_verteilung;
  if (!verteilung || !verteilung.gesamt) return "";
  const prozent = (anzahl) => Math.round((anzahl / verteilung.gesamt) * 100);
  const ring = (klasse, wert, label) => `<div class="ring">
    <svg viewBox="0 0 36 36" aria-hidden="true">
      <circle class="ring-grund" cx="18" cy="18" r="15.9"/>
      <circle class="ring-wert ${klasse}" cx="18" cy="18" r="15.9" pathLength="100"
        style="stroke-dasharray: ${wert} ${100 - wert}"/>
    </svg>
    <span class="ring-prozent">${wert}%</span>
    <span class="ring-abstand"></span>
    <span class="ring-label">${escapeHtml(label)}</span>
  </div>`;
  return `<section class="lupe-abschnitt"><h3>So tippt die Runde
      <span class="rang-detail">· ${verteilung.gesamt} ${verteilung.gesamt === 1 ? "Tipp" : "Tipps"}</span></h3>
    <div class="ring-trio">
      ${ring("heim", prozent(verteilung.heim), `Sieg ${detail.heim?.fifa_code ?? "Heim"}`)}
      ${ring("remis", prozent(verteilung.remis), "Remis")}
      ${ring("gast", prozent(verteilung.gast), `Sieg ${detail.gast?.fifa_code ?? "Gast"}`)}
    </div>
  </section>`;
}

function formDuellHtml(detail) {
  if (!detail.form || (!detail.form.heim.length && !detail.form.gast.length)) return "";
  const kette = (folge) =>
    folge.length
      ? `<span class="formkette">${folge
          .map((e) => `<span class="form-punkt ${e.toLowerCase()}">${e}</span>`)
          .join("")}</span>`
      : '<span class="rang-detail">noch keine Spiele</span>';
  return `<section class="lupe-abschnitt"><h3>Form im Turnier
      <span class="rang-detail">· neueste zuerst</span></h3>
    <div class="form-duell">
      <span>${escapeHtml(detail.heim?.name ?? "")}</span>${kette(detail.form.heim)}
    </div>
    <div class="form-duell">
      <span>${escapeHtml(detail.gast?.name ?? "")}</span>${kette(detail.form.gast)}
    </div>
  </section>`;
}

function statPaarHtml(titel, links, rechts) {
  const summe = links + rechts;
  const anteilLinks = summe ? Math.round((links / summe) * 100) : 0;
  const anteilRechts = summe ? 100 - anteilLinks : 0;
  return `<div class="stat-paar">
    <div class="stat-titel">${escapeHtml(titel)}</div>
    <div class="stat-balken">
      <span class="stat-wert">${links}</span>
      <span class="stat-spur links"><span class="stat-fuellung" style="width:${anteilLinks}%"></span></span>
      <span class="stat-spur rechts"><span class="stat-fuellung" style="width:${anteilRechts}%"></span></span>
      <span class="stat-wert">${rechts}</span>
    </div>
  </div>`;
}

function analyseSektionHtml(detail, typ, titel) {
  const analyse = detail.analysen?.[typ];
  if (!analyse) return "";
  const teile = [
    `<section class="lupe-abschnitt ki-sektion"><h3>
    <img src="/illustrationen/ki-chip.webp" alt="">${titel}
    <span class="rang-detail">· ${escapeHtml(analyse.agent_name)} · v${analyse.version}</span></h3>`,
  ];
  let struktur = null;
  try {
    struktur = analyse.struktur_json ? JSON.parse(analyse.struktur_json) : null;
  } catch {
    /* defekte struktur_json ignorieren, Markdown reicht */
  }
  const wkt = struktur?.wahrscheinlichkeiten;
  if (wkt && typ === "prognose") {
    const heim = Math.round((wkt.heim ?? 0) * 100);
    const remis = Math.round((wkt.remis ?? 0) * 100);
    const gast = Math.round((wkt.gast ?? 0) * 100);
    teile.push(`<div class="prognose-balken" role="img"
        aria-label="Heimsieg ${heim} Prozent, Remis ${remis} Prozent, Auswärtssieg ${gast} Prozent">
      <span class="heim" style="flex-basis:${heim}%">${heim}%</span>
      <span class="remis" style="flex-basis:${remis}%">${remis}%</span>
      <span class="gast" style="flex-basis:${gast}%">${gast}%</span>
    </div>`);
  }
  teile.push(`<div class="analyse-text">${analyseAlsHtml(analyse.inhalt_markdown)}</div></section>`);
  return teile.join("");
}

function tippZeilenHtml(detail) {
  if (!detail.tipps.length) {
    return emptyStateHtml(
      "empty-no-tipps",
      "Noch keine Tipps",
      "Die Glaskugel ist noch beschlagen — fremde Tipps erscheinen ab Anpfiff.",
      true
    );
  }
  return detail.tipps
    .map((tipp) => {
      const ki = tipp.rolle === "ki" ? '<span class="ki">KI</span>' : "";
      const eigene = tipp.nutzer_id === zustand.nutzer?.id;
      const punkte =
        tipp.punkte !== null ? ` <span class="punkte-chip">+${tipp.punkte}</span>` : "";
      return `<div class="tipp-zeile${eigene ? " hervorgehoben" : ""}">
        <span>${escapeHtml(tipp.anzeigename)}${ki}${eigene ? " <span class='rang-detail'>(du)</span>" : ""}</span>
        <span><strong>${tipp.tipp_heim}:${tipp.tipp_gast}</strong>${punkte}</span></div>`;
    })
    .join("");
}

function panelTippsHtml(detail, phase) {
  const teile = [];
  if (detail.tippbar) {
    const eingabe = (seite, wert, team) => `<input class="tipp-eingabe" type="number" min="0"
      max="99" inputmode="numeric" data-spiel="${detail.id}" data-seite="${seite}"
      value="${wert ?? ""}" aria-label="Tipp ${escapeHtml(team?.name ?? seite)}">`;
    teile.push(`<section class="lupe-abschnitt lupe-tipp-eingabe" data-lupe-tipp="${detail.id}">
      <h3>Dein Tipp</h3>
      <div class="tipp-paar zentriert">
        ${eingabe("heim", detail.mein_tipp?.tipp_heim, detail.heim)}
        <span class="tipp-doppelpunkt" aria-hidden="true">:</span>
        ${eingabe("gast", detail.mein_tipp?.tipp_gast, detail.gast)}
      </div>
      <button class="lupe-tipp-speichern primaer breit" data-spiel="${detail.id}" hidden>
        Tipp speichern</button>
      <p class="hinweis zentriert">${
        detail.mein_tipp ? "Gespeichert ✓ — änderbar bis zum Anpfiff" : "Bis zum Anpfiff änderbar"
      }</p>
    </section>`);
  }
  teile.push(ringTrioHtml(detail));
  teile.push(quotenSektionHtml(detail, phase));
  teile.push(notizSektionHtml(detail));
  teile.push(analyseSektionHtml(detail, "prognose", "KI-Prognose"));
  teile.push(`<section class="lupe-abschnitt"><h3>Tipps der Runde</h3>${tippZeilenHtml(detail)}</section>`);
  return teile.join("");
}

/* Buchmacher-Quote (v0.1.1): 1X2-Dezimalquoten + daraus errechnete implizite
   Wahrscheinlichkeiten (1/Quote, normalisiert) — nur vor dem Anpfiff. */
function quotenSektionHtml(detail, phase) {
  const quote = detail.quote;
  if (!quote || phase !== "vor") return "";
  // Defensive: kaputte Werte (0/negativ/NaN) lieber gar nicht zeigen
  if ([quote.heim, quote.remis, quote.gast].some((wert) => !Number.isFinite(wert) || wert < 1)) {
    return "";
  }
  const implizit = (wert) => 1 / wert;
  const summe = implizit(quote.heim) + implizit(quote.remis) + implizit(quote.gast);
  const eintraege = [
    ["1", detail.heim?.fifa_code ?? "Heim", quote.heim],
    ["X", "Remis", quote.remis],
    ["2", detail.gast?.fifa_code ?? "Gast", quote.gast],
  ];
  return `<section class="lupe-abschnitt"><h3>Quoten
      <span class="rang-detail">· ${escapeHtml(quote.anbieter)} · Stand ${lokaleUhrzeit(quote.abruf_utc)} Uhr</span></h3>
    <div class="quoten-zeile">${eintraege
      .map(
        ([kuerzel, label, wert]) => `<span class="quoten-pille">
        <span class="quoten-label">${kuerzel} · ${escapeHtml(String(label))}</span>
        <strong>${Number(wert).toFixed(2)}</strong>
        <span class="quoten-prozent">${Math.round((implizit(wert) / summe) * 100)} %</span>
      </span>`
      )
      .join("")}</div>
    <p class="hinweis">Buchmacher-Einschätzung — nur zur Orientierung.</p>
  </section>`;
}

/* Private Notiz zum Spiel: zugeklappt eine Zeile, aufgeklappt Textarea mit
   Auto-Save (debounced). Leeren löscht die Notiz serverseitig. */
function notizSektionHtml(detail) {
  const notiz = detail.notiz;
  const stand = notiz
    ? `Gespeichert · ${lokalerTag(notiz.geaendert_utc)}, ${lokaleUhrzeit(notiz.geaendert_utc)} Uhr`
    : "";
  return `<section class="lupe-abschnitt">
    <details class="notiz-bereich" data-notiz-bereich="${detail.id}"${notiz ? " open" : ""}>
      <summary class="notiz-kopf">Meine Notizen
        <span class="neben">privat — sieht nur du</span></summary>
      <textarea class="notiz-text" maxlength="2000" rows="4"
        aria-label="Private Notiz zu diesem Spiel"
        placeholder="Gedanken zum Spiel, Bauchgefühl, Merkzettel …">${escapeHtml(notiz?.text ?? "")}</textarea>
      <p class="notiz-status hinweis" data-notiz-status aria-live="polite">${stand}</p>
    </details>
  </section>`;
}

let notizTimer = null;
let notizAusstehend = null; // wartendes Speichern, das beim Lupe-Wechsel sofort laufen muss

/* Wartet eine Notiz noch auf den Debounce, sofort speichern (Lupe-Schließen,
   Spiel-Wechsel über den Mini-Scroller) — Getipptes geht nie verloren. */
function notizSofortSpeichern() {
  clearTimeout(notizTimer);
  if (notizAusstehend) {
    const speichern = notizAusstehend;
    notizAusstehend = null;
    speichern();
  }
}

function notizEreignisse(bereich, spielId) {
  notizSofortSpeichern();
  const feld = bereich.querySelector(".notiz-text");
  const status = bereich.querySelector("[data-notiz-status]");
  const speichern = async () => {
    notizAusstehend = null;
    const text = feld.value.trim();
    try {
      if (!text) {
        await api(`/api/notizen/${spielId}`, { method: "DELETE" });
        status.textContent = "Notiz gelöscht";
      } else {
        const antwort = await api(`/api/notizen/${spielId}`, {
          method: "PUT",
          body: JSON.stringify({ text }),
        });
        status.textContent = `Gespeichert ✓ · ${lokaleUhrzeit(antwort.geaendert_utc)} Uhr`;
      }
      const spiel = zustand.spiele.find((eintrag) => eintrag.id === spielId);
      if (spiel && spiel.hat_notiz !== !!text) {
        spiel.hat_notiz = !!text;
        spieleRendern(); // ✎-Marker in der Liste sofort nachführen
      }
    } catch {
      status.textContent = "Speichern fehlgeschlagen — Verbindung prüfen";
    }
  };
  feld.addEventListener("input", () => {
    status.textContent = "Speichert …";
    clearTimeout(notizTimer);
    notizAusstehend = speichern;
    notizTimer = setTimeout(notizSofortSpeichern, 800);
  });
}

function panelTickerHtml(detail) {
  const teile = ['<section class="lupe-abschnitt"><h3>Ticker</h3><div class="ticker">'];
  if (detail.ereignisse.length) {
    for (const eintrag of detail.ereignisse) teile.push(tickerEintragHtml(eintrag));
  } else {
    teile.push(
      `<p class="hinweis">${
        lupePhase(detail) === "vor"
          ? "Der Ticker startet mit dem Anpfiff."
          : "Keine Ticker-Einträge zu diesem Spiel."
      }</p>`
    );
  }
  teile.push("</div></section>");
  if (zustand.nutzer?.rolle === "admin") {
    teile.push(`<section class="lupe-abschnitt"><h3>Ticker-Eintrag nachtragen (Admin)</h3>
      <form class="inline-formular" data-ereignis-formular="${detail.id}">
        <select name="typ" aria-label="Ereignistyp">
          ${["tor", "eigentor", "elfmeter", "gelb", "gelbrot", "rot", "wechsel", "var", "freitext"]
            .map((typ) => `<option value="${typ}">${typ}</option>`)
            .join("")}
        </select>
        <input name="minute" type="number" min="0" max="150" placeholder="Min." style="max-width:90px">
        <input name="text" placeholder="Text, z. B. Torschütze" maxlength="500">
        <button type="submit" class="primaer klein">Eintragen</button>
      </form></section>`);
  }
  return teile.join("");
}

function panelTeamsHtml(detail) {
  const teile = [formDuellHtml(detail)];
  if (detail.heim && detail.gast) {
    const tabKnopf = (team, aktiv) => `<button class="team-tab${aktiv ? " aktiv" : ""}"
        data-team-tab="${team.id}" role="tab" aria-selected="${aktiv ? "true" : "false"}">
        ${team.flagge_url ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="">` : ""}
        <span>${escapeHtml(team.name)}</span></button>`;
    teile.push(`<section class="lupe-abschnitt"><h3>Kader &amp; Trainer</h3>
      <div class="team-tabs" role="tablist" aria-label="Team wählen">
        ${tabKnopf(detail.heim, true)}${tabKnopf(detail.gast, false)}
      </div>
      <div class="team-tab-inhalt" data-team-inhalt></div>
    </section>`);
  }
  if (detail.tabelle?.length) {
    const zeilen = detail.tabelle
      .map((zeile) => {
        const beteiligt = zeile.team_id === detail.heim?.id || zeile.team_id === detail.gast?.id;
        const diff = zeile.tordifferenz > 0 ? `+${zeile.tordifferenz}` : `${zeile.tordifferenz}`;
        return `<div class="tipp-zeile${beteiligt ? " hervorgehoben" : ""}">
        <span>${zeile.platz}. ${escapeHtml(zeile.name)}
          <span class="rang-detail">${zeile.spiele} Sp. · ${diff}</span></span>
        <span>${zeile.punkte} P.</span></div>`;
      })
      .join("");
    teile.push(`<section class="lupe-abschnitt"><h3>Tabelle ${escapeHtml(detail.runde)}</h3>${zeilen}</section>`);
  }
  const duelle = (detail.vergleiche ?? [])
    .map((duell) => {
      const jahr = duell.datum_utc ? new Date(duell.datum_utc).getFullYear() : "";
      const ergebnis =
        duell.tore_heim !== null && duell.tore_gast !== null
          ? `${duell.tore_heim}:${duell.tore_gast}`
          : "–";
      return `<div class="tipp-zeile">
      <span>${escapeHtml(duell.heim_name)} – ${escapeHtml(duell.gast_name)}
        <span class="rang-detail">${jahr}</span></span><span>${ergebnis}</span></div>`;
    })
    .join("");
  if (duelle) {
    teile.push(`<section class="lupe-abschnitt"><h3>Direkter Vergleich</h3>${duelle}</section>`);
  }
  return teile.join("");
}

function panelStatistikHtml(detail, phase) {
  const teile = [];
  if (phase === "nach") {
    const gewertet = detail.tipps
      // Versteckte Konten (rangliste_sichtbar=0) tauchen in Wertungs-Ansichten
      // nicht auf — in der Tippliste selbst bleiben sie sichtbar.
      .filter((tipp) => tipp.punkte !== null && tipp.punkte > 0 && tipp.rangliste_sichtbar !== 0)
      .sort((a, b) => b.punkte - a.punkte)
      .slice(0, 3);
    if (gewertet.length) {
      teile.push(`<section class="lupe-abschnitt"><h3>Top-Tipper des Spiels</h3>
        <div class="tipper-karten">${gewertet
          .map(
            (tipp) => `<div class="tipper-karte">
          <span class="tipper-avatar">${escapeHtml(tipp.anzeigename.slice(0, 1).toUpperCase())}</span>
          <span class="tipper-name">${escapeHtml(tipp.anzeigename)}${
            tipp.rolle === "ki" ? ' <span class="ki">KI</span>' : ""
          }</span>
          <span class="tipper-punkte">+${tipp.punkte}</span>
          <span class="tipper-tipp">Tipp ${tipp.tipp_heim}:${tipp.tipp_gast}</span>
        </div>`
          )
          .join("")}</div></section>`);
    }
  }
  const statTeile = [];
  const verteilung = detail.tipp_verteilung;
  if (verteilung?.gesamt) {
    statTeile.push(statPaarHtml("Tipps auf Sieg", verteilung.heim, verteilung.gast));
  }
  if (detail.bilanz && detail.bilanz.anzahl > 0) {
    statTeile.push(statPaarHtml("Siege im direkten Vergleich", detail.bilanz.heim_siege, detail.bilanz.gast_siege));
  }
  if (detail.form && (detail.form.heim.length || detail.form.gast.length)) {
    const siege = (kette) => kette.filter((e) => e === "S").length;
    statTeile.push(statPaarHtml("Siege im Turnier", siege(detail.form.heim), siege(detail.form.gast)));
  }
  if (statTeile.length) {
    teile.push(`<section class="lupe-abschnitt">
      <h3>${escapeHtml(detail.heim?.fifa_code ?? "Heim")} <span class="rang-detail">vs.</span> ${escapeHtml(detail.gast?.fifa_code ?? "Gast")}</h3>
      ${statTeile.join("")}</section>`);
  }
  teile.push(analyseSektionHtml(detail, "nachanalyse", "KI-Nachanalyse"));
  teile.push('<div data-spiel-news></div>');
  if (!statTeile.length && phase === "vor") {
    teile.push('<p class="hinweis">Mehr Zahlen gibt es, sobald getippt und gespielt wird.</p>');
  }
  return teile.join("");
}

let lupeCountdownTimer = null;

function lupeCountdownStarten(spielId) {
  clearInterval(lupeCountdownTimer);
  lupeCountdownTimer = setInterval(() => {
    const stand = el("lupeInhalt").querySelector("[data-lupe-anstoss]");
    if (!stand) {
      clearInterval(lupeCountdownTimer);
      return;
    }
    const rest = new Date(stand.dataset.lupeAnstoss) - new Date();
    stand.textContent = countdownText(rest);
    if (rest <= 0) {
      clearInterval(lupeCountdownTimer);
      spielLupeOeffnen(spielId).catch(() => {});
    }
  }, 1000);
}

async function spielNewsLaden(detail) {
  const ziel = el("lupeInhalt").querySelector("[data-spiel-news]");
  if (!ziel || (!detail.heim && !detail.gast)) return;
  const anfragen = [detail.heim, detail.gast]
    .filter(Boolean)
    .map((team) => api(`/api/news?team_id=${team.id}&limit=3`).catch(() => []));
  const ergebnisse = (await Promise.all(anfragen)).flat();
  // Duplikate (gleicher Link) raus, neueste zuerst
  const gesehen = new Set();
  const items = ergebnisse
    .filter((item) => !gesehen.has(item.link) && gesehen.add(item.link))
    .sort((a, b) => (b.veroeffentlicht_utc ?? "").localeCompare(a.veroeffentlicht_utc ?? ""))
    .slice(0, 5);
  if (!items.length) return;
  ziel.outerHTML = `<section class="lupe-abschnitt"><h3>News zum Spiel</h3>${items
    .map(
      (item) => `<div class="tipp-zeile"><span>
      <a class="news-titel" href="${escapeHtml(sichereUrl(item.link))}" target="_blank" rel="noopener">${escapeHtml(item.titel)}</a>
      <span class="rang-detail">${escapeHtml(item.team_name ?? item.feed_titel ?? "")}</span>
    </span></div>`
    )
    .join("")}</section>`;
}

async function spielLupeOeffnen(spielId, startTab = null) {
  const detail = await api(`/api/spiele/${spielId}`);
  zustand.lupeSpielId = spielId;
  const phase = lupePhase(detail);
  // Standard-Tab wechselt mit der Spielphase
  const tab = startTab ?? (phase === "live" ? "ticker" : phase === "nach" ? "statistik" : "tipps");
  const teile = [
    lupeScrollerHtml(detail),
    lupeHeroHtml(detail, phase),
    phase === "nach" ? punkteBannerHtml(detail) : "",
    pillTabsHtml(tab),
    `<div class="lupe-panel" data-panel="tipps"${tab === "tipps" ? "" : " hidden"}>${panelTippsHtml(detail, phase)}</div>`,
    `<div class="lupe-panel" data-panel="ticker"${tab === "ticker" ? "" : " hidden"}>${panelTickerHtml(detail)}</div>`,
    `<div class="lupe-panel" data-panel="teams"${tab === "teams" ? "" : " hidden"}>${panelTeamsHtml(detail)}</div>`,
    `<div class="lupe-panel" data-panel="statistik"${tab === "statistik" ? "" : " hidden"}>${panelStatistikHtml(detail, phase)}</div>`,
  ];
  lupeOeffnen(teile.join(""));

  if (phase === "vor") lupeCountdownStarten(spielId);
  teamTabCache.clear();
  if (detail.heim && detail.gast) {
    teamTabLaden(detail.heim.id).catch(() => {
      /* Team-Bereich ist Komfort — die Lupe funktioniert auch ohne */
    });
  }
  spielNewsLaden(detail).catch(() => {});

  const notizBereich = el("lupeInhalt").querySelector("[data-notiz-bereich]");
  if (notizBereich) notizEreignisse(notizBereich, detail.id);

  // Tipp-Eingabe in der Lupe
  const tippBereich = el("lupeInhalt").querySelector("[data-lupe-tipp]");
  if (tippBereich) {
    tippBereich.addEventListener("input", () => {
      tippBereich.querySelector(".lupe-tipp-speichern").hidden = false;
    });
    tippBereich.querySelector(".lupe-tipp-speichern").addEventListener("click", async () => {
      await tippSpeichern(detail.id, tippBereich);
      await spielLupeOeffnen(detail.id, "tipps").catch(fehlerAnzeigen);
    });
  }

  const formular = el("lupeInhalt").querySelector("[data-ereignis-formular]");
  if (formular) {
    formular.addEventListener("submit", async (ereignis) => {
      ereignis.preventDefault();
      const daten = new FormData(formular);
      try {
        await api(`/api/admin/spiele/${detail.id}/ereignis`, {
          method: "POST",
          body: JSON.stringify({
            typ: daten.get("typ"),
            minute: daten.get("minute") ? Number(daten.get("minute")) : null,
            text: daten.get("text") || null,
          }),
        });
        toast("Eintrag gespeichert ✓");
        formular.reset();
      } catch (fehler) {
        fehlerAnzeigen(fehler);
      }
    });
  }
}

/* ---------- Team-Tabs in der Spiel-Lupe (geteilte Ansicht) ---------- */

const teamTabCache = new Map();

function alterText(geburtsdatum) {
  if (!geburtsdatum) return "";
  const alter = Math.floor((Date.now() - new Date(geburtsdatum)) / 3.15576e10);
  return Number.isFinite(alter) && alter > 0 ? `${alter} J.` : "";
}

function teamTabHtml(team) {
  const teile = [];
  const chips = [];
  if (team.trainer) {
    chips.push(`<span class="steckbrief-chip">${escapeHtml(team.trainer.name)}
      <span class="neben">Trainer</span></span>`);
  }
  if (team.taktik?.formation) {
    chips.push(`<span class="steckbrief-chip">${escapeHtml(team.taktik.formation)}
      <span class="neben">Formation</span></span>`);
  }
  if (team.gruppe) chips.push(`<span class="steckbrief-chip">Gruppe ${escapeHtml(team.gruppe)}</span>`);
  if (team.ausgeschieden) chips.push('<span class="badge raus">Ausgeschieden</span>');
  if (chips.length) teile.push(`<div class="team-steckbrief">${chips.join("")}</div>`);

  teile.push(kaderListeHtml(team));
  return teile.join("");
}

/* Kompletter Kader als Positions-Liste — geteilt zwischen Spiel-Lupe
   (Teams-Tab) und Team-Lupe. */
function kaderListeHtml(team) {
  if (!team.kader) return '<p class="hinweis">Noch kein Kader hinterlegt.</p>';
  const teile = [];
  const verletzt = new Map(
    (team.verletzungen ?? []).map((fall) => [fall.spieler_name, fall.status])
  );
  const gruppen = new Map([
    ["Torwart", []],
    ["Abwehr", []],
    ["Mittelfeld", []],
    ["Sturm", []],
    ["Weitere", []],
  ]);
  for (const spieler of team.kader) {
    (gruppen.get(spieler.position) ?? gruppen.get("Weitere")).push(spieler);
  }
  for (const [position, spieler] of gruppen) {
    if (!spieler.length) continue;
    const zeilen = spieler
      .map((person) => {
        const status = verletzt.get(person.name);
        const info = status
          ? `<span class="kader-info verletzt">${escapeHtml(status === "faellt aus" ? "fällt aus" : status)}</span>`
          : `<span class="kader-info">${alterText(person.geburtsdatum)}</span>`;
        return `<button class="kader-zeile" data-spieler-lupe="${person.id}">
          <span class="kader-nummer">${person.trikotnummer ?? "–"}</span>
          <span class="kader-name">${escapeHtml(person.name)}</span>${info}</button>`;
      })
      .join("");
    teile.push(`<div class="kader-gruppe">${position}</div><div class="kader-gitter">${zeilen}</div>`);
  }
  if (!team.kader.length) teile.push('<p class="hinweis">Noch kein Kader hinterlegt.</p>');
  return teile.join("");
}

async function teamTabLaden(teamId) {
  const inhalt = el("lupeInhalt").querySelector("[data-team-inhalt]");
  if (!inhalt) return;
  inhalt.classList.remove("wechsel");
  let team = teamTabCache.get(teamId);
  if (!team) {
    inhalt.innerHTML = '<p class="hinweis">Lädt …</p>';
    team = await api(`/api/teams/${teamId}`);
    teamTabCache.set(teamId, team);
  }
  inhalt.innerHTML = teamTabHtml(team);
  void inhalt.offsetWidth; // Wechsel-Animation neu starten
  inhalt.classList.add("wechsel");
}

/* ---------- Team-Lupe mit SVG-Feld ---------- */

const POSITIONS_REIHEN = [
  ["Sturm", 150],
  ["Mittelfeld", 295],
  ["Abwehr", 440],
  ["Torwart", 560],
];

function feldSvg(kader) {
  const gruppen = new Map(POSITIONS_REIHEN.map(([name]) => [name, []]));
  const sonstige = [];
  for (const spieler of kader) {
    if (gruppen.has(spieler.position)) gruppen.get(spieler.position).push(spieler);
    else sonstige.push(spieler);
  }
  const teile = [
    `<svg viewBox="0 0 420 640" role="img" aria-label="Kader auf dem Spielfeld">
    <defs>
      <linearGradient id="rasen" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#0e3d24"/><stop offset="1" stop-color="#0a2e1b"/>
      </linearGradient>
    </defs>
    <rect width="420" height="640" rx="14" fill="url(#rasen)"/>
    ${[0, 1, 2, 3, 4, 5, 6, 7].map((i) => `<rect x="6" y="${8 + i * 78}" width="408" height="39" fill="rgba(255,255,255,0.025)"/>`).join("")}
    <rect x="14" y="14" width="392" height="612" fill="none" stroke="rgba(255,255,255,0.35)" stroke-width="2" rx="4"/>
    <circle cx="210" cy="14" r="56" fill="none" stroke="rgba(255,255,255,0.25)" stroke-width="2"/>
    <rect x="105" y="510" width="210" height="116" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="2"/>
    <rect x="155" y="576" width="110" height="50" fill="none" stroke="rgba(255,255,255,0.3)" stroke-width="2"/>`,
  ];
  for (const [position, basisY] of POSITIONS_REIHEN) {
    const spieler = gruppen.get(position);
    if (!spieler.length) continue;
    const proReihe = 5;
    spieler.forEach((person, index) => {
      const reihe = Math.floor(index / proReihe);
      const inReihe = Math.min(spieler.length - reihe * proReihe, proReihe);
      const spalte = index % proReihe;
      const x = 210 + (spalte - (inReihe - 1) / 2) * (inReihe > 4 ? 76 : 88);
      const y = basisY - reihe * 64 + (position === "Torwart" ? 0 : 0);
      const kurzname = escapeHtml(person.name.split(" ").pop());
      teile.push(`<g class="feld-spieler" data-spieler-lupe="${person.id}" role="button">
        <circle cx="${x}" cy="${y}" r="15"/>
        <text x="${x}" y="${y + 4.5}" text-anchor="middle" font-size="12">${person.trikotnummer ?? "·"}</text>
        <text class="feld-name" x="${x}" y="${y + 30}" text-anchor="middle" font-size="9.5">${kurzname}</text>
      </g>`);
    });
  }
  teile.push("</svg>");
  return `<div class="feld-rahmen">${teile.join("")}</div>${
    sonstige.length
      ? `<p class="hinweis">Ohne Positionsangabe: ${sonstige
          .map((s) => escapeHtml(s.name))
          .join(", ")}</p>`
      : ""
  }`;
}

async function teamLupeOeffnen(teamId) {
  const [team, spiele, tabellen, news] = await Promise.all([
    api(`/api/teams/${teamId}`),
    api(`/api/spiele?team_id=${teamId}`),
    api("/api/tabellen"),
    api(`/api/news?team_id=${teamId}&limit=5`),
  ]);
  const teile = [];
  const flagge = team.flagge_url
    ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="" style="width:46px;height:32px">`
    : "";
  const rausBadge = team.ausgeschieden ? ' <span class="badge raus">Ausgeschieden</span>' : "";
  teile.push(`<header class="lupe-spielkopf">
    <div class="lupe-duell" style="grid-template-columns:1fr">
      <div class="lupe-team">${flagge}<span class="name" style="font-size:1.3rem">${escapeHtml(team.name)}${rausBadge}</span>
      <span class="lupe-meta">${team.gruppe ? `Gruppe ${escapeHtml(team.gruppe)} · ` : ""}${
        team.trainer ? `Trainer: ${escapeHtml(team.trainer.name)}` : ""
      } ${pinKnopf("team", team.id)}</span></div>
    </div>
  </header>`);

  // Gruppentabelle
  const tabelle = team.gruppe ? tabellen[team.gruppe] : null;
  if (tabelle?.length) {
    teile.push(`<section class="lupe-abschnitt"><h3>Gruppe ${escapeHtml(team.gruppe)}</h3>`);
    for (const zeile of tabelle) {
      const diff = zeile.tordifferenz > 0 ? `+${zeile.tordifferenz}` : `${zeile.tordifferenz}`;
      teile.push(`<div class="tipp-zeile${zeile.team_id === team.id ? " hervorgehoben" : ""}">
        <span>${zeile.platz}. ${escapeHtml(zeile.name)}
          <span class="rang-detail">${zeile.spiele} Sp. · ${diff}</span></span>
        <span>${zeile.punkte} P.</span></div>`);
    }
    teile.push("</section>");
  }

  // Spiele (Ergebnisse + Restprogramm)
  if (spiele.length) {
    teile.push('<section class="lupe-abschnitt"><h3>Spiele</h3>');
    for (const spiel of spiele) {
      const ergebnis = istVorbei(spiel)
        ? `<strong>${spiel.tore_heim ?? "–"}:${spiel.tore_gast ?? "–"}</strong>`
        : lokaleUhrzeit(spiel.anstoss_utc);
      teile.push(`<div class="tipp-zeile">
        <span>${escapeHtml(spiel.heim?.name ?? "?")} – ${escapeHtml(spiel.gast?.name ?? "?")}
          <span class="rang-detail">${new Date(spiel.anstoss_utc).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })} · ${escapeHtml(spiel.runde)}</span></span>
        <span>${ergebnis}</span></div>`);
    }
    teile.push("</section>");
  }

  // Taktik: Formation + Beschreibung, Quelle sichtbar (SPEC 6.4)
  if (team.taktik) {
    const taktik = team.taktik;
    teile.push(`<section class="lupe-abschnitt"><h3>Taktik
      <span class="rang-detail">· ${taktik.quelle === "agent" ? "KI-Recherche" : "manuell"}</span></h3>`);
    if (taktik.formation) {
      teile.push(`<div class="tipp-zeile"><span>Formation</span><span><strong>${escapeHtml(taktik.formation)}</strong></span></div>`);
    }
    for (const [label, wert] of [
      ["Spielweise", taktik.beschreibung],
      ["Stärken", taktik.staerken],
      ["Schwächen", taktik.schwaechen],
    ]) {
      if (wert) teile.push(`<p class="hinweis"><strong>${label}:</strong> ${escapeHtml(wert)}</p>`);
    }
    teile.push("</section>");
  }

  // Verletzungen & Ausfälle
  if (team.verletzungen?.length) {
    teile.push('<section class="lupe-abschnitt"><h3>Verletzungen &amp; Ausfälle</h3>');
    for (const fall of team.verletzungen) {
      const status = fall.status === "faellt aus" ? "fällt aus" : fall.status;
      teile.push(`<div class="tipp-zeile">
        <span>${escapeHtml(fall.spieler_name)}
          <span class="rang-detail">${escapeHtml(fall.beschreibung)}${fall.geprueft ? "" : " · ungeprüft"}</span></span>
        <span class="verletzt-status ${fall.status === "faellt aus" ? "raus" : ""}">${escapeHtml(status)}</span>
      </div>`);
    }
    teile.push("</section>");
  }

  // Feld: übliche Startelf (sobald offizielle Aufstellungen vorliegen),
  // sonst wie bisher der komplette Kader; darunter immer die Kader-Liste.
  if (team.kader?.length) {
    if (team.startelf?.spieler?.length) {
      const basis = team.startelf.spiele_basis;
      const meta = [
        team.startelf.formation ? escapeHtml(team.startelf.formation) : null,
        `aus ${basis} ${basis === 1 ? "Spiel" : "Spielen"}`,
      ]
        .filter(Boolean)
        .join(" · ");
      teile.push(`<section class="lupe-abschnitt"><h3>Übliche Startelf
        <span class="rang-detail">· ${meta}</span></h3>${feldSvg(team.startelf.spieler)}</section>`);
    } else {
      teile.push(`<section class="lupe-abschnitt"><h3>Kader auf dem Feld</h3>
        <p class="hinweis">Sobald die ersten offiziellen Aufstellungen da sind,
          steht hier die übliche Startelf.</p>
        ${feldSvg(team.kader)}</section>`);
    }
    teile.push(
      `<section class="lupe-abschnitt"><h3>Kader (${team.kader.length})</h3>${kaderListeHtml(team)}</section>`
    );
  }

  // News
  if (news.length) {
    teile.push('<section class="lupe-abschnitt"><h3>News</h3>');
    for (const item of news) {
      teile.push(`<div class="tipp-zeile"><span>
        <a class="news-titel" href="${escapeHtml(sichereUrl(item.link))}" target="_blank" rel="noopener">${escapeHtml(item.titel)}</a>
        <span class="rang-detail">${item.veroeffentlicht_utc ? lokalerTag(item.veroeffentlicht_utc) : ""}</span>
      </span></div>`);
    }
    teile.push("</section>");
  }

  lupeOeffnen(teile.join(""));
}

/* ---------- Spieler-Lupe ---------- */

async function spielerLupeOeffnen(spielerId) {
  const spieler = await api(`/api/spieler/${spielerId}`);
  const stat = spieler.statistik;
  const alter = spieler.geburtsdatum
    ? Math.floor((Date.now() - new Date(spieler.geburtsdatum)) / 3.15576e10)
    : null;
  const zeilen = [
    ["Team", `${escapeHtml(spieler.team_name)}`],
    ["Position", escapeHtml(spieler.position ?? "–")],
    ["Trikotnummer", spieler.trikotnummer ?? "–"],
    alter !== null ? ["Alter", `${alter} Jahre`] : null,
    ["Tore im Turnier", stat.tore],
    stat.vorlagen !== null && stat.vorlagen !== undefined ? ["Vorlagen", stat.vorlagen] : null,
    stat.spiele !== null && stat.spiele !== undefined ? ["Einsätze", stat.spiele] : null,
    stat.gelbe_karten ? ["Gelbe Karten", stat.gelbe_karten] : null,
    stat.platzverweise ? ["Platzverweise", stat.platzverweise] : null,
  ].filter(Boolean);
  lupeOeffnen(`<header class="lupe-spielkopf">
      <div class="lupe-team">
        <span class="name" style="font-size:1.3rem">${escapeHtml(spieler.name)}</span>
        <span class="lupe-meta">${escapeHtml(spieler.team_name)}</span>
      </div>
    </header>
    <section class="lupe-abschnitt"><h3>Profil &amp; Turnierstatistik</h3>
      ${zeilen
        .map(
          ([label, wert]) =>
            `<div class="tipp-zeile"><span>${label}</span><span><strong>${wert}</strong></span></div>`
        )
        .join("")}
    </section>
    <p class="hinweis">Statistik aus dem Live-Ticker und der Torschützenliste — Details lassen
    sich vom Admin nachtragen.</p>`);
}

/* ---------- Turnier (Gruppen + K.o.-Baum) ---------- */

const KO_RUNDEN = [
  "Sechzehntelfinale",
  "Achtelfinale",
  "Viertelfinale",
  "Halbfinale",
  "Finale",
];

function teamFlagge(teamId) {
  const team = zustand.teams.find((kandidat) => kandidat.id === teamId);
  return team?.flagge_url
    ? `<img class="flagge mini" src="${escapeHtml(team.flagge_url)}" alt="" loading="lazy">`
    : '<span class="platzhalter-flagge mini"></span>';
}

function gruppenGitterHtml(tabellen) {
  const gruppen = Object.keys(tabellen).sort();
  if (!gruppen.length) {
    return '<div class="karte"><p class="hinweis">Noch keine Gruppentabellen geladen.</p></div>';
  }
  return `<div class="gruppen-gitter">${gruppen
    .map((gruppe) => {
      const zeilen = tabellen[gruppe]
        .map((zeile) => {
          const diff = zeile.tordifferenz > 0 ? `+${zeile.tordifferenz}` : `${zeile.tordifferenz}`;
          // Quali-Zonen: Platz 1–2 direkt weiter, Platz 3 möglich (beste Dritte)
          const zone = zeile.platz <= 2 ? " quali" : zeile.platz === 3 ? " quali-vielleicht" : "";
          return `<button class="gruppe-zeile${zone}" data-team-lupe="${zeile.team_id}">
            <span class="gruppe-platz">${zeile.platz}</span>
            ${teamFlagge(zeile.team_id)}
            <span class="gruppe-name">${escapeHtml(zeile.name)}</span>
            <span class="gruppe-werte">${zeile.spiele} · ${diff} · <strong>${zeile.punkte}</strong></span>
          </button>`;
        })
        .join("");
      return `<div class="karte gruppe-karte">
        <h3>Gruppe ${escapeHtml(gruppe)}</h3>
        <div class="gruppe-legende" aria-hidden="true">Sp. · Diff. · Pkt.</div>
        ${zeilen}
        <div class="gruppe-fuss">Platz 1–2 direkt weiter · Platz 3 möglich (beste Dritte)</div>
      </div>`;
    })
    .join("")}</div>`;
}

function baumSpielHtml(spiel, aktuellesId = null) {
  const fertig = spiel.status === "beendet";
  const live = spiel.status === "live" || spiel.status === "halbzeit";
  const aktuell = spiel.id === aktuellesId;
  const zeile = (team, tore, verloren) => {
    const name = team
      ? `${teamFlagge(team.id)}<span class="baum-name${verloren ? " verloren" : ""}">${escapeHtml(team.name)}</span>`
      : '<span class="baum-name offen">Noch offen</span>';
    return `<div class="baum-team">${name}<span class="baum-tore">${tore ?? ""}</span></div>`;
  };
  let heimVerloren = false;
  let gastVerloren = false;
  if (fertig && spiel.tore_heim !== null) {
    if (spiel.tore_heim !== spiel.tore_gast) {
      heimVerloren = spiel.tore_heim < spiel.tore_gast;
      gastVerloren = !heimVerloren;
    }
  }
  const datum = new Date(spiel.anstoss_utc).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
  });
  return `<button class="baum-spiel${live ? " live" : ""}${aktuell ? " aktuell" : ""}" data-spiel-lupe="${spiel.id}">
    <span class="baum-datum">${datum}${live ? ' · <span class="badge live"><span class="live-punkt"></span>LIVE</span>' : ""}</span>
    ${zeile(spiel.heim, spiel.tore_heim, heimVerloren)}
    ${zeile(spiel.gast, spiel.tore_gast, gastVerloren)}
  </button>`;
}

/* Champion-Karte ans Ende des Baums: Pokal mit „?" bis zum Finale */
function championKarteHtml(finale) {
  const sieger =
    finale && finale.status === "beendet" && finale.tore_heim !== null
      ? finale.tore_heim > finale.tore_gast
        ? finale.heim
        : finale.tore_gast > finale.tore_heim
          ? finale.gast
          : finale.elfmeter_sieger_team_id
            ? [finale.heim, finale.gast].find((t) => t?.id === finale.elfmeter_sieger_team_id)
            : null
      : null;
  if (sieger) {
    return `<div class="champion-karte gewonnen">
      <span class="champion-titel">Weltmeister</span>
      ${teamFlagge(sieger.id)}
      <span class="champion-name">${escapeHtml(sieger.name)}</span>
    </div>`;
  }
  return `<div class="champion-karte">
    <img src="/illustrationen/champion-placeholder.webp" alt="" loading="lazy">
    <span class="champion-titel">Champion</span>
    <span class="champion-name offen">Noch offen</span>
  </div>`;
}

function koBaumHtml() {
  const runden = KO_RUNDEN.map((runde) => ({
    runde,
    spiele: zustand.spiele.filter((spiel) => spiel.runde === runde),
  })).filter((eintrag) => eintrag.spiele.length);
  const platz3 = zustand.spiele.filter((spiel) => spiel.runde === "Spiel um Platz 3");
  if (!runden.length) {
    return '<div class="karte"><p class="hinweis">Die K.o.-Paarungen stehen noch nicht fest.</p></div>';
  }
  // Das laufende bzw. nächste K.o.-Spiel bekommt den hervorgehobenen
  // Rahmen ("aktuell"), Ausgeschiedene sind durchgestrichen.
  const koSpiele = runden.flatMap((eintrag) => eintrag.spiele).concat(platz3);
  const aktuelles =
    koSpiele.find((s) => s.status === "live" || s.status === "halbzeit") ??
    koSpiele
      .filter((s) => s.status === "geplant" && new Date(s.anstoss_utc) > new Date())
      .sort((a, b) => a.anstoss_utc.localeCompare(b.anstoss_utc))[0];
  const finale = runden.find((eintrag) => eintrag.runde === "Finale")?.spiele[0] ?? null;
  const spalten = runden
    .map(
      (eintrag) => `<div class="baum-spalte">
        <h3>${escapeHtml(eintrag.runde)}</h3>
        ${eintrag.spiele.map((spiel) => baumSpielHtml(spiel, aktuelles?.id)).join("")}
        ${
          eintrag.runde === "Finale"
            ? championKarteHtml(finale) +
              (platz3.length
                ? `<h3 class="platz3-titel">Spiel um Platz 3</h3>${platz3
                    .map((spiel) => baumSpielHtml(spiel, aktuelles?.id))
                    .join("")}`
                : "")
            : ""
        }
      </div>`
    )
    .join("");
  return `<div class="baum-kopfzeile">
      <p class="hinweis baum-hinweis">Ziehen · kneifen oder doppeltippen zum Zoomen · Spiel antippen</p>
      <button type="button" class="klein baum-reset" data-baum-reset hidden>⤢ Übersicht</button>
    </div>
    <div class="baum-buehne" data-baum-buehne>
      <div class="baum-leinwand"><div class="baum">${spalten}</div></div>
    </div>`;
}

/* Freier Zoom & Pan für den K.o.-Baum (v0.1.1): ein Finger/Maus zieht,
   zwei Finger kneifen, Doppeltipp auf freie Fläche zoomt, Ctrl+Scrollrad
   am Desktop. Klicks auf Spiele/Teams bleiben Klicks (Bewegungs-Schwelle);
   ohne JS bleibt das native seitliche Scrollen als Fallback. */
function baumZoomEinrichten() {
  const buehne = el("turnierInhalt").querySelector("[data-baum-buehne]");
  if (!buehne) return;
  const leinwand = buehne.querySelector(".baum-leinwand");
  const reset = el("turnierInhalt").querySelector("[data-baum-reset]");
  buehne.classList.add("aktiv");

  const ansicht = { skala: 1, x: 0, y: 0 };
  const zeiger = new Map(); // pointerId → {x, y, startX, startY}
  let pinchStart = null;
  let bewegt = false;

  const anwenden = () => {
    leinwand.style.transform = `translate(${ansicht.x}px, ${ansicht.y}px) scale(${ansicht.skala})`;
    const standard = ansicht.skala === 1 && ansicht.x === 0 && ansicht.y === 0;
    reset.hidden = standard;
  };
  const begrenzen = () => {
    ansicht.skala = Math.min(2.5, Math.max(0.4, ansicht.skala));
    // Pan locker einhegen: mindestens ein Stück Baum bleibt immer sichtbar
    const rand = 70;
    const breite = leinwand.scrollWidth * ansicht.skala;
    const hoehe = leinwand.scrollHeight * ansicht.skala;
    ansicht.x = Math.min(buehne.clientWidth - rand, Math.max(rand - breite, ansicht.x));
    ansicht.y = Math.min(buehne.clientHeight - rand, Math.max(rand - hoehe, ansicht.y));
  };
  const zoomUm = (faktor, punktX, punktY) => {
    const neu = Math.min(2.5, Math.max(0.4, ansicht.skala * faktor));
    const echt = neu / ansicht.skala;
    ansicht.x = punktX - (punktX - ansicht.x) * echt;
    ansicht.y = punktY - (punktY - ansicht.y) * echt;
    ansicht.skala = neu;
    begrenzen();
    anwenden();
  };
  const buehnenPunkt = (ereignis) => {
    const box = buehne.getBoundingClientRect();
    return [ereignis.clientX - box.left, ereignis.clientY - box.top];
  };

  buehne.addEventListener("pointerdown", (ereignis) => {
    zeiger.set(ereignis.pointerId, {
      x: ereignis.clientX,
      y: ereignis.clientY,
      startX: ereignis.clientX,
      startY: ereignis.clientY,
    });
    if (zeiger.size === 1) bewegt = false;
    if (zeiger.size === 2) {
      const [a, b] = [...zeiger.values()];
      pinchStart = { abstand: Math.hypot(a.x - b.x, a.y - b.y), skala: ansicht.skala };
    }
    buehne.setPointerCapture(ereignis.pointerId);
  });
  buehne.addEventListener("pointermove", (ereignis) => {
    const punkt = zeiger.get(ereignis.pointerId);
    if (!punkt) return;
    const dx = ereignis.clientX - punkt.x;
    const dy = ereignis.clientY - punkt.y;
    punkt.x = ereignis.clientX;
    punkt.y = ereignis.clientY;
    if (Math.hypot(ereignis.clientX - punkt.startX, ereignis.clientY - punkt.startY) > 8) {
      bewegt = true;
    }
    if (zeiger.size === 1) {
      ansicht.x += dx;
      ansicht.y += dy;
      begrenzen();
      anwenden();
    } else if (zeiger.size === 2 && pinchStart) {
      const [a, b] = [...zeiger.values()];
      const abstand = Math.hypot(a.x - b.x, a.y - b.y);
      if (abstand > 0 && pinchStart.abstand > 0) {
        const box = buehne.getBoundingClientRect();
        const mitteX = (a.x + b.x) / 2 - box.left;
        const mitteY = (a.y + b.y) / 2 - box.top;
        zoomUm((pinchStart.skala * (abstand / pinchStart.abstand)) / ansicht.skala, mitteX, mitteY);
      }
    }
  });
  const loslassen = (ereignis) => {
    zeiger.delete(ereignis.pointerId);
    if (zeiger.size < 2) pinchStart = null;
  };
  buehne.addEventListener("pointerup", loslassen);
  buehne.addEventListener("pointercancel", loslassen);

  // Nach echtem Ziehen darf der Klick darunter nicht mehr feuern
  buehne.addEventListener(
    "click",
    (ereignis) => {
      if (bewegt) {
        ereignis.stopPropagation();
        ereignis.preventDefault();
      }
    },
    true
  );
  buehne.addEventListener("dblclick", (ereignis) => {
    if (ereignis.target.closest("button")) return;
    const [x, y] = buehnenPunkt(ereignis);
    if (ansicht.skala > 1.05) {
      ansicht.skala = 1;
      ansicht.x = 0;
      ansicht.y = 0;
      anwenden();
    } else {
      zoomUm(1.6, x, y);
    }
  });
  buehne.addEventListener(
    "wheel",
    (ereignis) => {
      if (!ereignis.ctrlKey) return; // normales Scrollen nicht kapern
      ereignis.preventDefault();
      const [x, y] = buehnenPunkt(ereignis);
      zoomUm(ereignis.deltaY < 0 ? 1.15 : 0.87, x, y);
    },
    { passive: false }
  );
  reset.addEventListener("click", () => {
    ansicht.skala = 1;
    ansicht.x = 0;
    ansicht.y = 0;
    anwenden();
  });
}

async function turnierRendern() {
  const inhalt = el("turnierInhalt");
  if (zustand.turnierModus === "gruppen") {
    const tabellen = await api("/api/tabellen");
    inhalt.innerHTML = gruppenGitterHtml(tabellen);
  } else {
    inhalt.innerHTML = koBaumHtml();
    baumZoomEinrichten();
  }
}

function turnierEreignisse() {
  for (const knopf of el("turnierModus").querySelectorAll("button")) {
    knopf.addEventListener("click", () => {
      zustand.turnierModus = knopf.dataset.turnier;
      for (const anderer of el("turnierModus").querySelectorAll("button")) {
        anderer.classList.toggle("aktiv", anderer === knopf);
        anderer.setAttribute("aria-selected", anderer === knopf ? "true" : "false");
      }
      turnierRendern().catch(fehlerAnzeigen);
    });
  }
  el("turnierInhalt").addEventListener("click", (ereignis) => {
    const team = ereignis.target.closest("[data-team-lupe]");
    if (team) {
      teamLupeOeffnen(Number(team.dataset.teamLupe)).catch(fehlerAnzeigen);
      return;
    }
    const spiel = ereignis.target.closest("[data-spiel-lupe]");
    if (spiel) spielLupeOeffnen(Number(spiel.dataset.spielLupe)).catch(fehlerAnzeigen);
  });
}

/* ---------- Teams-Ansicht ---------- */

function teamsRendern() {
  const gitter = el("teamsGitter");
  const suche = zustand.teamSuche.trim().toLowerCase();
  const teams = zustand.teams.filter(
    (team) => !suche || team.name.toLowerCase().includes(suche)
  );
  if (!teams.length) {
    gitter.innerHTML = '<div class="karte"><p class="hinweis">Kein Team gefunden.</p></div>';
    return;
  }
  gitter.innerHTML = teams
    .map((team) => {
      const flagge = team.flagge_url
        ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="" loading="lazy">`
        : '<span class="platzhalter-flagge"></span>';
      const stern = zustand.pins.team.has(team.id) ? " ★" : "";
      return `<button class="team-kachel${team.ausgeschieden ? " raus" : ""}" data-team="${team.id}"
        ${team.ausgeschieden ? 'title="Ausgeschieden"' : ""}>
        ${flagge}<span class="kachel-name">${escapeHtml(team.name)}${stern}</span>
        <span class="gruppe-chip">${team.ausgeschieden ? "✕" : team.gruppe ? escapeHtml(team.gruppe) : ""}</span>
      </button>`;
    })
    .join("");
}

function teamsEreignisse() {
  el("teamsGitter").addEventListener("click", (ereignis) => {
    const kachel = ereignis.target.closest(".team-kachel");
    if (kachel) teamLupeOeffnen(Number(kachel.dataset.team)).catch(fehlerAnzeigen);
  });
  el("teamSuche").addEventListener("input", (ereignis) => {
    zustand.teamSuche = ereignis.target.value;
    teamsRendern();
  });
}

/* ---------- News-Ansicht ---------- */

async function newsTagsLaden() {
  if (el("newsTagLeiste").childElementCount) return;
  const tags = await api("/api/news/tags");
  el("newsTagLeiste").innerHTML = ["", ...tags]
    .map(
      (tag) =>
        `<button class="tag-chip${tag === zustand.newsTag ? " aktiv" : ""}" data-tag="${escapeHtml(tag)}">
          ${tag === "" ? "Alle Themen" : escapeHtml(tag)}</button>`
    )
    .join("");
}

async function newsLaden() {
  await newsTagsLaden().catch(() => {});
  const parameter = new URLSearchParams();
  if (zustand.newsTeam) parameter.set("team_id", zustand.newsTeam);
  if (zustand.newsTag) parameter.set("tag", zustand.newsTag);
  const abfrage = parameter.toString();
  const items = await api(`/api/news${abfrage ? `?${abfrage}` : ""}`);
  const liste = el("newsListe");
  zustand.newsItems = items;
  if (!items.length) {
    liste.innerHTML = emptyStateHtml(
      "empty-no-news",
      "Noch keine News",
      "Das Radio sucht noch nach einem Signal — sobald die Feeds liefern, steht hier alles Wichtige."
    );
    return;
  }
  // Hero-Artikel: der neueste Beitrag bekommt die große Bühne
  const [hero, ...rest] = items;
  const stuecke = [
    `<button class="news-hero" data-news-reader="0">
      <img src="/illustrationen/news-fallback.webp" alt="" loading="lazy">
      <span class="news-hero-text">
        <span class="news-quelle">${escapeHtml(hero.feed_titel ?? "Feed")}${
          hero.veroeffentlicht_utc ? ` · ${lokalerTag(hero.veroeffentlicht_utc)}` : ""
        }</span>
        <strong>${escapeHtml(hero.titel)}</strong>
      </span>
    </button>`,
  ];
  stuecke.push(
    ...rest.map((item, index) => {
      const zeit = item.veroeffentlicht_utc
        ? `${lokalerTag(item.veroeffentlicht_utc)}, ${lokaleUhrzeit(item.veroeffentlicht_utc)} Uhr`
        : "";
      const teamChip = item.team_name
        ? `<span class="team-chip">${escapeHtml(item.team_name)}</span>`
        : "";
      const tagChips = (item.tags ?? [])
        .map((tag) => `<span class="team-chip tag">${escapeHtml(tag)}</span>`)
        .join("");
      return `<article class="karte news-karte" data-news-reader="${index + 1}" role="button" tabindex="0">
        <div class="news-quelle">${escapeHtml(item.feed_titel ?? "Feed")} · ${zeit} ${teamChip}${tagChips}</div>
        <span class="news-titel">${escapeHtml(item.titel)}</span>
        ${item.zusammenfassung ? `<p class="news-text">${escapeHtml(item.zusammenfassung)}</p>` : ""}
      </article>`;
    })
  );
  liste.innerHTML = stuecke.join("");
}

/* News-Reader-Sheet: Artikel im Vollbild-Sheet mit Aa-Regler */
const READER_GROESSEN = ["s", "m", "l"];

function newsReaderOeffnen(item) {
  const zeit = item.veroeffentlicht_utc
    ? `${lokalerTag(item.veroeffentlicht_utc)}, ${lokaleUhrzeit(item.veroeffentlicht_utc)} Uhr`
    : "";
  lupeOeffnen(`<article class="reader" data-groesse="m">
    <div class="reader-bild"><img src="/illustrationen/news-fallback.webp" alt=""></div>
    <div class="reader-kopf">
      <button class="klein" data-reader-aa aria-label="Schriftgröße ändern">Aa</button>
    </div>
    <h2 class="reader-titel">${escapeHtml(item.titel)}</h2>
    <p class="reader-meta">${escapeHtml(item.feed_titel ?? "Feed")} · ${zeit}${
      item.team_name ? ` · ${escapeHtml(item.team_name)}` : ""
    }</p>
    <div class="reader-text">${
      item.zusammenfassung
        ? `<p>${escapeHtml(item.zusammenfassung)}</p>`
        : '<p class="hinweis">Zu diesem Artikel liefert der Feed nur die Überschrift — den ganzen Text gibt es bei der Quelle.</p>'
    }</div>
    <a class="reader-quelle" href="${escapeHtml(sichereUrl(item.link))}" target="_blank" rel="noopener">Zur Quelle ↗</a>
  </article>`);
}

/* ---------- Rangliste + Bonusfragen ---------- */

async function ranglisteLaden() {
  let pfad = "/api/rangliste";
  if (zustand.zeitraum === "heute") {
    // Lokales Datum (nicht UTC), sonst springt "Heute" nachts auf den Vortag
    const heute = new Date().toLocaleDateString("en-CA");
    pfad += `?datum=${heute}`;
  } else if (zustand.zeitraum === "runde" && zustand.runde) {
    pfad += `?runde=${encodeURIComponent(zustand.runde)}`;
  }
  const eintraege = await api(pfad);
  const podium = el("podium");
  const liste = el("ranglisteListe");
  if (!eintraege.length) {
    podium.hidden = true;
    liste.innerHTML = emptyStateHtml(
      "empty-no-tipps",
      "Noch keine Wertung",
      "Sobald das erste Spiel gewertet ist, füllt sich die Rangliste."
    );
  } else {
    const spitze = eintraege.filter((eintrag) => eintrag.platz <= 3).slice(0, 3);
    const zeigePodium = zustand.zeitraum === "gesamt" && spitze.some((e) => e.punkte > 0);
    podium.hidden = !zeigePodium;
    if (zeigePodium) {
      const klassen = ["eins", "zwei", "drei"];
      const medaillen = ["🥇", "🥈", "🥉"];
      podium.innerHTML =
        `<img class="podium-bild" src="/illustrationen/podium.webp" alt="" loading="lazy">` +
        `<div class="podium-plaetze">` +
        spitze
          .map((eintrag, index) => {
            const ki = eintrag.rolle === "ki" ? '<span class="ki">KI</span>' : "";
            return `<div class="podium-platz ${klassen[index]}">
            <div class="podium-medaille">${medaillen[eintrag.platz - 1] ?? ""}</div>
            <div class="podium-name">${escapeHtml(eintrag.anzeigename)}${ki}</div>
            <div class="podium-punkte">${eintrag.punkte}</div>
            <div class="podium-detail">${eintrag.exakt}× exakt</div>
          </div>`;
          })
          .join("") +
        `</div>`;
    }
    // Eine Tabelle statt Einzelkarten: die Spalten-Labels stehen genau einmal
    // im Kopf. Tendenz erst ab 720 px (.nur-breit), sonst wird 375 px zu eng.
    const mitBonus = eintraege.some((eintrag) => eintrag.bonus_punkte > 0);
    const zeilen = eintraege
      .map((eintrag) => {
        const ki = eintrag.rolle === "ki" ? '<span class="ki">KI</span>' : "";
        const bonus = eintrag.bonus_punkte ? ` title="inkl. +${eintrag.bonus_punkte} Bonus"` : "";
        return `<tr>
          <td class="num rang-platz">${eintrag.platz}</td>
          <td class="rang-name">${escapeHtml(eintrag.anzeigename)}${ki}</td>
          <td class="num">${eintrag.tipps_gewertet}</td>
          <td class="num">${eintrag.exakt}</td>
          <td class="num">${eintrag.differenz}</td>
          <td class="num nur-breit">${eintrag.tendenz}</td>
          <td class="rang-form">${tipperFormkette(eintrag.form)}</td>
          <td class="num rang-punkte"${bonus}>${eintrag.punkte}</td>
        </tr>`;
      })
      .join("");
    liste.innerHTML = `<div class="karte rang-tabelle-karte">
      <table class="rang-tabelle">
        <thead><tr>
          <th class="num" aria-label="Platz">#</th>
          <th>Tipper</th>
          <th class="num" title="Gewertete Tipps">Sp</th>
          <th class="num" title="Exakte Ergebnisse">4er</th>
          <th class="num" title="Richtige Tordifferenz">3er</th>
          <th class="num nur-breit" title="Richtige Tendenz">2er</th>
          <th>Form</th>
          <th class="num">Pkt</th>
        </tr></thead>
        <tbody>${zeilen}</tbody>
      </table>
      <p class="rang-fussnote">Sp = gewertete Tipps · 4er = exakt · 3er = Differenz · 2er = Tendenz${
        mitBonus ? " · Pkt inkl. Bonuspunkte" : ""
      }</p>
    </div>`;
  }
  await bonusfragenLaden().catch(fehlerAnzeigen);
}

/* Formkette eines Tippers: Punkte der letzten gewerteten Tipps als Farbpunkte
   (4 = gold, 3 = grün, 2 = grau, 0 = rot) */
function tipperFormkette(form) {
  if (!form?.length) return "";
  const klasse = (punkte) =>
    punkte >= 4 ? "exakt" : punkte === 3 ? "s" : punkte === 2 ? "u" : "n";
  return `<span class="formkette klein" aria-label="Letzte Tipps">${form
    .map((punkte) => `<span class="form-punkt ${klasse(punkte)}">${punkte}</span>`)
    .join("")}</span>`;
}

function bonusAntwortFeld(frage) {
  if (frage.typ === "team") {
    const optionen = zustand.teams
      .map(
        (team) =>
          `<option value="${team.id}" ${
            frage.mein_tipp?.antwort_ref === team.id ? "selected" : ""
          }>${escapeHtml(team.name)}</option>`
      )
      .join("");
    return `<select data-bonus-antwort><option value="">– Team wählen –</option>${optionen}</select>`;
  }
  const wert = frage.mein_tipp?.antwort_name ?? "";
  return `<input data-bonus-suche list="bonusSpieler-${frage.id}" placeholder="Spieler suchen …"
      value="${escapeHtml(wert)}" autocomplete="off">
    <datalist id="bonusSpieler-${frage.id}"></datalist>
    <input type="hidden" data-bonus-antwort value="${frage.mein_tipp?.antwort_ref ?? ""}">`;
}

async function bonusfragenLaden() {
  const fragen = await api("/api/bonusfragen");
  el("bonusBereich").hidden = !fragen.length;
  if (!fragen.length) return;
  el("bonusListe").innerHTML = fragen
    .map((frage) => {
      const schluss = `${lokalerTag(frage.einsendeschluss_utc)}, ${lokaleUhrzeit(frage.einsendeschluss_utc)} Uhr`;
      const countdown = `<span class="bonus-countdown" data-bonus-schluss="${frage.einsendeschluss_utc}">${countdownText(
        new Date(frage.einsendeschluss_utc) - new Date()
      )}</span>`;
      const teile = [
        `<div class="karte bonus-karte" data-bonus="${frage.id}">
        <div class="frage">${escapeHtml(frage.frage)}
          <span class="punkte-chip">${frage.punkte_wert} P.</span></div>
        <p class="bonus-schluss">${frage.offen ? `Einsendeschluss: ${schluss} · noch ${countdown}` : frage.aufloesung_name ? `Auflösung: <strong>${escapeHtml(frage.aufloesung_name)}</strong>` : "Einsendeschluss vorbei — Auflösung folgt."}</p>`,
      ];
      if (frage.offen) {
        teile.push(`<div class="bonus-antwort">${bonusAntwortFeld(frage)}
          <button class="primaer klein" data-bonus-speichern="${frage.id}">Speichern</button></div>`);
        if (frage.mein_tipp) {
          teile.push(
            `<p class="hinweis">Dein Tipp: <strong>${escapeHtml(frage.mein_tipp.antwort_name ?? "?")}</strong></p>`
          );
        }
      } else {
        for (const tipp of frage.tipps) {
          const ki = tipp.rolle === "ki" ? '<span class="ki">KI</span>' : "";
          const punkte =
            tipp.punkte !== null ? ` <span class="punkte-chip">+${tipp.punkte}</span>` : "";
          teile.push(`<div class="tipp-zeile"><span>${escapeHtml(tipp.anzeigename)}${ki}</span>
            <span>${escapeHtml(tipp.antwort_name ?? "?")}${punkte}</span></div>`);
        }
      }
      teile.push("</div>");
      return teile.join("");
    })
    .join("");
  bonusCountdownStarten();
}

/* Tickender Einsendeschluss-Timer auf den offenen Bonus-Karten; läuft eine
   Frist ab, lädt die Liste neu (Eingabe verschwindet, Sperre greift sichtbar). */
let bonusCountdownTimer = null;

function bonusCountdownStarten() {
  clearInterval(bonusCountdownTimer);
  if (!el("bonusListe").querySelector("[data-bonus-schluss]")) return;
  bonusCountdownTimer = setInterval(() => {
    const felder = el("bonusListe").querySelectorAll("[data-bonus-schluss]");
    if (!felder.length) {
      clearInterval(bonusCountdownTimer);
      return;
    }
    let abgelaufen = false;
    for (const feld of felder) {
      const rest = new Date(feld.dataset.bonusSchluss) - new Date();
      feld.textContent = countdownText(rest);
      if (rest <= 0) abgelaufen = true;
    }
    if (abgelaufen) {
      clearInterval(bonusCountdownTimer);
      bonusfragenLaden().catch(fehlerAnzeigen);
    }
  }, 1000);
}

function bonusEreignisse() {
  el("bonusListe").addEventListener("input", async (ereignis) => {
    const suche = ereignis.target.closest("[data-bonus-suche]");
    if (!suche) return;
    const begriff = suche.value.trim();
    if (begriff.length < 2) return;
    try {
      const treffer = await api(`/api/spieler?suche=${encodeURIComponent(begriff)}`);
      const datalist = suche.nextElementSibling;
      datalist.innerHTML = treffer
        .map(
          (spieler) =>
            `<option value="${escapeHtml(spieler.name)} (${escapeHtml(spieler.team_name)})" data-id="${spieler.id}">`
        )
        .join("");
      const versteckt = suche.parentElement.querySelector("[data-bonus-antwort]");
      const wahl = [...datalist.options].find((option) => option.value === suche.value);
      versteckt.value = wahl ? wahl.dataset.id : "";
    } catch {
      /* Suche ist nur Komfort */
    }
  });
  el("bonusListe").addEventListener("click", async (ereignis) => {
    const knopf = ereignis.target.closest("[data-bonus-speichern]");
    if (!knopf) return;
    const karte = knopf.closest(".bonus-karte");
    const antwort = karte.querySelector("[data-bonus-antwort]").value;
    if (!antwort) {
      toast("Bitte erst eine Antwort wählen.", true);
      return;
    }
    try {
      await api("/api/bonustipps", {
        method: "POST",
        body: JSON.stringify({
          bonusfrage_id: Number(knopf.dataset.bonusSpeichern),
          antwort_ref: Number(antwort),
        }),
      });
      toast("Bonustipp gespeichert ✓");
      await bonusfragenLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
}

function ranglisteEreignisse() {
  for (const knopf of document.querySelectorAll("#view-rangliste .segmente button")) {
    knopf.addEventListener("click", () => {
      zustand.zeitraum = knopf.dataset.zeitraum;
      for (const anderer of document.querySelectorAll("#view-rangliste .segmente button")) {
        anderer.classList.toggle("aktiv", anderer === knopf);
        anderer.setAttribute("aria-selected", anderer === knopf ? "true" : "false");
      }
      const rundenWahl = el("ranglisteRunde");
      rundenWahl.hidden = zustand.zeitraum !== "runde";
      if (zustand.zeitraum === "runde" && !zustand.runde) {
        zustand.runde = rundenWahl.value;
      }
      ranglisteLaden().catch(fehlerAnzeigen);
    });
  }
  el("ranglisteRunde").addEventListener("change", (ereignis) => {
    zustand.runde = ereignis.target.value;
    ranglisteLaden().catch(fehlerAnzeigen);
  });
}

/* ---------- Web Push ---------- */

function base64ZuUint8(base64) {
  const auffuellung = "=".repeat((4 - (base64.length % 4)) % 4);
  const roh = atob((base64 + auffuellung).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(roh, (zeichen) => zeichen.charCodeAt(0));
}

async function pushStatusLaden() {
  const zeile = el("pushZeile");
  const schalter = el("pushSchalter");
  const erinnerung = el("erinnerungZeile");
  const hinweis = el("pushHinweis");
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    zeile.hidden = true;
    erinnerung.hidden = true;
    return;
  }
  try {
    const info = await api("/api/push/vapid-key");
    if (!info.aktiv) {
      zeile.hidden = true;
      erinnerung.hidden = true;
      hinweis.textContent = "Push ist auf dem Server nicht konfiguriert (VAPID-Schlüssel fehlen).";
      hinweis.hidden = zustand.nutzer?.rolle !== "admin";
      return;
    }
    const registrierung = await navigator.serviceWorker.ready;
    const abo = await registrierung.pushManager.getSubscription();
    zustand.pushAktiv = Boolean(abo);
    zeile.hidden = false;
    schalter.checked = zustand.pushAktiv;
    schalter.dataset.publicKey = info.public_key;
    // Vorlaufzeit der Tipp-Erinnerung nur zeigen, wenn Push überhaupt ankommt
    erinnerung.hidden = !zustand.pushAktiv;
    if (zustand.pushAktiv) {
      const ich = await api("/api/me");
      el("erinnerungVorlauf").value = String(ich.tipp_erinnerung_minuten ?? 120);
    }
  } catch {
    zeile.hidden = true;
    erinnerung.hidden = true;
  }
}

async function pushUmschalten() {
  const schalter = el("pushSchalter");
  try {
    const registrierung = await navigator.serviceWorker.ready;
    const bestehend = await registrierung.pushManager.getSubscription();
    if (bestehend) {
      await api("/api/push/unsubscribe", {
        method: "POST",
        body: JSON.stringify({ endpoint: bestehend.endpoint }),
      });
      await bestehend.unsubscribe();
      zustand.pushAktiv = false;
      toast("Benachrichtigungen deaktiviert");
    } else {
      const erlaubnis = await Notification.requestPermission();
      if (erlaubnis !== "granted") {
        toast("Benachrichtigungen wurden im Browser abgelehnt.", true);
        return;
      }
      const abo = await registrierung.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: base64ZuUint8(schalter.dataset.publicKey),
      });
      await api("/api/push/subscribe", { method: "POST", body: JSON.stringify(abo.toJSON()) });
      zustand.pushAktiv = true;
      toast("Benachrichtigungen aktiv 🔔 — Tore, Anpfiffe, Tipp-Erinnerungen");
    }
  } catch (fehler) {
    fehlerAnzeigen(fehler);
  } finally {
    // Schalter und Erinnerungs-Zeile immer auf den echten Stand bringen
    await pushStatusLaden().catch(() => {});
  }
}

/* ---------- Wettbewerbs-/Saison-Auswahl ---------- */

/* Wettbewerbs-Icons (Strichstil wie die Tab-Bar): Pokal für die WM,
   Ball für die Bundesliga. */
const WETTBEWERB_ICONS = {
  WC: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4h12v3a4 4 0 0 1-4 4h-4A4 4 0 0 1 6 7Z" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M6 5H4a2 2 0 0 0 2 4M18 5h2a2 2 0 0 1-2 4" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 11v4m-3 5h6m-3-5v5" stroke="currentColor" stroke-width="1.8"/></svg>',
  BL1: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M12 7.2 16.4 10.4 14.7 15.6H9.3L7.6 10.4Z" fill="currentColor"/></svg>',
};

function wettbewerbIcon(code) {
  return WETTBEWERB_ICONS[code] ?? WETTBEWERB_ICONS.WC;
}

async function wettbewerbeLaden() {
  try {
    zustand.wettbewerbe = await api("/api/wettbewerbe");
  } catch {
    zustand.wettbewerbe = [];
  }
  const knopf = el("wettbewerbKnopf");
  const aktiv = zustand.wettbewerbe.find((wettbewerb) => wettbewerb.aktiv);
  if (!aktiv) {
    knopf.hidden = true;
    return;
  }
  // Icon statt Textliste: Pokal + Saison, der volle Name wandert ins aria-label.
  knopf.innerHTML = `${wettbewerbIcon(aktiv.code)}<span>${escapeHtml(aktiv.saison)}</span>`;
  knopf.setAttribute("aria-label", `Wettbewerb wählen — aktiv: ${aktiv.name} ${aktiv.saison}`);
  knopf.hidden = false;
}

function wettbewerbsWahlOeffnen() {
  const teile = [
    `<header class="lupe-spielkopf"><div class="lupe-team">
      <span class="name" style="font-size:1.25rem">Wettbewerb wählen</span>
      <span class="lupe-meta">Ligen &amp; Saisons — weitere folgen nach dem Turnier</span>
    </div></header>`,
    '<div class="wb-liste" style="margin-top:14px">',
  ];
  for (const wettbewerb of zustand.wettbewerbe) {
    const badge = wettbewerb.aktiv
      ? '<span class="badge" style="background:var(--akzent);color:var(--akzent-ink);border-color:transparent">Aktiv</span>'
      : `<span class="badge">${escapeHtml(wettbewerb.hinweis ?? "Bald")}</span>`;
    teile.push(`<button class="wb-eintrag ${wettbewerb.aktiv ? "aktuell" : "bald"}"
        data-wettbewerb="${escapeHtml(wettbewerb.code)}"${wettbewerb.aktiv ? "" : ' aria-disabled="true"'}>
      <span class="wb-logo${wettbewerb.code === "BL1" ? " bl" : ""}">${wettbewerbIcon(wettbewerb.code)}</span>
      <span style="min-width:0">
        <span class="wb-name">${escapeHtml(wettbewerb.name)} ${escapeHtml(wettbewerb.saison)}</span><br>
        <span class="wb-detail">${escapeHtml(wettbewerb.beschreibung ?? "")}</span>
      </span>
      ${badge}
    </button>`);
  }
  teile.push("</div>");
  lupeOeffnen(teile.join(""));
}

function wettbewerbGewaehlt(code) {
  const wettbewerb = zustand.wettbewerbe.find((eintrag) => eintrag.code === code);
  if (!wettbewerb) return;
  if (wettbewerb.aktiv) {
    lupeSchliessen();
    toast(`${wettbewerb.name} ${wettbewerb.saison} läuft — viel Erfolg beim Tippen! ⚽`);
  } else {
    toast(wettbewerb.hinweis_lang ?? `${wettbewerb.name} ${wettbewerb.saison} wird nach der WM freigeschaltet.`);
  }
}

/* ---------- Onboarding nach Registrierung ---------- */

function onboardingSchritt(name) {
  for (const schritt of document.querySelectorAll(".onboarding-schritt")) {
    schritt.hidden = schritt.dataset.schritt !== name;
  }
  if (name === "team") onboardingTeamsRendern();
  window.scrollTo(0, 0);
}

function onboardingTeamsRendern() {
  const suche = zustand.onboardingSuche.trim().toLowerCase();
  const teams = zustand.teams.filter(
    (team) => !suche || team.name.toLowerCase().includes(suche)
  );
  el("onboardingTeams").innerHTML = teams
    .map((team) => {
      const gewaehlt = zustand.pins.team.has(team.id);
      const flagge = team.flagge_url
        ? `<img class="flagge" src="${escapeHtml(team.flagge_url)}" alt="" loading="lazy">`
        : '<span class="platzhalter-flagge"></span>';
      return `<button class="flaggen-kachel${gewaehlt ? " gewaehlt" : ""}"
        data-onboarding-team="${team.id}" aria-pressed="${gewaehlt}">
        ${flagge}<span>${escapeHtml(team.fifa_code)}</span></button>`;
    })
    .join("");
}

function onboardingEreignisse() {
  el("onboardingSuche").addEventListener("input", (ereignis) => {
    zustand.onboardingSuche = ereignis.target.value;
    onboardingTeamsRendern();
  });
  el("onboardingPush").addEventListener("change", async (ereignis) => {
    // Master-Toggle: fragt die Browser-Berechtigung an bzw. meldet ab
    if (ereignis.target.checked !== zustand.pushAktiv) await pushUmschalten();
    ereignis.target.checked = zustand.pushAktiv;
  });
  el("view-onboarding").addEventListener("click", async (ereignis) => {
    const team = ereignis.target.closest("[data-onboarding-team]");
    if (team) {
      await pinUmschalten("team", Number(team.dataset.onboardingTeam));
      onboardingTeamsRendern();
      return;
    }
    if (ereignis.target.closest("[data-onboarding-weiter]")) {
      onboardingSchritt("team");
      return;
    }
    if (ereignis.target.closest("[data-onboarding-fertig]")) {
      zeigeAnsicht("heute");
      spieleRendern();
    }
  });
}

/* ---------- Registrierung ---------- */

function loginTabsEinrichten() {
  const tabs = [
    ["tabAnmelden", "loginFormular"],
    ["tabRegistrieren", "registrierFormular"],
  ];
  for (const [tabId, formId] of tabs) {
    el(tabId).addEventListener("click", () => {
      for (const [andererTab, anderesFormular] of tabs) {
        el(andererTab).classList.toggle("aktiv", andererTab === tabId);
        el(andererTab).setAttribute("aria-selected", andererTab === tabId ? "true" : "false");
        el(anderesFormular).hidden = anderesFormular !== formId;
      }
    });
  }
}

async function registrierenAbsenden(ereignis) {
  ereignis.preventDefault();
  const fehler = el("registrierFehler");
  fehler.hidden = true;
  if (el("regPin").value !== el("regPin2").value) {
    fehler.textContent = "Die beiden PINs stimmen nicht überein.";
    fehler.hidden = false;
    return;
  }
  try {
    zustand.nutzer = await api("/api/registrieren", {
      method: "POST",
      body: JSON.stringify({
        anzeigename: el("regName").value.trim(),
        pin: el("regPin").value,
        gruppen_passwort: el("regGruppe").value,
      }),
    });
    el("regPin").value = "";
    el("regPin2").value = "";
    el("regGruppe").value = "";
    toast(`Willkommen, ${zustand.nutzer.anzeigename}! 🎉`);
    // Frische Konten starten mit dem 2-Schritt-Onboarding (Push, Teams)
    await appStarten("onboarding");
    onboardingSchritt("push");
  } catch (fehlerObjekt) {
    fehler.textContent = fehlerObjekt.message;
    fehler.hidden = false;
  }
}

/* ---------- Mehr / Verwaltung ---------- */

function adminMeldung(text, istFehler = false) {
  const meldung = el("adminMeldung");
  meldung.textContent = text;
  meldung.classList.toggle("fehler", istFehler);
  meldung.hidden = false;
}

async function verwaltungLaden() {
  await pushStatusLaden();
  api("/api/health")
    .then((info) => {
      if (info.version) el("appVersion").textContent = `WM26 v${info.version}`;
    })
    .catch(() => {});
  if (zustand.nutzer?.rolle !== "admin") return;
  await Promise.all([
    syncStatusLaden(),
    nutzerLaden().catch(fehlerAnzeigen),
    adminFeedbackLaden().catch(fehlerAnzeigen),
    feedsLaden().catch(fehlerAnzeigen),
    overridesLaden().catch(fehlerAnzeigen),
    adminBonusLaden().catch(fehlerAnzeigen),
    tokensLaden().catch(fehlerAnzeigen),
    beitraegeLaden().catch(fehlerAnzeigen),
  ]);
}

async function nutzerLaden() {
  const nutzer = await api("/api/admin/nutzer");
  el("nutzerListe").innerHTML = nutzer.length
    ? nutzer
        .map((person) => {
          const ich = person.id === zustand.nutzer?.id;
          const kiImmer = person.rolle === "admin" || person.rolle === "ki";
          const kiKnopf = kiImmer
            ? '<span class="neben">KI-Wertung: immer</span>'
            : `<button class="klein${person.ki_freigeschaltet ? "" : " gefahr"}"
                 data-nutzer-ki="${person.id}" data-ki-neu="${person.ki_freigeschaltet ? 0 : 1}">
                 KI-Wertung: ${person.ki_freigeschaltet ? "an" : "aus"}</button>`;
          const sichtbarKnopf = `<button class="klein${person.rangliste_sichtbar ? "" : " gefahr"}"
                 data-nutzer-sichtbar="${person.id}" data-sichtbar-neu="${person.rangliste_sichtbar ? 0 : 1}">
                 Rangliste: ${person.rangliste_sichtbar ? "an" : "aus"}</button>`;
          return `<div class="verwaltungszeile">
            <span>${escapeHtml(person.anzeigename)}<span class="rollen-chip ${person.rolle}">${person.rolle}</span>${
              ich ? ' <span class="neben">(du)</span>' : ""
            }</span>
            <span class="fuss-knoepfe">
              ${kiKnopf}
              ${sichtbarKnopf}
              <button class="klein" data-nutzer-pin="${person.id}"
                data-nutzer-name="${escapeHtml(person.anzeigename)}">PIN</button>
              ${
                ich
                  ? ""
                  : `<button class="klein gefahr" data-nutzer-loeschen="${person.id}"
                      data-nutzer-name="${escapeHtml(person.anzeigename)}">Löschen</button>`
              }
            </span></div>`;
        })
        .join("")
    : '<p class="hinweis">Noch keine Nutzer.</p>';
}

/* Posteingang der Verwaltung: Feedback/Fehlermeldungen der Nutzer (v0.1.1) */
async function adminFeedbackLaden() {
  const meldungen = await api("/api/admin/feedback");
  el("feedbackListe").innerHTML = meldungen.length
    ? meldungen
        .map(
          (meldung) => `<div class="verwaltungszeile feedback-eintrag${
            meldung.status === "erledigt" ? " erledigt" : ""
          }">
        <span class="feedback-inhalt">
          <span><span class="kategorie-chip ${meldung.kategorie}">${meldung.kategorie}</span>
            <span class="neben">${escapeHtml(meldung.anzeigename)} ·
              ${lokalerTag(meldung.erstellt_utc)}, ${lokaleUhrzeit(meldung.erstellt_utc)} Uhr</span></span>
          <span class="feedback-text">${escapeHtml(meldung.nachricht)}</span>
        </span>
        <span class="fuss-knoepfe">
          <button class="klein${meldung.status === "offen" ? " primaer" : ""}"
            data-feedback-umschalten="${meldung.id}">
            ${meldung.status === "offen" ? "Erledigt" : "Wieder öffnen"}</button>
          <button class="klein gefahr" data-feedback-loeschen="${meldung.id}">Löschen</button>
        </span></div>`
        )
        .join("")
    : '<p class="hinweis">Posteingang leer — keine Meldungen.</p>';
}

async function tokensLaden() {
  const tokens = await api("/api/admin/agent-tokens");
  el("tokenListe").innerHTML = tokens.length
    ? tokens
        .map(
          (token) => `<div class="verwaltungszeile">
        <span>${escapeHtml(token.name)}
          <span class="neben">${escapeHtml(token.scopes)}${token.widerrufen_utc ? " · widerrufen" : ""}</span></span>
        ${token.widerrufen_utc ? "" : `<button class="klein gefahr" data-token-widerrufen="${token.id}">Widerrufen</button>`}
      </div>`
        )
        .join("")
    : '<p class="hinweis">Noch keine Tokens.</p>';
}

async function beitraegeLaden() {
  const beitraege = await api("/api/admin/beitraege");
  el("beitragListe").innerHTML = beitraege.length
    ? beitraege
        .map((beitrag) => {
          let inhalt = beitrag.inhalt_json;
          try {
            const daten = JSON.parse(beitrag.inhalt_json);
            inhalt = Object.entries(daten)
              .map(([schluessel, wert]) => `${schluessel}: ${wert}`)
              .join(" · ");
          } catch {
            /* Roh-JSON anzeigen */
          }
          const bezug = beitrag.team_name ?? beitrag.spieler_name ?? "?";
          return `<div class="verwaltungszeile">
            <span><strong>${escapeHtml(beitrag.typ)}</strong> für ${escapeHtml(bezug)}
              <span class="neben">${escapeHtml(inhalt)} — von ${escapeHtml(beitrag.agent_name)}</span></span>
            <span class="fuss-knoepfe">
              <button class="klein primaer" data-beitrag="${beitrag.id}" data-entscheidung="uebernehmen">Übernehmen</button>
              <button class="klein gefahr" data-beitrag="${beitrag.id}" data-entscheidung="verwerfen">Verwerfen</button>
            </span></div>`;
        })
        .join("")
    : '<p class="hinweis">Keine offenen Vorschläge.</p>';
}

async function syncStatusLaden() {
  try {
    const status = await api("/api/admin/sync-status");
    el("syncStatus").innerHTML = status.length
      ? status
          .map(
            (job) =>
              `<div><strong>${escapeHtml(job.job)}</strong>: ${escapeHtml(job.status ?? "—")} · letzter Erfolg ${
                job.letzter_erfolg_utc ? lokaleUhrzeit(job.letzter_erfolg_utc) : "nie"
              } · ${escapeHtml(job.detail ?? "")}</div>`
          )
          .join("")
      : "<div>Noch kein Sync gelaufen.</div>";
  } catch {
    el("syncStatus").textContent = "Sync-Status nicht verfügbar.";
  }
}

async function feedsLaden() {
  const feeds = await api("/api/admin/feeds");
  el("feedListe").innerHTML = feeds.length
    ? feeds
        .map(
          (feed) => `<div class="verwaltungszeile">
        <span>${escapeHtml(feed.titel ?? feed.url)}
          <span class="neben">${feed.anzahl_news} Einträge${feed.aktiv ? "" : " · pausiert"}</span></span>
        <span class="fuss-knoepfe">
          <button class="klein" data-feed-umschalten="${feed.id}">${feed.aktiv ? "Pausieren" : "Aktivieren"}</button>
          <button class="klein gefahr" data-feed-loeschen="${feed.id}">Löschen</button>
        </span></div>`
        )
        .join("")
    : '<p class="hinweis">Noch keine Feeds abonniert.</p>';
}

async function overridesLaden() {
  const liste = await api("/api/admin/overrides");
  el("overrideListe").innerHTML = liste.length
    ? liste
        .map(
          (eintrag) => `<div class="verwaltungszeile">
        <span>${escapeHtml(eintrag.entitaet)} #${eintrag.entitaet_id} · ${escapeHtml(eintrag.feld)} = ${escapeHtml(eintrag.wert ?? "leer")}
          <span class="neben">von ${escapeHtml(eintrag.gesetzt_von_name ?? "?")}</span></span>
        <button class="klein" data-override-aufheben="${eintrag.id}">Aufheben</button></div>`
        )
        .join("")
    : '<p class="hinweis">Keine aktiven Overrides.</p>';
}

async function adminBonusLaden() {
  const fragen = await api("/api/bonusfragen");
  el("adminBonusListe").innerHTML = fragen.length
    ? fragen
        .map((frage) => {
          const aufloesung = frage.aufloesung_ref
            ? `<span class="neben">aufgelöst: ${escapeHtml(frage.aufloesung_name ?? "")}</span>`
            : `<button class="klein" data-bonus-aufloesen="${frage.id}" data-bonus-typ="${frage.typ}">Auflösen</button>`;
          return `<div class="verwaltungszeile">
          <span>${escapeHtml(frage.frage)} <span class="neben">${frage.punkte_wert} P.</span></span>
          ${aufloesung}</div>`;
        })
        .join("")
    : '<p class="hinweis">Noch keine Bonusfragen.</p>';
}

async function bonusAufloesenDialog(frageId, typ) {
  const name = prompt(
    typ === "team"
      ? "Richtige Antwort: Teamname (z. B. Deutschland)"
      : "Richtige Antwort: Spielername (exakt)"
  );
  if (!name) return;
  try {
    let ref = null;
    if (typ === "team") {
      const team = zustand.teams.find(
        (kandidat) => kandidat.name.toLowerCase() === name.trim().toLowerCase()
      );
      if (!team) throw new Error(`Team '${name}' nicht gefunden`);
      ref = team.id;
    } else {
      const treffer = await api(`/api/spieler?suche=${encodeURIComponent(name.trim())}&limit=2`);
      if (treffer.length !== 1) {
        throw new Error(
          treffer.length === 0 ? `Spieler '${name}' nicht gefunden` : "Name nicht eindeutig — bitte exakter"
        );
      }
      ref = treffer[0].id;
    }
    const ergebnis = await api(`/api/admin/bonusfragen/${frageId}/aufloesen`, {
      method: "POST",
      body: JSON.stringify({ aufloesung_ref: ref }),
    });
    toast(`Aufgelöst — ${ergebnis.tipps_gewertet} Tipps gewertet`);
    await Promise.all([adminBonusLaden(), bonusfragenLaden()]);
  } catch (fehler) {
    fehlerAnzeigen(fehler);
  }
}

function mehrEreignisse() {
  for (const knopf of document.querySelectorAll("[data-mehr-ziel]")) {
    knopf.addEventListener("click", () => zeigeAnsicht(knopf.dataset.mehrZiel));
  }
  el("logoutKnopf").addEventListener("click", async () => {
    try {
      await api("/api/logout", { method: "POST" });
    } catch {
      /* Session war bereits abgelaufen */
    }
    zeigeLogin();
  });
  el("pushSchalter").addEventListener("change", pushUmschalten);
  el("erinnerungVorlauf").addEventListener("change", async (ereignis) => {
    try {
      const minuten = Number(ereignis.target.value);
      await api("/api/me/einstellungen", {
        method: "PATCH",
        body: JSON.stringify({ tipp_erinnerung_minuten: minuten }),
      });
      if (zustand.nutzer) zustand.nutzer.tipp_erinnerung_minuten = minuten;
      toast(minuten === 0 ? "Tipp-Erinnerung ausgeschaltet" : "Tipp-Erinnerung gespeichert ✓");
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });

  // Feedback/Fehler melden (v0.1.1): Kategorie-Wahl + Formular
  el("feedbackKategorie").addEventListener("click", (ereignis) => {
    const knopf = ereignis.target.closest("[data-kategorie]");
    if (!knopf) return;
    for (const anderer of el("feedbackKategorie").querySelectorAll("[data-kategorie]")) {
      anderer.classList.toggle("aktiv", anderer === knopf);
      anderer.setAttribute("aria-checked", anderer === knopf ? "true" : "false");
    }
  });
  el("feedbackFormular").addEventListener("submit", async (ereignis) => {
    ereignis.preventDefault();
    const nachricht = el("feedbackText").value.trim();
    if (!nachricht) return;
    try {
      await api("/api/feedback", {
        method: "POST",
        body: JSON.stringify({
          kategorie:
            el("feedbackKategorie").querySelector(".aktiv")?.dataset.kategorie ?? "sonstiges",
          nachricht,
        }),
      });
      ereignis.target.reset();
      // Kategorie-Pills liegen außerhalb des Formulars: zurück auf den Standard
      for (const knopf of el("feedbackKategorie").querySelectorAll("[data-kategorie]")) {
        const standard = knopf.dataset.kategorie === "fehler";
        knopf.classList.toggle("aktiv", standard);
        knopf.setAttribute("aria-checked", standard ? "true" : "false");
      }
      toast("Danke! Deine Meldung ist angekommen ✓");
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });

  el("tokenFormular").addEventListener("submit", async (ereignis) => {
    ereignis.preventDefault();
    try {
      const neu = await api("/api/admin/agent-tokens", {
        method: "POST",
        body: JSON.stringify({
          name: el("tokenName").value.trim(),
          scopes: el("tokenScopes").value.split(","),
        }),
      });
      const anzeige = el("tokenAnzeige");
      anzeige.textContent = `Token für '${neu.name}' (einmalige Anzeige — jetzt kopieren): ${neu.token}`;
      anzeige.hidden = false;
      ereignis.target.reset();
      await tokensLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("tokenListe").addEventListener("click", async (ereignis) => {
    const knopf = ereignis.target.closest("[data-token-widerrufen]");
    if (!knopf) return;
    try {
      await api(`/api/admin/agent-tokens/${knopf.dataset.tokenWiderrufen}`, { method: "DELETE" });
      toast("Token widerrufen");
      await tokensLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("beitragListe").addEventListener("click", async (ereignis) => {
    const knopf = ereignis.target.closest("[data-beitrag]");
    if (!knopf) return;
    try {
      await api(`/api/admin/beitraege/${knopf.dataset.beitrag}/${knopf.dataset.entscheidung}`, {
        method: "POST",
      });
      toast(knopf.dataset.entscheidung === "uebernehmen" ? "Übernommen ✓" : "Verworfen");
      await beitraegeLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });

  el("nutzerFormular").addEventListener("submit", async (ereignis) => {
    ereignis.preventDefault();
    try {
      const neu = await api("/api/admin/nutzer", {
        method: "POST",
        body: JSON.stringify({
          anzeigename: el("neuName").value.trim(),
          pin: el("neuPin").value,
          rolle: el("neuRolle").value,
        }),
      });
      adminMeldung(`Nutzer '${neu.anzeigename}' angelegt.`);
      ereignis.target.reset();
      await nutzerLaden().catch(fehlerAnzeigen);
    } catch (fehler) {
      adminMeldung(fehler.message, true);
    }
  });
  el("nutzerListe").addEventListener("click", async (ereignis) => {
    const kiKnopf = ereignis.target.closest("[data-nutzer-ki]");
    const sichtbarKnopf = ereignis.target.closest("[data-nutzer-sichtbar]");
    const pinResetKnopf = ereignis.target.closest("[data-nutzer-pin]");
    const loeschKnopf = ereignis.target.closest("[data-nutzer-loeschen]");
    try {
      if (kiKnopf) {
        await api(`/api/admin/nutzer/${kiKnopf.dataset.nutzerKi}`, {
          method: "PATCH",
          body: JSON.stringify({ ki_freigeschaltet: kiKnopf.dataset.kiNeu === "1" }),
        });
        toast(kiKnopf.dataset.kiNeu === "1" ? "KI-Wertung freigeschaltet ✓" : "KI-Wertung entzogen");
        await nutzerLaden();
      } else if (sichtbarKnopf) {
        await api(`/api/admin/nutzer/${sichtbarKnopf.dataset.nutzerSichtbar}`, {
          method: "PATCH",
          body: JSON.stringify({ rangliste_sichtbar: sichtbarKnopf.dataset.sichtbarNeu === "1" }),
        });
        toast(
          sichtbarKnopf.dataset.sichtbarNeu === "1"
            ? "Konto zählt wieder in der Rangliste ✓"
            : "Konto aus Rangliste & Co. ausgeblendet"
        );
        await nutzerLaden();
      } else if (pinResetKnopf) {
        const neuePin = prompt(
          `Neue PIN für '${pinResetKnopf.dataset.nutzerName}' (4–32 Zeichen):`
        );
        if (!neuePin) return;
        await api(`/api/admin/nutzer/${pinResetKnopf.dataset.nutzerPin}`, {
          method: "PATCH",
          body: JSON.stringify({ pin: neuePin }),
        });
        toast("PIN geändert ✓");
      } else if (
        loeschKnopf &&
        confirm(
          `Nutzer '${loeschKnopf.dataset.nutzerName}' endgültig löschen? Tipps, Pins und Punkte gehen verloren.`
        )
      ) {
        await api(`/api/admin/nutzer/${loeschKnopf.dataset.nutzerLoeschen}`, { method: "DELETE" });
        toast("Nutzer gelöscht");
        await nutzerLaden();
      }
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("feedbackListe").addEventListener("click", async (ereignis) => {
    const umschalter = ereignis.target.closest("[data-feedback-umschalten]");
    const loescher = ereignis.target.closest("[data-feedback-loeschen]");
    try {
      if (umschalter) {
        await api(`/api/admin/feedback/${umschalter.dataset.feedbackUmschalten}/umschalten`, {
          method: "POST",
        });
        await adminFeedbackLaden();
      } else if (loescher && confirm("Meldung endgültig löschen?")) {
        await api(`/api/admin/feedback/${loescher.dataset.feedbackLoeschen}`, {
          method: "DELETE",
        });
        await adminFeedbackLaden();
      }
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("syncKnopf").addEventListener("click", () => syncStarten("ergebnisse"));
  el("stammdatenKnopf").addEventListener("click", () => syncStarten("stammdaten"));
  el("feedsAbrufKnopf").addEventListener("click", async () => {
    adminMeldung("News werden abgerufen …");
    try {
      const ergebnis = await api("/api/admin/feeds/abrufen", { method: "POST" });
      adminMeldung(`${ergebnis.neu} neue Einträge (${ergebnis.fehler} Fehler).`);
      await feedsLaden();
    } catch (fehler) {
      adminMeldung(fehler.message, true);
    }
  });

  el("feedFormular").addEventListener("submit", async (ereignis) => {
    ereignis.preventDefault();
    try {
      await api("/api/admin/feeds", {
        method: "POST",
        body: JSON.stringify({
          url: el("feedUrl").value.trim(),
          titel: el("feedTitel").value.trim() || null,
        }),
      });
      ereignis.target.reset();
      toast("Feed abonniert ✓");
      await feedsLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("feedListe").addEventListener("click", async (ereignis) => {
    const umschalten = ereignis.target.closest("[data-feed-umschalten]");
    const loeschen = ereignis.target.closest("[data-feed-loeschen]");
    try {
      if (umschalten) {
        await api(`/api/admin/feeds/${umschalten.dataset.feedUmschalten}/umschalten`, {
          method: "POST",
        });
        await feedsLaden();
      } else if (loeschen && confirm("Feed samt News-Einträgen löschen?")) {
        await api(`/api/admin/feeds/${loeschen.dataset.feedLoeschen}`, { method: "DELETE" });
        await feedsLaden();
      }
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });

  el("bonusFormular").addEventListener("submit", async (ereignis) => {
    ereignis.preventDefault();
    const lokal = el("bonusSchluss").value;
    if (!lokal) return;
    try {
      await api("/api/admin/bonusfragen", {
        method: "POST",
        body: JSON.stringify({
          frage: el("bonusFrage").value.trim(),
          typ: el("bonusTyp").value,
          punkte_wert: Number(el("bonusPunkte").value) || 10,
          einsendeschluss_utc: new Date(lokal).toISOString().replace(/\.\d{3}Z$/, "Z"),
        }),
      });
      ereignis.target.reset();
      el("bonusPunkte").value = 10;
      toast("Bonusfrage angelegt ✓");
      await Promise.all([adminBonusLaden(), bonusfragenLaden()]);
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
  el("adminBonusListe").addEventListener("click", (ereignis) => {
    const knopf = ereignis.target.closest("[data-bonus-aufloesen]");
    if (knopf) bonusAufloesenDialog(Number(knopf.dataset.bonusAufloesen), knopf.dataset.bonusTyp);
  });
  el("overrideListe").addEventListener("click", async (ereignis) => {
    const knopf = ereignis.target.closest("[data-override-aufheben]");
    if (!knopf) return;
    try {
      await api(`/api/admin/overrides/${knopf.dataset.overrideAufheben}`, { method: "DELETE" });
      toast("Override aufgehoben — der nächste Sync setzt den API-Stand.");
      await overridesLaden();
    } catch (fehler) {
      fehlerAnzeigen(fehler);
    }
  });
}

async function syncStarten(job) {
  adminMeldung("Sync läuft …");
  try {
    const ergebnis = await api(`/api/admin/sync/${job}`, { method: "POST" });
    adminMeldung(`Sync ${ergebnis.job}: ${ergebnis.detail}`);
    await Promise.all([spieleLaden(), syncStatusLaden()]);
  } catch (fehler) {
    adminMeldung(fehler.message, true);
  }
}

/* ---------- Start ---------- */

async function appStarten(zielAnsicht = "heute") {
  const kiSichtbar =
    zustand.nutzer.rolle === "admin" ||
    zustand.nutzer.rolle === "ki" ||
    Boolean(zustand.nutzer.ki_freigeschaltet);
  el("kontoInfo").textContent =
    `Angemeldet als ${zustand.nutzer.anzeigename} (${zustand.nutzer.rolle})` +
    (kiSichtbar ? " · KI-Wertung freigeschaltet" : "");
  el("adminBereich").hidden = zustand.nutzer.rolle !== "admin";
  await spieleLaden();
  zeigeAnsicht(zielAnsicht);
  liveVerbinden();
  wettbewerbeLaden().catch(() => {});
  pushStatusLaden().catch(() => {});
  // Push-Klick auf /#spiel-ID: passende Lupe öffnen
  const treffer = location.hash.match(/^#spiel-(\d+)$/);
  if (treffer) {
    spielLupeOeffnen(Number(treffer[1])).catch(() => {});
    history.replaceState(null, "", location.pathname);
  }
}

async function start() {
  el("loginFormular").addEventListener("submit", loginAbsenden);
  el("registrierFormular").addEventListener("submit", registrierenAbsenden);
  loginTabsEinrichten();
  el("wettbewerbKnopf").addEventListener("click", wettbewerbsWahlOeffnen);
  for (const knopf of document.querySelectorAll("#leiste button")) {
    knopf.addEventListener("click", () => zeigeAnsicht(knopf.dataset.view));
  }
  spieleEreignisse();
  heuteEreignisse();
  ranglisteEreignisse();
  bonusEreignisse();
  turnierEreignisse();
  teamsEreignisse();
  lupeEreignisse();
  mehrEreignisse();
  el("newsTeamFilter").addEventListener("change", (ereignis) => {
    zustand.newsTeam = ereignis.target.value;
    newsLaden().catch(fehlerAnzeigen);
  });
  el("newsTagLeiste").addEventListener("click", (ereignis) => {
    const chip = ereignis.target.closest(".tag-chip");
    if (!chip) return;
    zustand.newsTag = chip.dataset.tag;
    for (const anderer of el("newsTagLeiste").querySelectorAll(".tag-chip")) {
      anderer.classList.toggle("aktiv", anderer === chip);
    }
    newsLaden().catch(fehlerAnzeigen);
  });
  el("newsListe").addEventListener("click", (ereignis) => {
    const eintrag = ereignis.target.closest("[data-news-reader]");
    if (!eintrag) return;
    const item = zustand.newsItems[Number(eintrag.dataset.newsReader)];
    if (item) newsReaderOeffnen(item);
  });
  onboardingEreignisse();

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* PWA optional: App funktioniert auch ohne Service Worker */
    });
  }

  try {
    zustand.nutzer = await api("/api/me");
  } catch (fehler) {
    if (fehler.message === "Nicht angemeldet") return;
    offlineAnzeigen();
    return;
  }
  try {
    await appStarten();
  } catch {
    offlineAnzeigen();
  }
}

/* Freundlicher Fehlerzustand, wenn der Server nicht erreichbar ist */
function offlineAnzeigen() {
  for (const ansicht of ANSICHTEN) {
    el(`view-${ansicht}`).hidden = ansicht !== "heute";
  }
  el("leiste").hidden = true;
  el("heuteGruss").textContent = "Ups!";
  el("heuteDatum").textContent = "";
  el("heuteInhalt").innerHTML =
    emptyStateHtml(
      "error-offline",
      "Keine Verbindung",
      "Der Server ist gerade nicht erreichbar — prüf dein Netz und versuch es gleich nochmal."
    ) + '<button class="primaer breit" data-neuladen>Nochmal versuchen</button>';
  el("heuteInhalt")
    .querySelector("[data-neuladen]")
    .addEventListener("click", () => location.reload());
}

start();
