# Project Learnings

## Overview

Durable learnings from recent `kurier` work. Keep this file for practical caveats that should survive beyond a single session.

## Stable Learnings

- `src/arkiv/core/llm.py` is the canonical integration point for Ollama, OpenAI-compatible, and Anthropic chat calls. Keep provider logic there instead of adding wrapper dependencies back.
- Pluggy hook calls return lists. When a hook is unimplemented, preserve the original content instead of treating an empty result like a replacement value.
- Memory search quality improves when human-facing fields such as suggested filenames, destination names, and display titles are stored and indexed alongside the core content. A readable `match_reason` also makes retrieval behavior easier to trust and debug.
- A manual review correction is not complete until the item is marked as confirmed. If the category changes without confirming confidence, the entry can fall back into the review queue on the next refresh.
- User-facing validation and benchmark output must read like product language, not developer commands. Internals such as `uv run ruff check src/ tests/`, `mypy`, or raw pytest summaries are useful for contributors, but the finished Kurier UI/CLI should translate them into plain results such as "Code-Qualität geprüft", "Typprüfung bestanden", and "Alle automatischen Tests erfolgreich".
- Plan a dedicated UX polish pass after the core flows are technically stable. The goal is to make Kurier feel understandable for "Otto Normalverbraucher": fewer raw implementation terms, clearer status messages, calmer error explanations, and guided next steps instead of command-shaped output.
- Kurier must earn trust before it grows feature surface. For the next product hardening pass, every new feature or technical optimization should connect to a concrete user-confidence question: what happened to my document, where is it now, how do I fix it, or why did search behave this way?
- Treat the local beta feedback flow as a product-learning system, not telemetry. Signals stay local and should answer practical next-step questions for the 5-day real-use test documented in `docs/anti-failure-plan.md`.
- Keep router refactors small and behavior-preserving. Webhook payload construction now has one source of truth, and folder/review routing share destination collision handling so route behavior does not drift between normal filing and manual review.

## Klassifikation: eigene Kategorien

- **Ein Kategorie-NAME zieht Treffer an, wenn er wörtlich im Dokument steht.** Solange
  die eingebaute Kategorie `rechnung` zur Auswahl stand, landete jede Zahnarzt-Rechnung
  dort statt bei `gesundheit` — das Wort "Rechnung" steht eben groß auf dem Blatt.
  Beschreibungen umformulieren half nicht, auch nicht mit "use this INSTEAD of rechnung".
  Erst das Abschalten der Kategorie (`disabled_categories`) plus ein neutraler Name für
  das Auffangbecken (`sonstiges`) hat es gelöst: 16 von 18 echten Dokumenten richtig
  statt 4 von 18 (gemessen 2026-08-09, qwen2.5:7b).
- **Modelle antworten mit Kategorien, die gar nicht angeboten wurden.** Nach dem
  Abschalten kam `rechnung` weiter zurück — frei erfunden aus dem Dokumenttext.
  Ungeprüft übernommen trifft so eine Kategorie keine einzige Route, das Dokument
  verschwindet lautlos. `Classifier._reject_unknown_category()` schickt es jetzt in die
  Prüfliste. Wer eine Kategorienliste vorgibt, muss die Antwort dagegen validieren.
- **Die Reihenfolge im Prompt zählt.** Eigene Kategorien stehen jetzt vor den
  eingebauten; standen die Defaults vorne, gewann die generische Kategorie.
- **Deutsche Stichwörter in die Beschreibungen aufnehmen**, wenn die Dokumente deutsch
  sind. "dentist" matcht nicht auf "Zahngesundheit GmbH".
- **Kategorien gegeneinander abgrenzen, nicht nur beschreiben.** `versicherung` fing
  eine Motorradhandschuh-Rechnung ab, bis der Satz "NOT for buying goods or gear — a
  shop selling helmets is fahrzeug" dazukam.
- Beim Tunen immer gegen **echte Dokumente** messen, nicht gegen erfundene Beispiele:
  zwei der Testfälle waren gar nicht das, wonach sie aussahen ("Rechnung Rail" ist
  Railway-Hosting, keine Bahnfahrt).

## Workflow Gotchas

