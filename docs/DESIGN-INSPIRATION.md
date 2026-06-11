# Design-Inspiration — UI/UX-Rework 2026 (mobile-first, iOS-nah)

Katalog der vom Nutzer gesammelten Design-Screenshots (Quelle: Mobbin u. a.).
Ziel: professionellerer, iOS-näherer UI-Flow für die WM26-App. Screenshots
werden hier nach **abgebildetem Inhalt** sortiert; pro Eintrag: Quelle-App,
Beschreibung, beobachtete UI-Muster, möglicher Bezug zur WM26-App.

IDs: Kürzel der Quell-App + laufende Nummer (z. B. TS-01 = theScore, Nr. 1).
**Konzept-Studien** (Dribbble/Behance, keine gelaunchten Apps) laufen unter
`CON-…`; Shots mit mehreren Screens werden je Screen als a/b/c geführt.
Konzepte sind visuell stark, aber UX-ungeprüft — gute Stil-Quelle, Flows
lieber an den echten Apps (theScore, FotMob, MLS, NBA) validieren.

---

## Kategorie A — Onboarding & Personalisierung

### TS-01 · theScore — Push-Benachrichtigungen wählen („Never miss a game")
- **Inhalt:** Onboarding-Schritt für Benachrichtigungs-Präferenzen. Oben
  schwebende Team-Logo-Bubbles + Beispiel-Push-Karte („Final Score …"),
  zentrierte Headline + erklärende Subline, darunter Liste mit iOS-Toggles
  (Breaking News, Game Start, Game End, Live Activities, Scoring), unten
  vollbreiter Primär-Button „Continue".
- **UI-Muster:** Dark Theme; Toggle-Liste mit Hairline-Trennern; großzügiger
  Weißraum; ein einziger klarer CTA unten (Safe-Area); spielerische
  Illustration oben statt Stock-Grafik (echte Logos + echte Beispiel-Push).
- **WM26-Bezug:** Push-Opt-in nach Registrierung/Login — statt Einstellungs-
  Seite ein dedizierter Onboarding-Schritt mit Toggles (Anpfiff, Tore,
  Endstand, News, Erinnerung „noch tippen!").

### TS-05 · theScore — Lieblingsteams wählen („Choose your favorite teams")
- **Inhalt:** Onboarding-Schritt Favoriten-Auswahl. Oben Zurück-Pfeil +
  horizontale Reihe runder Sport-/Liga-Icons (Stern, PGA, ATP, WNBA, UCL,
  UEL), Headline + Subline, Suchfeld, horizontale Filter-Tabs
  (RECOMMENDED / WNBA / UCL / …), darunter 4-spaltiges Grid aus Team-Wappen
  mit Kürzel-Label, unten vollbreiter „Continue"-Button.
- **UI-Muster:** Auswahl als visuelles Logo-Grid statt Textliste; zweistufige
  Filterung (Icon-Reihe oben + Text-Tabs); Suche als Fallback; CTA fixiert
  unten.
- **WM26-Bezug:** Lieblingsteam(s) für die WM wählen (Priorisierung in
  Spieleliste/Push), perspektivisch Bundesliga-Teamwahl. Flaggen-Grid statt
  Dropdown.

### MLS-01 · MLS — Club-Benachrichtigungen (Bottom-Sheet auf Teamseite)
- **Inhalt:** Teamseite Inter Miami CF (abgedunkeltes Hero-Foto, Wappen, großer
  Teamname) mit darüber liegendem Bottom-Sheet „Club Notifications" + „Done".
  Oben hervorgehobene Karte „All Notifications" als Master-Toggle, darunter
  Einzel-Toggles: Breaking News, Promos & Offers, Kickoff/Half time/Full time,
  Goals, Goal Highlights, Lineup, … (scrollbar).
- **UI-Muster:** Kontextuelle Einstellungen als Sheet statt eigener Settings-
  Seite; Master-Toggle visuell abgesetzt (eigene Karte) über den Detail-
  Toggles; Hintergrund bleibt sichtbar (Kontext bleibt erhalten); „Done"
  statt X.
- **WM26-Bezug:** Push-Einstellungen pro Lieblingsteam oder pro Spiel direkt
  aus der Team-/Spiel-Lupe heraus — passt zum bestehenden Bottom-Sheet-Muster
  der App. Master-Toggle „Alle Benachrichtigungen" + Einzelschalter
  (Anpfiff/Tore/Endstand/Aufstellung).

### CON-01a · Konzept „FIFA World Cup Mexico 2026" — Splash/Welcome
- **Inhalt:** Dunkler oliv-grüner Splash-Screen: goldene WM-Trophäe vor
  riesiger „26" in Grün-Rot-Verlauf, Titel „FIFA WORLD CUP MEXICO 2026",
  kurzer Intro-Text, runder grüner Weiter-Button.
- **UI-Muster:** Turnier-Branding als eigener Willkommens-Moment; Verlaufs-
  Display-Typo als Markenelement; genau ein CTA.
- **WM26-Bezug:** Login-/Welcome-Screen als Branding-Moment aufwerten
  (Trophäe/„26"-Motiv, Flutlicht-Stimmung) statt direkt ins Formular zu
  springen.

---

## Kategorie B — Home / Dashboard / Feed

### TS-02 · theScore — News-Tab (Feed-Startseite)
- **Inhalt:** News-Home. Top-Bar: Profil-Avatar links, zentrierter Titel
  „News", Suche + Glocke rechts. Darunter horizontal scrollbare Liga-Tabs
  (TOP, PGA, ATP, WNBA, UCL, UEL, NFL, NHL …) mit Unterstreichungs-Indikator.
  Hero-Karte „FEATURED" (vollflächiges Bild, große Headline als Overlay),
  darunter horizontales Karussell aus Artikel-Karten (Bild, Headline, Meta
  „10h ago · 5m read"), dann Sektion „Latest — sorted by most recent stories".
  Unten 5er-Tab-Bar: News, Scores, Favorites, Discover, Leagues.
- **UI-Muster:** Klassische iOS-Informationsarchitektur (Top-Bar + Segment-Tabs
  + Bottom-Tab-Bar); Hero + Karussell + chronologische Liste als
  Feed-Hierarchie; Meta-Zeile mit Lesedauer; aktiver Tab blau.
- **WM26-Bezug:** News-Ansicht (RSS + Tags) — Hero-Artikel oben, Tag-Filter
  als scrollbare Tabs, Karussell für Top-Meldungen; generell Vorbild für die
  Bottom-Tab-Navigation der App.

### CON-01b · Konzept „FIFA World Cup Mexico 2026" — Home mit Live-Hero
- **Inhalt:** Begrüßung „Welcome back, Christopher Jack" mit Avatar + Suche.
  Horizontaler Datums-Streifen (Mon 12 … Sun 18, aktiver Tag als grüner
  Chip). Sektion „Live Match" + „View All": große Live-Karte (Badge
  „Live – Group A", Stadion-/Spielerfotos, Flaggen, 1:1, Spielminute),
  Pagination-Dots. Sektion „Today Match" mit Filter-Chips (FIFA aktiv grün,
  UEFA, ECC, EFC, Women); Spielkarten mit Header „FIFA – Day 12", Flaggen,
  Score und Status-Badge (**ENDED** rot / **LIVE** grün).
- **UI-Muster:** Persönliche Begrüßung; Datums-Chips; Live-Spiel als
  Hero-Karussell; Spielstatus als farbige Badges statt Text.
- **WM26-Bezug:** „Heute"-Ansicht mit dem Live-Spiel als Hero-Karte oben;
  Status-Badges auch fürs Tippspiel nutzbar (offen / live / gewertet).

### CON-04b · Konzept „WC26" — Home-Dashboard mit Quick Access
- **Inhalt:** Top-Bar: Hamburger, grünes „WC26"-Logo, Glocke. Hero-Karte
  Turnier-Branding (Trophäe, „FIFA WORLD CUP 2026 — United States, Canada,
  Mexico", Claim „One World, One Game, One Dream"). Reihe „Quick Access"
  mit vier Icon-Kacheln (Matches, Groups, Teams, Tickets). Sektion „Today's
  Matches" + „View All": Karten mit Flaggen, Anstoßzeit groß mittig,
  Stadion-Name. Bottom-Nav: Home, Matches, News, Favourite, More.
- **UI-Muster:** Quick-Access-Kacheln als flache zweite Navigationsebene;
  Turnier-Hero oben; Anstoßzeit als größtes Element der Spielkarte.
- **WM26-Bezug:** Funktionen aus „Mehr" (Turnierbaum, Rangliste, Bonusfragen,
  Regeln) als Quick-Access-Kacheln auf die Startseite holen — kürzere Wege,
  bessere Entdeckbarkeit.

---

## Kategorie C — Content-Detail / Artikel-Reader

### TS-03 · theScore — Artikel-Detailansicht
- **Inhalt:** Vollbild-Reader. Hero-Bild oben mit Overlay-Buttons: X (schließen)
  links, „Aa" (Textgröße) rechts. Große Headline, Autoren-Zeile (Avatar, Name,
  „10h ago · 1m read", Bildcredit rechts), kursiver Lead-Absatz, Fließtext mit
  unterstrichenen Links. Sticky Bottom-Bar: Reaktions-Emojis + Zähler,
  Reaktion-hinzufügen, Kommentar-Zähler (226), „…"-Menü.
- **UI-Muster:** Reader als modales Vollbild (X statt Back); Typo-Hierarchie
  Headline → Meta → Lead → Body; Engagement-Leiste unten angepinnt;
  Schriftgrößen-Steuerung.
- **WM26-Bezug:** News-Artikel-Ansicht (heute Link nach außen / einfache
  Darstellung) → könnte ein In-App-Reader im Bottom-Sheet-/Vollbild-Stil
  werden. Reaktions-Leiste passt zum Gemeinschafts-Charakter (Tippspiel).

---

## Kategorie D — Spiel-Detail (Match Detail)

### TS-04 · theScore — Spielseite „RMD V ARS" (Tab Scorecast)
- **Inhalt:** Spiel-Detailseite. Top-Bar: Zurück-Pfeil, zentrierter Titel
  „RMD V ARS", Teilen-Icon. Sub-Tabs: FEED / CHAT (grüner Live-Punkt) /
  SCORECAST / LINEUPS / STATS mit Unterstreichung. Blaues Kontext-Banner:
  „Quarter-Finals" + „ARS win on aggregate 5-1". Score-Header: Wappen links/
  rechts mit Bilanz (5-0-3, 11th UCL / 6-1-1, 3rd UCL), mittig groß „1 Full
  Time 2". Sektionen: „Clips" (horizontale Video-Thumbnails mit Play-Overlay,
  Caption + Quelle + Zeit), „Table" mit „Full Table"-Link — Mini-Tabelle nur
  mit den zwei beteiligten Teams (GP, W-D-L, P, GD). Bottom-Tab-Bar bleibt.
- **UI-Muster:** Spielseite als eigener Navigationsraum mit Sub-Tabs;
  Kontext-Banner für Wettbewerbsphase/Aggregat; Score-Header mit Form/Rang
  unter den Wappen; Mini-Tabelle als Auszug mit Link zur Voll-Tabelle;
  horizontale Medien-Karussells.
- **WM26-Bezug:** Direktes Vorbild für die Spiel-Lupe: Sub-Tabs statt langem
  Scroll (z. B. Ticker / Tipps / Teams / Statistik), Gruppenphasen-Banner,
  Mini-Gruppentabelle mit beiden Teams + Link zur vollen Tabelle.

---

### FM-02 · FotMob — Spielseite, Tab „Knockout" (Turnierbaum)
- **Inhalt:** Spiel-Detailseite vor Anpfiff (MLS-Playoffs). Header: Zurück,
  Teilen/Glocke/Stern (favorisieren). Chip „2nd leg", Wappen links/rechts,
  große Anstoßzeit „10:45 AM", Live-Countdown „00:06:29". Sub-Tabs:
  Commentary / Lineup / **Knockout** / Stats / H2H. Darunter vertikaler
  K.o.-Baum: Achtelfinal-Karten mit Aggregat-Ergebnis (ausgeschiedene Teams
  **durchgestrichen**), aktuelles Spiel mit grünem Rahmen markiert,
  TBD-Platzhalter (graue Schilde) mit Datum, Badge „FINAL", Pokal-Icon
  „CHAMPION" mit „?" als Platzhalter.
- **UI-Muster:** Bracket als scrollbarer Baum innerhalb der Spielseite;
  Zustands-Codierung: aktiv = farbiger Rahmen, ausgeschieden =
  durchgestrichen, offen = graue Platzhalter + Termin; Countdown direkt im
  Score-Header.
- **WM26-Bezug:** Sehr nah am vorhandenen Turnier-Tab (K.o.-Baum wischbar,
  Ausgeschieden-Ableitung existiert schon). Ideen: aktuelles/angeschautes
  Spiel im Baum hervorheben, Baum auch aus der Spiel-Lupe erreichbar machen,
  Champion-Platzhalter mit „?" bis zum Finale.

### NBA-01 · NBA — Spielseite, Tab „Summary" (Statistik nach Abpfiff)
- **Inhalt:** Spiel-Detail nach Spielende. Schwarzer Kompakt-Header: Zurück,
  TOR-Logo 104 / „FINAL ▶" / 114 MIA-Logo, Cast-Icon; darunter Auswahlfeld
  „Game Recap" (Video/Audio). Sub-Tabs: **Summary** (gelbe Unterstreichung) /
  Box Score / Insights / Highlights. Sektion „TOP PERFORMERS": Spielerkarten
  (Foto, Name, Team | Nr. | Position) mit großen Kennzahlen (PTS/REB/AST).
  Sektion „TEAM COMPARISON": 2-spaltiges Karten-Grid (Field Goals,
  3-Pointers, Free Throws, Assists, …) mit Wert + horizontalem Vergleichs-
  Balken pro Team.
- **UI-Muster:** Score dauerhaft kompakt im Header (bleibt beim Scrollen
  präsent); „Top Performers" als Gesichter statt Tabellenzeilen; Stat-
  Vergleich als Balkenpaare in kleinen Karten; kräftige Display-Typo für
  Zahlen.
- **WM26-Bezug:** Statistik-Bereich der Spiel-Lupe nach Abpfiff:
  Team-Vergleichsbalken (Ballbesitz, Schüsse, Ecken — soweit API liefert),
  „Top-Tipper des Spiels" als Personen-Karten im Top-Performers-Stil.

### MLS-02 · MLS — Spielseite „Overview" mit Team-Farben-Hero
- **Inhalt:** Spiel-Detail nach Abpfiff (NSH 3:0 MTL). Ganz oben horizontaler
  Scroller mit Mini-Score-Karten der anderen Spiele des Tages. Hero-Header
  mit diagonalem Verlauf in den **Teamfarben** (gelb → blau): Zurück +
  Teilen, „Regular Season · Sun, 23 Mar 2025", Wappen + große Ziffern
  3 / Pill „Full-time" / 0, Spielort mit Pin („GEODIS Park"), Torschützenliste
  mittig (Pérez 67', Bauer 62', Muyl 56' + Ball-Icon), großer Button
  „Watch on Apple TV". Sub-Tabs: **Overview** / Videos / Play-By-Play /
  Lineups. Sektion „Highlights" mit Video-Karten (Dauer-Badge).
- **UI-Muster:** Teamfarben als Hero-Gradient (jedes Spiel sieht anders aus);
  Quer-Navigation zu Parallel-Spielen oben; Torschützen direkt im Header;
  ein dominanter Medien-CTA; Sub-Tabs darunter.
- **WM26-Bezug:** Spiel-Lupe-Header mit Verlauf aus den Nationalfarben
  beider Teams (Flaggen-Akzente gibt es schon); Torschützen in den Header,
  sobald OpenLigaDB/API Minuten liefert; Mini-Scroller „andere Spiele heute"
  oben in der Lupe.

### CON-01c · Konzept „FIFA World Cup Mexico 2026" — Aufstellung auf Rasen
- **Inhalt:** Spiel-Detail, Titel „FIFA World Cup", Zurück + Stern. Hero-
  Karte mit rotem Live-Badge, Stadionfoto, Flaggen Frankreich 1:1 Portugal,
  Minute „90+4". Darunter **Pill-Tabs**: Line-ups (aktiv grün) / Stats /
  Summary / H2H. Hauptfläche: Rasen-Ansicht mit **Spielerfotos in
  Formation**, Namen unter den Köpfen (Lloris; Pavard, Varane, Umtiti,
  Hernandez; Pogba, Kanté; Mbappé, Griezmann, Matuidi).
- **UI-Muster:** Tabs als Pills statt Unterstreichung; Aufstellung als
  Spielfeld mit echten Gesichtern statt Punkten/Trikots.
- **WM26-Bezug:** Das SVG-Kader-Spielfeld existiert bereits — Ausbau mit
  Spielerfotos (TheSportsDB ist im Backlog notiert). Pill-Tabs passen zur
  vorhandenen Pill-Nav-Designsprache.

### CON-02 · Konzept (UCL-Familie) — Spielseite vor Anpfiff: Prognose & Form
- **Inhalt:** Dunkle Spielseite „UEFA Champions League / Group stage ·
  Matchday 2 of 6". Wappen Barcelona/PSG, mittig „8 Aug 14:00 / Not
  started", darunter Lesezeichen-Icon, Pill „1X2", Teilen. Karten:
  „WHO WILL WIN?" mit drei **Ring-Diagrammen** (W 32 % / D 35 % / L 33 %);
  „LAST 5 GAMES" mit **Formketten** (W/D/L als grüne/graue/rote Kreise),
  Quote „2/5" und Tabellenplatz „5th›"; Sektion „HEAD TO HEAD" mit
  Ergebnis-Chips. „SEE ALL"-Links pro Karte.
- **UI-Muster:** Prognose als Ringe; Form der letzten 5 Spiele als
  Farbkette; Karten-Sektionen mit konsistentem Header (Titel + SEE ALL).
- **WM26-Bezug:** Prognose-Balken existiert — Ringe als Alternative.
  Formkette wäre eine neue, kompakte Info in der Spiel-Lupe. „Who will
  win?" ließe sich als **Tipp-Verteilung der Gruppe** umdeuten (X % tippen
  Sieg A / Remis / Sieg B) — passt perfekt zum Tippspiel.

### CON-03 · Konzept (UCL-Familie) — Spielseite nach Abpfiff: News & Statistik
- **Inhalt:** Gleiche Designfamilie wie CON-02, Zustand nach Spielende:
  großes „3 : 0" mit Halbzeitstand „(1 – 0)" und „Match end"; Buttons jetzt
  „VIDEO" + „1X2". Karten: „LAST NEWS" (Artikel-Karte mit Headline,
  Quelle „Daily Mail", Zeit, Thumbnail); „STATISTICS" mit **gegenläufigen
  Balkenpaaren** (Attacks 25 %/18 %, Passing, Shooting, Yellow cards 11/7),
  Werte außen, Balken zur Mitte.
- **UI-Muster:** Zustandsabhängiger Header (Aktionen wechseln je
  Spielphase: vorher Wett-Quote, nachher Video); spielbezogene News direkt
  auf der Spielseite; Statistik-Balkenpaare.
- **WM26-Bezug:** Spiel-Lupe-Zustände (vor / live / nach) klarer
  differenzieren — andere Aktionen und Karten-Reihenfolge je Phase. News
  per Tag-Matching (existiert) direkt in die Spiel-Lupe einblenden.

---

## Kategorie E — Scores / Spiellisten

### FM-01 · FotMob — Matches-Startseite (Tagesliste, Light Theme)
- **Inhalt:** Spieltags-Übersicht. Top-Bar: FotMob-Logo links, Icons Uhr
  (Zeitachse), Suche, Kalender mit Tageszahl „4". Horizontale Datums-Tabs
  (2 Nov / Yesterday / **Today** / Tomorrow / Thu) mit Unterstreichung.
  Spiele **nach Wettbewerb gruppiert** in Karten: Gruppen-Header mit
  Landesflagge + „Land – Liga – Runde" + Einklapp-Chevron; Zeilen mit
  Teamnamen außen, Wappen + Ergebnis/Anstoßzeit mittig, FT-Badge links,
  TV-Icon. Pill-Button „Hide all" zwischen den Gruppen. Bottom-Tab-Bar:
  Matches, News, Leagues, Following, More.
- **UI-Muster:** **Light Theme** (Gegenentwurf zu theScore); Datum als
  horizontale Tab-Leiste statt Datepicker; Wettbewerbs-Gruppen einklappbar;
  Score mittig zwischen den Wappen — sehr schnelle Scanbarkeit; Kalender-
  Icon mit Badge.
- **WM26-Bezug:** Spieleliste: Datums-Tabs (Gestern/Heute/Morgen/Spieltage)
  statt reinem Scroll, Gruppierung nach WM-Gruppe bzw. K.o.-Runde mit
  einklappbaren Headern, Score-Layout mittig wie im Duell-Design der
  Tipp-Eingabe.

### CON-04a · Konzept „WC26" — Matches-Liste mit Status- und Datums-Filter
- **Inhalt:** Titel „Matches", darunter Status-Tabs (All / • Live /
  Upcoming / Finished) und horizontale Datums-Chips (Thu 12 … Mon 15).
  Spiele als Karten **nach WM-Gruppe gruppiert** (GROUP A, B, C, D):
  Team-Kürzel + Flagge außen, Anstoßzeit mittig, darunter Stadion
  (Estadio Azteca, MetLife, Mercedes-Benz …) und Datum als Meta-Zeile.
