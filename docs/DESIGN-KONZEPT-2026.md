# Designkonzept „Nachtspiel" — UI/UX-Rework 2026

**Leitsatz (vom Nutzer):** *spielerisch, abgerundete Cards, nerdig,
apple-inspiriert, fließend, animiert.*

**Beschlossene Leitplanken (2026-06-11):**
- **Dark bleibt Standard** (kein Light Mode geplant).
- **Kompletter Neuanfang** — „Flutlicht v2" wird vollständig abgelöst
  (Farbwelt, Schrift, Branding). Bewährte Interaktionen (Bottom-Sheets mit
  Drag-to-close, Duell-Tipp-Eingabe) bleiben als Muster erhalten.
- **Asset-Stil:** 3D-Clay/Glas im Apple-Keynote-Look, generiert vom Nutzer
  in ChatGPT Image (Prompts in §7).
- **Kein Maskottchen** — Illustrationsmotive sind ausschließlich Objekte
  (Pokal, Ball, Glocke, Glaskugel, Stadion …).

Evidenzbasis: `docs/DESIGN-INSPIRATION.md` (18 katalogisierte Screens aus
theScore, FotMob, MLS, NBA + Konzept-Studien).

---

## 1. Designphilosophie

**„Nachtspiel"** = ein Stadion bei Nacht aus der Sicht eines Nerds: tiefe,
ruhige Dunkelheit, auf der wenige Dinge präzise leuchten — der Rasen, der
Pokal, die Live-Anzeige. Die App fühlt sich an wie ein natives iOS-Produkt:
großzügige abgerundete Karten, schwebende Glas-Ebenen, federnde Animationen,
Daten-Details in Mono-Schrift als bewusstes Nerd-Statement.

Vier Prinzipien (jede Designentscheidung muss mindestens eines erfüllen):

1. **Glas über Nacht** — UI-Ebenen sind Glasflächen über dunklem Grund
   (Tab-Bar, Header, Sheets mit Blur). Tiefe durch Material, nicht Linien.
2. **Eine Bühne pro Screen** — jeder Screen hat genau einen Hero (Live-Spiel,
   Pokal, Podium). Der Rest ordnet sich in Karten unter. (Vorbild TS-02,
   CON-01b, MLS-02.)
3. **Zahlen sind Stars** — Scores, Punkte, Countdowns in großer
   Display-Typo mit Tabellenziffern; Mikro-Daten (Minute, Quote, Form) in
   Mono. (Vorbild NBA-01.)
4. **Alles antwortet** — jede Berührung gibt eine federnde Reaktion;
   Zustandswechsel (Tor, Tipp gewertet) werden zelebriert, nie nur
   ausgetauscht. `prefers-reduced-motion` wird respektiert.

---

## 2. Foundations (Design-Tokens)

### 2.1 Farben

| Token | Wert | Verwendung |
|---|---|---|
| `--ns-bg` | `#0A0C10` | App-Hintergrund (Anthrazit, leichter Blaustich) |
| `--ns-surface-1` | `#14171D` | Karten |
| `--ns-surface-2` | `#1C2129` | Erhöhte Karten, Chips, Eingaben |
| `--ns-glass` | `rgba(20,23,29,.72)` + `backdrop-filter: blur(20px) saturate(160%)` | Tab-Bar, Sticky-Header, Sheets |
| `--ns-hairline` | `rgba(255,255,255,.08)` | Trenner (nie Vollton-Borders) |
| `--ns-text` | `#F2F4F7` | Primärtext |
| `--ns-text-2` | `#9BA3AF` | Sekundär/Meta |
| `--ns-green` | `#34E27A` | **Akzent „Laser-Rasen"**: Aktionen, aktive Tabs, Erfolg |
| `--ns-gold` | `#F5C84C` | Pokal, Podium, Volltreffer, Bonus |
| `--ns-red` | `#FF453A` | LIVE, Fehler, verpasster Tipp |
| `--ns-cyan` | `#46C8FF` | Info, Links, KI-Elemente |
| `--ns-aurora` | `linear-gradient(135deg,#34E27A,#46C8FF)` | Hero-Akzente, Fortschritt, aktiver Ring |

