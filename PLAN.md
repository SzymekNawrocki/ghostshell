# Hacker RPG — plan (od 2026-08-29)

## Cel appki (zmieniony 2026-08-29)

Nie tracker XP, który tylko *przyjmuje* gotowe wyniki skanów — appka ma być **narzędziem, które
faktycznie robi robotę etycznego hakera**: sama uruchamia realne narzędzia bezpieczeństwa/OSINT,
parsuje wynik, zapisuje historię i nadaje XP za to, co naprawdę zrobiła. Gamifikacja (XP, questy)
zostaje jako warstwa na wierzchu, nie jako sens appki.

Porzucony pomysł: appka jako celowo dziurawy web-target do atakowania. Powód: nowe luki
(Active Directory, SIEM, HackTheBox, MiliTech) i tak nie są web AppSec, więc dziurawa appka
webowa by ich nie ćwiczyła. Szczegóły dyskusji: `NOTES.md` (wpis 2026-08-29), `MISSION.md`.

## Stack

- **Backend:** FastAPI (już jest) + Postgres (już jest, `docker-compose.yml`).
- **Orkiestracja narzędzi:** `subprocess` woła realne CLI (Sherlock, theHarvester, exiftool —
  wszystkie już zainstalowane u Szymona).
- **Frontend:** Jinja2 (szablony HTML renderowane przez FastAPI) + HTMX (podmiana fragmentów
  strony bez przeładowania, bez builda/npm). Jeden proces, jeden język (Python) — `uvicorn
  main:app` odpala całość.
- **Styl wizualny:** estetyka z karty postaci (artifact 2026-08-29) — HUD/dossier, IBM Plex Mono,
  paleta amber/cyan na ciemnym tle.

## Kolejność budowy (żeby appka zawsze działała, nigdy "pół zepsuta")

1. ✅ **Sherlock end-to-end, backend only** (zrobione 2026-08-29). `asyncio.create_subprocess_exec`
   → parsowanie linijek `[+] Serwis: URL` → tabela `osint_scans` (`db.py:init_db()`) → XP
   (`calculate_osint_xp`) → `POST /quest/sherlock`. Przetestowane na żywo (realny `sherlock.exe`,
   realna baza w Dockerze): pełny skan bez `--site` to ~400 serwisów i **2-3 minuty**, nie 30-60s
   jak zakładano wstępnie — stąd `SHERLOCK_TIMEOUT_SECONDS=240` domyślnie.
2. ✅ **Jinja2 + HTMX** (zrobione 2026-08-29). `GET /` renderuje `templates/dashboard.html`
   (dawny JSON-status z `/` przeniesiony koncepcyjnie do `/health`); formularz leci przez HTMX
   (`hx-post`, form-encoded, nie JSON) na nowy `POST /quest/sherlock/ui`, który współdzieli logikę
   skanu z `POST /quest/sherlock` (`perform_sherlock_scan()`) i zwraca fragment
   `templates/_sherlock_result.html` — podmiana bez przeładowania. Styl: `static/style.css`,
   HUD/dossier, IBM Plex Mono (Google Fonts), amber/cyan na ciemnym tle. HTMX z jsdelivr CDN.
   Błędy (timeout, zły `SHERLOCK_BIN`) renderują się jako `.error-box` we fragmencie zamiast
   wywalać surowy response HTMX-owi.
3. **Kolejne narzędzia** (theHarvester, exiftool) — kopiując sprawdzony wzorzec z kroku 1: własny
   panel na dashboardzie (form + wynik), własny `perform_<tool>_scan()`, wpis do `osint_scans`
   z odpowiednią wartością `tool`. Layout dashboardu (ustalone 2026-08-29): **panele pionowo na
   jednej stronie**, nie zakładki/osobne routy — każdy panel ma własny `hx-target`, więc długi skan
   jednego narzędzia nie blokuje reszty. Wspólny pasek `TOTAL XP` / `LVL` na górze
   (`db.py:get_total_xp()`) aktualizuje się przez HTMX out-of-band swap (`hx-swap-oob="true"` na
   `#xp-bar` w każdym fragmencie wyniku) — każdy kolejny panel musi doliczać ten sam OOB-blok.
4. Dalej: manualny quest-log dla rzeczy nieautomatyzowalnych (ukończona maszyna HTB, przećwiczona
   technika AD, przeczytany rozdział ISO, postawiony SIEM) — tabela `manual_quests`, osobny
   endpoint, ten sam mechanizm XP.

## Otwarte pytania / do ustalenia po drodze

- **Decyzja 2026-08-29: async od razu, nie "na później".** Sherlock/theHarvester sprawdzają setki
  serwisów i mogą trwać 30-60+ s — wołanie ich przez zwykły `subprocess.run()` w endpointcie
  `async def` blokowałoby cały event loop FastAPI (żaden inny request nie byłby obsłużony w tym
  czasie). Zamiast tego: `asyncio.create_subprocess_exec` + `await proc.communicate()` —
  prawdziwe, nieblokujące wywołanie procesu zewnętrznego.
- Appka na czas budowy odpalana lokalnie (`uvicorn`, poza Dockerem) — Sherlock/theHarvester/exiftool
  muszą być zainstalowane w kontenerze dopiero, gdy appka wraca do `docker-compose`.

## Zasada pracy (bez zmian)

Kod appki (i nowe wzorce w niej) pisze Szymon sam, z głowy — nowe koncepty (jak `subprocess`)
tłumaczone najpierw na osobnym, małym przykładzie. Patrz `[[teaching-code-user-writes-not-me]]`,
`NOTES.md`.