- **UI-Muster:** Doppelfilter Status + Datum; Stadion-Info direkt in der
  Spielkarte; Gruppen-Label als Karten-Header.
- **WM26-Bezug:** Spieleliste um Status-Filter (alle/live/anstehend/
  beendet) ergänzen; Stadion-Meta in die Spielkarten (Daten vorhanden).

---

## Kategorie F — Tabellen / Ligen

### CON-04c · Konzept „WC26" — Gruppen-Tabellen (Standings)
- **Inhalt:** Titel „Standings", Tabs **Groups / Knockout**. Alle Gruppen
  untereinander scrollbar, je Gruppe eine Karte: Header „GROUP A" (grün),
  Zeilen mit Platz, Flagge, Teamname, Spalten MP / W / D / L / PTS.
- **UI-Muster:** Gruppen + K.o. als Tab-Paar auf einer Seite; kompakte
  einheitliche Spaltenköpfe; eine Karte pro Gruppe.
- **WM26-Bezug:** Sehr nah am vorhandenen Turnier-Tab (12-Gruppen-Grid +
  wischbarer K.o.-Baum) — die klare Spaltenstruktur und das Tab-Paar
  Gruppen/K.o. als Vorbild für die Überarbeitung.

## Kategorie G — Profil / Einstellungen / Mehr
*(noch keine Screenshots)*

