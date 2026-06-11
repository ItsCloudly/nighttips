/* Boot-Skript: bewusst extern statt inline, damit die Content-Security-Policy
   ohne 'unsafe-inline' für Skripte auskommt.
   Die App kennt nur Dark — der Modus ist fest gesetzt; das data-Attribut
   bleibt als Anker für künftige Darstellungsvarianten. */
"use strict";
document.documentElement.dataset.mode = "dark";