- Mocked tests can miss real provider and plugin wiring bugs. After touching classification or routing flow, run at least one smoke test against a real provider.
- `mypy` is strict enough to catch integration details that unit tests may gloss over, especially around subprocess text handling and typed dict shapes.
- When changing webhook routing, cover both the installed-plugin path and the missing-plugin path. The missing-plugin branch should still enqueue the same versioned payload for retry instead of becoming a logging-only failure.
- macOS/fsevents delivers ONLY Modified events (never Created) for cloned files — Finder copies and `cp` on APFS set the `is_cloned` flag. A watcher that ignores Modified events for unknown paths silently misses such files until the next restart. Observed live 2026-08-06; the watcher now processes unknown paths on Modified and relies on in-flight lock + signature dedup against double processing.
- TOML top-level keys (`inbox_dir`, `notifications`, ...) must appear BEFORE the first `[section]` header. Placed after one, they silently become keys of that section and pydantic falls back to defaults — a hand-written test config pointed the watcher at the REAL inbox this way. When smoke-testing with a temp config, assert the loaded `inbox_dir` before starting the watcher. Documenting this was not enough: the REAL user config carried the same defect for months (`inbox_dir`, `review_dir` swallowed by `[database]`), unnoticed because the ignored values happened to equal the defaults. `kurier doctor` now flags it via `ArkivConfig.misplaced_settings()` — prefer a check over a note for silent-fallback traps.

- **macOS-Freigabe "Lokales Netzwerk" gilt pro Python-Binary.** Der per pipx installierte
  Kurier-Dienst erreicht n8n im Heimnetz problemlos, das Repo-venv (`.venv/bin/python`)
  bekommt fuer denselben POST "No route to host" (Errno 65) — waehrend `curl` und `ping`
  aus derselben Shell durchkommen. Wer im Repo-venv Dokumente einliest, erzeugt dadurch
  fehlgeschlagene Webhook-Zustellungen, die wie ein Kurier-Bug aussehen. Vor jeder
  Ursachensuche denselben Request mit beiden Pythons testen (nachgewiesen 2026-08-09).

- **Ein uebersprungener Scan sieht aus wie eine leere Datei — und wird nach dem Dateinamen
  einsortiert.** Bei Seiten ueber `MAX_OCR_PIXELS` gab die OCR frueher ganz auf. Der Engine
  fiel dann auf einen Metadaten-Text zurueck (Dateiname, MIME, Groesse), das Modell sah
  praktisch nur den Namen und meldete trotzdem 90 % Sicherheit. Weil der Dateiname selbst
  aus einer solchen Vermutung stammt, bestaetigt sich der Fehler mit jedem Durchlauf: Ein
  Minijob-Arbeitsvertrag hiess "RechnungDienstleistungen.pdf" und galt deshalb als Rechnung
  (gefunden 2026-08-09, 8 von 20 Dokumenten betroffen). Zwei Konsequenzen: OCR rechnet grosse
  Seiten jetzt herunter (`_fitting_ocr_dpi`, bis `MIN_OCR_DPI`), und wenn wirklich nur
  Metadaten vorliegen, deckelt `Engine._mark_as_guess` die Sicherheit — das Dokument landet
  in der Pruefliste statt still in einem Ordner.

## Infra / Deploy Notes

- GitHub Actions should use the same editable install path as local development so CI and README do not drift apart.
- The repo-level secret scan is worth treating as permanent CI baseline, not a one-off hardening task. The useful shape is: PR + `main` push + manual dispatch, least-privilege permissions, pinned action revisions, full-history checkout, and no noisy PR comments by default.
- For packaging or CLI changes, a green local dev environment is not enough on its own. Fresh editable-install and wheel-install smoke tests catch first-run problems that normal in-place checks can miss.
- Local network has two Raspberry Pi nodes relevant for Kurier/n8n testing:
  - `n8n-pi.local` / `192.168.178.75` is the current n8n node. SSH is open on `22`; n8n runs via Docker Compose in `/opt/n8n` and responds on `http://n8n-pi.local:5678/`.
  - n8n workflow `Kurier Intake` is published for the first smoke path. It accepts `POST http://n8n-pi.local:5678/webhook/kurier` and responds with JSON containing `ok: true`, `service: "kurier-intake"`, and `receivedAt`.
  - Local Kurier config at `~/.config/kurier/config.toml` includes `[routes.n8n]` as a webhook catch-all (`categories = []`, `confidence_threshold = 0.3`) pointing to `http://n8n-pi.local:5678/webhook/kurier`.
  - n8n API access is available with the local key stored outside the repo at `~/.config/kurier/n8n-api-key`. Do not commit or print this key; use it as `X-N8N-API-KEY` for `http://n8n-pi.local:5678/api/v1/...`.
  - `192.168.178.110` is the likely Bitcoin node and not the Kurier/n8n target. SSH is open on `22`; Bitcoin/Electrum-like ports observed include `8332`, `8333`, and `50001`.