## Kategorie H — Navigation & Grundgerüst (übergreifend)
*(Beobachtungen, die in mehreren Screenshots stecken)*

- **Bottom-Tab-Bar mit 5 Zielen** (theScore: News, Scores, Favorites,
  Discover, Leagues) — durchgehend sichtbar, aktiver Tab farbig + gefülltes
  Icon. Kandidat als Ersatz/Weiterentwicklung der WM26-Pill-Nav.
- **Horizontale Segment-Tabs mit Unterstreichung** als zweite Nav-Ebene
  (Liga-Filter im Feed, Sub-Tabs in der Spielseite).
- **Dark Theme als Standard**, sehr dunkles Grau statt reinem Schwarz,
  Hairline-Trenner, eine Akzentfarbe (Blau).
- **Vollbreite Primär-Buttons** unten mit Safe-Area-Abstand in Flows.
- **Meta-Zeilen** klein und grau („10h ago · 5m read", „ARS · 15h ago").
- **Sub-Tabs auf Spielseiten sind Branchenstandard** — alle vier Apps nutzen
  sie (theScore: Feed/Chat/Scorecast/Lineups/Stats · FotMob: Commentary/
  Lineup/Knockout/Stats/H2H · NBA: Summary/Box Score/Insights/Highlights ·
  MLS: Overview/Videos/Play-By-Play/Lineups). Klare Bestätigung für den
  Umbau der Spiel-Lupe von langem Scroll auf Tabs.