Regeln: Grün ist die einzige Handlungsfarbe. Gold nur für Erreichtes
(nie für Buttons). Rot nur live/negativ. Flächen bleiben neutral — Farbe
kommt aus Inhalten (Flaggen, Assets) und gezielten Akzenten.

### 2.2 Typografie

| Rolle | Schrift | Hinweise |
|---|---|---|
| UI/Fließtext | **System-Stack**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif` | Auf dem iPhone (PWA) = echtes SF Pro → „apple-inspiriert" ohne Ladezeit |
| Display/Zahlen | **Space Grotesk** (selbst gehostet, woff2, latin + latin-ext) | Scores, Countdowns, Punkte, Headlines; geometrisch-nerdig; `font-feature-settings: "tnum"` |
| Daten/Mikro | `ui-monospace, "SF Mono", "Cascadia Mono", monospace` | Spielminute, Formketten-Labels, Quoten, Stat-Achsen — das Nerd-Detail |

Archivo Variable wird ausgebaut (Fonts aus `app/static/fonts/` ersetzen,
SW-Precache anpassen).

### 2.3 Form & Layout

- **Radii:** 12 px (kleine Chips/Buttons) · 16 px (Listenzellen) ·
  **20 px (Standard-Karte)** · 28 px (Sheets, Hero-Karten) · 999 px (Pills,
  Toggles). Großzügig runden — „abgerundete Cards" ist Markenkern.
- **Raster:** 4-px-Basis; Seitenränder 16 px; Kartenabstand 12 px;
  Safe-Areas (`env(safe-area-inset-*)`) überall, dvh statt vh.
- **Karten:** Hintergrund `--ns-surface-1`, kein Border, Schatten
  `0 8px 24px rgba(0,0,0,.35)` nur für schwebende Elemente (Sheets,
  Tab-Bar). Innen-Padding 16 px.
- **Breakpoints:** unverändert 720/1050 (Desktop = 2-spaltige Listen),
  Mobile-first verbindlich (SPEC 6.5, Preview 375 px).

### 2.4 Motion

| Token | Wert | Einsatz |
|---|---|---|
| `--ns-ease-spring` | `cubic-bezier(.32,.72,0,1)` | Sheets, Tab-Wechsel, alles Große (350–500 ms) |
| `--ns-ease-out` | `cubic-bezier(.16,1,.3,1)` | Karten-Einzug, Chips (200–280 ms) |
| Mikro | 120–160 ms | Hover/Press (Scale .97), Toggle |
| Stagger | 35 ms pro Karte, max. 8 | Listen-Einzug (existiert, übernehmen) |

Signatur-Animationen:
- **Odometer-Score:** Torzahlen rollen vertikal beim Update (Live!).
- **Tipp-Plopp:** Nach Tipp-Abgabe federt die Karte (Scale 1→1.04→1) und
  das Häkchen zeichnet sich (SVG stroke-dashoffset).
- **Live-Puls:** roter Punkt mit weichem Doppel-Puls (existierende
  LIVE-Glut ersetzen).
- **Aurora-Drift:** sehr langsamer Gradient-Drift auf Hero-Flächen
  (Nachfolger des Flutlicht-Drifts).
- **Tab-Wechsel:** Crossfade + 8 px Lift, Inhalte staggern nach.
- Drag-to-close am Sheet-Griff **bleibt** (bewährt).

Alle Keyframes hinter `@media (prefers-reduced-motion: no-preference)`.

---

## 3. Navigationsarchitektur

**Schwebende Glas-Tab-Bar** (abgelöst vom Rand, Pill-Form, Blur — iOS-26-
Liquid-Glass-Anmutung; Nachfolger der Pill-Nav) mit 5 Zielen:

| Tab | Inhalt | Vorbilder |
|---|---|---|
| **Heute** | Dashboard: Live-Hero, Tipp-Status, nächste Spiele, News-Teaser, Quick-Access | CON-01b, CON-04b, TS-02 |
| **Spiele** | Spielplan mit Datums-Chips + Status-Filter, Gruppierung nach Gruppe/Runde, Inline-Tippen | FM-01, CON-04a |
| **Turnier** | Gruppen-Tabellen ⇄ K.o.-Baum (Tab-Paar) | CON-04c, FM-02 |
| **Rangliste** | Podium, Gesamt/Tag/Runde, Tipper-Profile | NBA-01-Muster |
| **Mehr** | News-Archiv, Bonusfragen, Regeln, Einstellungen, Admin | MLS-01 |

Zweite Ebene: **Segment-Tabs mit Unterstreichung** auf Listen-Seiten
(Datums-/Filterwahl), **Pill-Tabs** innerhalb der Spiel-Lupe (passt zur
runden Formsprache; CON-01c). Detail-Ansichten bleiben **Bottom-Sheets**
(Spiel/Team/Spieler) — das ist bereits ein Markenzeichen der App.

**Spiel-Lupe = zustandsabhängige Spielseite** (wichtigste Strukturänderung,
von allen 4 echten Apps validiert): Pill-Tabs `Tipps · Ticker · Teams ·
Statistik`, und je Phase ändert sich Hero + Karten-Reihenfolge:

- **Vor Anpfiff:** Countdown groß, Tipp-Eingabe als Hero, Tipp-Verteilung
  der Gruppe als Ring-Trio (CON-02 umgedeutet: „42 % tippen Sieg A"),
  Formketten, H2H.
- **Live:** Odometer-Score + Minute (Mono), Ticker zuerst, LIVE-Puls,
  Mini-Scroller „andere Spiele jetzt" (MLS-02).
- **Nach Abpfiff:** Endstand + Halbzeitstand, Punkte-Banner („Du: +3"),
  Statistik-Balkenpaare (NBA-01/CON-03), Spiel-News (Tag-Matching),
  „Top-Tipper des Spiels" als Personen-Karten.

Hero der Spiel-Lupe: dezenter Verlauf aus den **Nationalfarben beider
Teams** (MLS-02), darüber Wappen/Flaggen + Score in Space Grotesk.

---

## 4. Screen-Konzepte (Kurzfassung je Screen)

1. **Login/Welcome:** Branding-Bühne statt Formular (CON-01a): Asset
   `hero-login` (Pokal), App-Name, darunter Glas-Karte mit Name+PIN bzw.
   Tab „Registrieren". Ein CTA.
2. **Onboarding nach Registrierung (neu, 2 Schritte):**
   a) Push-Opt-in als Toggle-Liste (TS-01; Asset `hero-push`),
   b) Lieblingsteam-Wahl als Flaggen-Grid mit Suche (TS-05; Asset
   `hero-team-pick`). Beide überspringbar, vollbreiter CTA unten.
3. **Heute:** Begrüßung („Moin, Alex"), Live-/Nächstes-Spiel-Hero-Karte,
   Karte „Deine offenen Tipps (3)" mit Direkteinstieg, Quick-Access-Kacheln
   (Turnierbaum, Bonusfragen, Rangliste, News), News-Teaser-Karussell.
4. **Spiele:** Datums-Chips (horizontal) + Status-Segmente
   (Alle/Live/Anstehend/Beendet), Karten je Gruppe/Runde mit einklappbaren
   Headern, Spielzeile = Duell-Layout mit Inline-Tipp-Boxen (bleibt),
   Stadion + Anstoß als Mono-Meta.
5. **Turnier:** Segment „Gruppen ⇄ K.o.". Gruppen als Karten (CON-04c).
   Baum mit Zustands-Codierung (FM-02): aktuell = Aurora-Rahmen,
   ausgeschieden = durchgestrichen, offen = Platzhalter-Schild + Datum;
   Finale mit Asset `champion-placeholder`.
6. **Rangliste:** Podium-Bühne (Gold-Glow, Asset `podium`), darunter
   Liste mit Formketten der letzten 5 Tipp-Tage (CON-02-Muster auf Tipper
   übertragen), Segmente Gesamt/Tag/Runde.
7. **News:** Hero-Artikel + Karussell (TS-02), Tag-Chips (existieren);
   Artikel im Vollbild-Reader-Sheet (TS-03) mit „Aa"-Regler,
   Quellen-Footer; Fallback-Bild `news-fallback`.
8. **Mehr/Einstellungen:** Einstellungen als Bottom-Sheet mit
   Master-Toggle + Detail-Toggles (MLS-01-Muster für Push-Einstellungen).
9. **KI-Elemente (nur für freigeschaltete Profile):** Analysen/KI-Tipps
   bekommen Cyan-Akzent + Asset `ki-chip` als Sektions-Icon (abstrakt,
   kein Charakter). **KI-Gate (SPEC 5.4, Entscheidung 11.06.):**
   Standardprofile sind komplett KI-frei — ohne `ki_freigeschaltet`
   rendert die App keinerlei KI-Bestandteile: kein KI-Analyse-Tab, keine
   KI-Zeile in Ranglisten (Plätze ohne KI berechnet), keine KI-Tipps in
   Tipplisten, kein Cyan-Akzent, kein `ki-chip`. Serverseitig filtern
   (API liefert die Daten gar nicht erst aus), nicht nur per CSS
   verstecken; Layouts müssen ohne KI-Elemente lückenlos aussehen.
10. **Empty/Fehler-States:** jede Hauptliste bekommt ein Asset + einen
    spielerischen Einzeiler + ggf. Aktion (§7, `empty-*`).

---

## 5. Komponentenbibliothek (Umbau-Checkliste)

Karte · Hero-Karte · Glas-Tab-Bar · Segment-Tabs · Pill-Tabs · Chip ·
Status-Badge (LIVE/ENDE/OFFEN in Farbe, FM-01/CON-01b) · Duell-Zeile mit
Tipp-Boxen · Ring-Diagramm (Tipp-Verteilung) · Formkette (W/D/L-Punkte) ·
Statistik-Balkenpaar · Odometer-Zahl · Toggle (iOS-Maß 51×31) ·
Bottom-Sheet (bleibt) · Toast/Banner · Podium · Empty-State-Block.

---

## 6. Technische Leitplanken

- Reines CSS-Token-Update in `styles.css` (`:root`-Variablen `--ns-*`),
  kein Framework. `backdrop-filter` mit `@supports`-Fallback
  (deckende `--ns-surface-1`).
- CSP beachten: keine Inline-Skripte (boot.js-Muster), Animationen
  CSS-first.
- SW-Cache: neue Fonts + Assets in Precache, **Cache-Version bumpen**
  (sw.js `VERSION` + alle `?v=` in index.html + boot.js).
- Assets: WebP, Ziel ≤ 150 KB je Illustration (Rock64!), `loading="lazy"`
  außer Login-Hero. Ablage `app/static/illustrationen/`.
- Rechtliches: kein FIFA-Logo, keine echte WM-Pokal-Silhouette, keine
  realen Personen in Assets — nur generische/abstrakte Motive (steht so
  in den Prompts).

---

## 7. Asset-Plan für ChatGPT Image

### 7.1 Arbeitsweise (wichtig für Konsistenz)

1. **Alle Assets in EINER ChatGPT-Session** generieren, Reihenfolge wie
   unten (Referenz-Asset zuerst).
2. Jeder Prompt beginnt mit dem **identischen Style-Block** (unten) —
   wortgleich kopieren, nie umformulieren.
3. Ab dem zweiten Asset zusätzlich anhängen:
   *"Match the material, lighting and color grading of the previous
   image exactly."*
4. **Format in ChatGPT forcen** (Formatauswahl beim Generieren) — pro
   Asset steht es in der Überschrift. Kurzregel: Objekte/Empty States =
   **Square 1:1**, Onboarding-Heroes = **Portrait 3:4**, Login-Vollbild =
   **Story 9:16**, breite Fallback-Bilder = **Widescreen 16:9**
   (Landscape 4:3 brauchen wir nicht).
5. Objekt-Assets mit **transparentem Hintergrund** anfordern (steht im
   jeweiligen Prompt); Szenen-Assets bekommen den dunklen Studiogrund.
6. PNGs einfach in `Project26/assets-roh/` ablegen — Konvertierung
   (WebP, Größen, Benennung) übernehme ich.
7. Wenn ein Ergebnis stilistisch ausreißt: *"Regenerate, keep the exact
   same style, only change: …"*

### 7.2 Der gemeinsame Style-Block (vor jeden Prompt kopieren)

```text
Premium 3D render in a soft matte clay style with frosted-glass and
polished-metal accents. Apple-keynote product-shot aesthetic: a single
centered object floating in space, dramatic soft studio rim light from
the upper left, gentle emerald-green (#34E27A) and warm gold (#F5C84C)
glow reflections, rounded playful proportions, crisp details, subtle
depth of field. No text, no letters, no numbers, no logos, no watermark,
no people.
```

### 7.3 Die Assets (17 Stück)

**Block A — Marke & Onboarding**

**A1 · `icon-app` — App-Icon (Format: Square 1:1, mit Hintergrund)**
> [Style-Block] A glossy soccer ball made of dark frosted glass with
> softly rounded clay pentagon panels; exactly one panel glows emerald
> green from within. The ball sits centered on a deep charcoal (#0A0C10)
> rounded-square background with a very faint emerald-to-cyan aurora
> gradient in the top corner. Composition safe for an app icon: the ball
> fills about 70% of the canvas, fully centered.

**A2 · `hero-login` — Login-Bühne (Format: Story 9:16, dunkler Grund — Vollbild-Hintergrund)**
> [Style-Block] An abstract tall golden trophy with a smooth, rounded,
> minimalist silhouette (NOT the real World Cup trophy), standing on a
> small round glass pedestal. Tiny specks of golden confetti float
> around it. Background: deep charcoal (#0A0C10) studio with a faint
> emerald-to-cyan aurora gradient rising behind the trophy. Lots of
> empty dark space in the lower third for UI elements.

**A3 · `hero-push` — Push-Opt-in (Format: Portrait 3:4, dunkler Grund — sitzt oben über der Toggle-Liste)**
> [Style-Block] A frosted-glass notification bell with a small glowing
> emerald dot on its top right; a tiny clay soccer ball orbits the bell
> on a thin glowing ring, like a planet. Background: deep charcoal
> (#0A0C10) with faint aurora gradient, comfortable dark margins around
> the object.

**A4 · `hero-team-pick` — Lieblingsteam-Wahl (Format: Portrait 3:4, dunkler Grund — sitzt oben über dem Flaggen-Grid)**
> [Style-Block] Three blank rounded shield badges made of dark frosted
> glass floating in a loose stack; the front shield glows with a soft
> emerald rim and has a small gold star above it. The shields are
> completely blank (no emblems). Background: deep charcoal (#0A0C10)
> with faint aurora gradient, comfortable dark margins around the
> objects.

**Block B — Empty States (alle Format: Square 1:1, transparent)**

**B1 · `empty-no-matches` — „Heute kein Spiel"**
> [Style-Block] A matte clay soccer ball sleeping on a tiny rounded
> podium, with a small frosted-glass crescent moon floating above it.
> Cozy, calm mood. Render on a fully transparent background (PNG alpha)
> with only a soft contact shadow under the objects.

**B2 · `empty-no-live` — „Gerade kein Live-Spiel"**
> [Style-Block] A stylized stadium floodlight mast made of clay and
> glass, switched off, gently leaning; a single tiny emerald standby LED
> glows at its base. Render on a fully transparent background (PNG
> alpha) with only a soft contact shadow.

**B3 · `empty-no-news` — „Noch keine News"**
> [Style-Block] A cute retro radio made of matte clay with a frosted
> glass front panel and a thin antenna; a tiny emerald signal wave just
> starting to appear above the antenna. Render on a fully transparent
> background (PNG alpha) with only a soft contact shadow.

**B4 · `empty-no-tipps` — „Noch keine Tipps"**
> [Style-Block] A crystal ball made of frosted glass on a small clay
> base; inside the sphere a tiny soccer ball floats in soft emerald
> mist. Mystical but playful. Render on a fully transparent background
> (PNG alpha) with only a soft contact shadow.

**B5 · `error-offline` — Offline/Fehler**
> [Style-Block] A small clay satellite dish with a loosely knotted,
> unplugged glowing cable lying in front of it; one tiny red status
> light. Friendly, not dramatic. Render on a fully transparent
> background (PNG alpha) with only a soft contact shadow.

**Block C — Erfolgs- & Spielmomente (alle Format: Square 1:1, transparent)**

**C1 · `success-tipp` — Tipp abgegeben**
> [Style-Block] A matte clay soccer ball with one frosted-glass panel
> showing an embossed glowing emerald checkmark; a few tiny confetti
> chips of glass float around the ball. Render on a fully transparent
> background (PNG alpha) with only a soft contact shadow.

**C2 · `volltreffer` — Exakter Tipp (4 Punkte)**
> [Style-Block] A round dartboard target made of dark clay rings with a
> golden bullseye; a tiny glass soccer ball sits exactly in the center,
> radiating a warm gold glow. Render on a fully transparent background
> (PNG alpha) with only a soft contact shadow.

**C3 · `podium` — Rangliste/Podium**
> [Style-Block] A three-step rounded podium made of dark matte clay;
> above the steps float three medals made of frosted glass in gold,
> silver and bronze, the gold one slightly higher and glowing. Render on
> a fully transparent background (PNG alpha) with only a soft contact
> shadow.

**C4 · `bonus-question` — Bonusfragen**
> [Style-Block] A rounded dice-like cube made of frosted glass, hovering
> and slightly tilted, with softly embossed glowing question-mark
> symbols on its faces (abstract symbol shapes, not typed text). Render
> on a fully transparent background (PNG alpha) with only a soft
> contact shadow.

**C5 · `champion-placeholder` — K.o.-Baum-Finale**
> [Style-Block] An abstract golden trophy silhouette rendered as
> translucent hologram glass, semi-transparent and faintly glowing, with
> a soft emerald question-mark shape floating inside it (abstract symbol
> shape, not typed text). Render on a fully transparent background (PNG
> alpha) with only a soft contact shadow.

**Block D — Flächen & Fallbacks**

**D1 · `ki-chip` — KI-Sektionen (Format: Square 1:1, transparent)**
> [Style-Block] A small square microchip made of dark frosted glass with
> rounded corners; thin glowing cyan (#46C8FF) circuit traces run across
> its surface and pulse softly. Abstract, no face, no character. Render
> on a fully transparent background (PNG alpha) with only a soft contact
> shadow.

**D2 · `stadium-hero` — Spiel-Lupe-Fallback (Format: Widescreen 16:9, dunkler Grund)**
> [Style-Block] A miniature stadium bowl made of matte clay seen from a
> high angle at night, the pitch glowing soft emerald green, tiny
> floodlights as glass details, deep charcoal (#0A0C10) surroundings
> with faint aurora gradient on the horizon. Wide cinematic composition
> with empty dark space on the left third.

**D3 · `news-fallback` — Artikel ohne Bild (Format: Widescreen 16:9, dunkler Grund)**
> [Style-Block] A folded newspaper made of matte clay with frosted-glass
> edges and a small glass magnifier resting on top; blank pages (no
> readable text). Background: deep charcoal (#0A0C10) with faint aurora
> gradient. Wide composition, object on the right half.

### 7.4 Nachbearbeitung (Stand 11.06., umgesetzt)

- **Befund Alpha-Transparenz:** ChatGPT hat bei ALLEN Objekt-Assets
  (B1–B5, C1–C5, D1) die Transparenz ignoriert und stattdessen ein
  Schachbrettmuster (~24-px-Kacheln, #F8/#FE) eingebacken. Entscheidung:
  Freistellen per **rembg** (Modell `isnet-general-use`), Ergebnis auf
  #0A0C10 sichtgeprüft (kein Saum, Glows bleiben weich). Szenen-Assets
  (A1–A4, D2, D3) kamen korrekt mit dunklem Studiogrund.
- Pipeline-Skript: `tools/assets_konvertieren.py` (reproduzierbar,
  Qualitäts-Sweep bis ≤ 150 KB, WebP method=6).
- PNG → WebP (Illustrationen ~800 px Kante, Heroes 1200 px), alle
  16 Illustrationen 36–106 KB; Ablage `app/static/illustrationen/`.
- `icon-app` (A1): 512/192/180er-PNGs erzeugt (Manifest + Apple-Touch),
  schwarze Ecken außerhalb der Kachel mit #0A0C10 geflutet; Motiv ~70 %
  zentriert → maskable-tauglich. Altes Flutlicht-`icon.svg` entfernt.
- SW-Precache um alle Illustrationen erweitert (Empty States müssen
  offline verfügbar sein) + Cache-Version-Bump.

---

## 7.5 Umsetzungs-Notizen (Entscheidungen im Geist des Leitsatzes)

Während der Umsetzung getroffene Detail-Entscheidungen, wo das Konzept
Lücken ließ:

- **Spiele-Statusfilter:** Die vier Status-Segmente (CON-04a) sind um ein
  „★"-Segment ergänzt — der Gepinnt-Filter ist Bestandsfunktion (SPEC 5.1)
  und bleibt erhalten.
- **Teams & News-Archiv** leben unter „Mehr" (Karte „Entdecken"); der
  Mehr-Tab bleibt dabei aktiv markiert. Frische News erscheinen als
  Teaser-Karussell auf „Heute" (TS-02).
- **Quick-Kachel „Bonusfragen"** springt zur Rangliste und scrollt zur
  Bonus-Sektion (Bonusfragen wohnen dort, SPEC 5.4).
- **Spiel-Lupe „Statistik":** Der Free-Tier liefert keine Spielstatistiken
  (Ballbesitz etc.) — der Statistik-Tab zeigt stattdessen Tippspiel-
  Statistik (Verteilungs-Balken, Durchschnittstipp), die H2H-Bilanz als
  Balkenpaar, „Top-Tipper des Spiels" (NBA-01-Muster) und Team-News.
- **Teamfarben-Hero (MLS-02):** Hauptfarbe je Nation als JS-Konstante
  (FIFA-Code → Hex), Verlauf wird per color-mix stark auf die dunkle
  Fläche gedimmt; Fallback ist der Aurora-Verlauf.
- **Tipp-Verteilung** kommt serverseitig aggregiert (`tipp_verteilung` im
  Spiel-Detail) — verrät keine Einzeltipps vor Anpfiff und respektiert
  das KI-Gate; ebenso die Formketten (`form`, S/U/N, neueste zuerst).

## 8. Umsetzungs-Etappen (Vorschlag)

0. **KI-Gate (vorgezogen, designunabhängig):** Backend filtert den
   KI-Tipper für nicht freigeschaltete Profile aus Ranglisten (Plätze
   aufrücken), Tipplisten und Spiel-Lupe; Frontend rendert KI-Sektionen
   nur bei Flag aus `/api/me`-Kontext. Default bleibt aus
   (`ki_freigeschaltet=0`). Tests für beide Sichtbarkeits-Welten.
1. **Foundations:** Tokens, Fonts (Space Grotesk rein, Archivo raus),
   Karten/Radii/Hairlines, Glas-Tab-Bar — App sieht sofort neu aus.
2. **Heute-Tab** (neu) + Spieleliste-Umbau (Datums-Chips, Status-Filter,
   Badges).
3. **Spiel-Lupe**: Pill-Tabs + Phasen-Logik, Ring-Verteilung, Formketten,
   Statistik-Balken, Odometer.
4. **Turnier + Rangliste**: Baum-Zustände, Podium-Bühne.
5. **Onboarding + Empty States + Assets** einbauen, Politur, SW-Bump,
   Deploy aufs Rock64.

Jede Etappe: mobil zuerst (375 px), Tests, CodeRabbit, Commit (deutsch).