- **Light vs. Dark:** FotMob ist hell, theScore/NBA-Header dunkel, MLS dunkel
  mit Teamfarben-Gradients — beides funktioniert; entscheidend sind eine
  Akzentfarbe + ruhige Flächen.
- **Bottom-Sheets für kontextuelle Einstellungen** (MLS-01) statt separater
  Settings-Seiten.
- **Zustands-Codierung im Turnierkontext:** durchgestrichen = ausgeschieden,
  grauer Platzhalter + Datum = offen, farbiger Rahmen = aktuell (FM-02).
- **Status-Badges in Farbe** (LIVE grün, ENDED rot, FT-Chip) statt reinem
  Text — in Konzepten wie echten Apps (CON-01b, FM-01).
- **Formketten** (letzte 5 Spiele als W/D/L-Farbpunkte) und **Ring-/
  Balken-Prognosen** als kompakte Vor-Spiel-Module (CON-02).
- **Pill-Tabs vs. Unterstreichungs-Tabs:** echte Apps nutzen meist
  Unterstreichung, Konzepte oft Pills (CON-01c, theScore-Sub-Tabs) — zur
  WM26-Pill-Nav passen Pills als zweite Ebene.
- **Zustandsabhängige Spielseite:** Header-Aktionen und Karten-Reihenfolge
  wechseln mit der Spielphase vor/live/nach (CON-02 vs. CON-03, MLS-02).

---

## Eingangsprotokoll

| ID | App | Inhalt | Kategorie |
|---|---|---|---|
| TS-01 | theScore | Onboarding: Push-Toggles | A |
| TS-02 | theScore | News-Feed Home | B |
| TS-03 | theScore | Artikel-Reader | C |
| TS-04 | theScore | Spiel-Detail (Scorecast) | D |
| TS-05 | theScore | Onboarding: Team-Favoriten | A |
| MLS-01 | MLS | Club-Benachrichtigungen (Bottom-Sheet) | A |
| FM-01 | FotMob | Matches-Tagesliste (Light Theme) | E |
| FM-02 | FotMob | Spielseite: K.o.-Baum-Tab | D |
| NBA-01 | NBA | Spielseite: Summary/Statistik | D |
| MLS-02 | MLS | Spielseite: Teamfarben-Hero + Overview | D |
| CON-01a | Konzept „FIFA WC Mexico 2026" | Splash/Welcome mit Trophäe | A |
| CON-01b | Konzept „FIFA WC Mexico 2026" | Home: Live-Hero + Today Match | B |
| CON-01c | Konzept „FIFA WC Mexico 2026" | Spiel-Detail: Aufstellung auf Rasen | D |
| CON-02 | Konzept (UCL-Familie) | Spielseite vor Anpfiff: Prognose-Ringe, Formkette | D |
| CON-03 | Konzept (UCL-Familie) | Spielseite nach Abpfiff: News + Statistik-Balken | D |
| CON-04a | Konzept „WC26" | Matches-Liste: Status-/Datums-Filter | E |
| CON-04b | Konzept „WC26" | Home-Dashboard mit Quick Access | B |
| CON-04c | Konzept „WC26" | Gruppen-Tabellen (Standings) | F |

*Hinweis: Der „WC26"-Shot (CON-04) wurde doppelt geschickt — einmal erfasst.*
