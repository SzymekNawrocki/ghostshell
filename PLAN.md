# GhostShell — plan (od 2026-08-29, przemianowane z "Hacker RPG" 2026-08-29)

## Cel appki (zmieniony 2026-08-29, gamifikacja usunięta 2026-08-29)

Nie tracker XP, który tylko *przyjmuje* gotowe wyniki skanów — appka ma być **narzędziem, które
faktycznie robi robotę etycznego hakera**: sama uruchamia realne narzędzia bezpieczeństwa/OSINT,
parsuje wynik i zapisuje historię.

**Update tego samego dnia: żadnej gamifikacji.** Pierwotny plan zakładał XP/poziomy jako warstwę
na wierzchu — porzucone. To ma być narzędzie, nie gra: bez punktów, bez poziomów, bez "questów".
Usunięte z kodu: `calculate_xp`, `calculate_osint_xp`, `calculate_level`, `db.get_total_xp()`,
kolumna `xp_earned` w `osint_scans`, cały endpoint `/quest/scan` (i tabela `scans` — dotyczyła
tylko naliczania XP za wyniki skanów obrazów, nie uruchamiała żadnego realnego narzędzia, więc
odpadła w całości, nie tylko warstwa XP). Zostaje: `osint_scans(tool, target, found_count,
results, created_at)` — czysta historia, bez punktacji.

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
   → parsowanie linijek `[+] Serwis: URL` → tabela `osint_scans` (`db.py:init_db()`) →
   `POST /scan/sherlock`. Przetestowane na żywo (realny `sherlock.exe`, realna baza w Dockerze):
   pełny skan bez `--site` to ~400 serwisów i **2-3 minuty**, nie 30-60s jak zakładano wstępnie —
   stąd `SHERLOCK_TIMEOUT_SECONDS=240` domyślnie.
2. ✅ **Jinja2 + HTMX** (zrobione 2026-08-29). `GET /` renderuje `templates/dashboard.html`
   (dawny JSON-status z `/` przeniesiony koncepcyjnie do `/health`); formularz leci przez HTMX
   (`hx-post`, form-encoded, nie JSON) na nowy `POST /scan/sherlock/ui`, który współdzieli logikę
   skanu z `POST /scan/sherlock` (`perform_sherlock_scan()`) i zwraca fragment
   `templates/_sherlock_result.html` — podmiana bez przeładowania. Styl: `static/style.css`,
   HUD/dossier, IBM Plex Mono (Google Fonts), amber/cyan na ciemnym tle. HTMX z jsdelivr CDN.
   Błędy (timeout, zły `SHERLOCK_BIN`) renderują się jako `.error-box` we fragmencie zamiast
   wywalać surowy response HTMX-owi.
3. ✅ **theHarvester + exiftool** (zrobione 2026-08-29), wzorzec z kroku 1 powielony 1:1:
   - **theHarvester** (`POST /scan/theharvester[/ui]`): `-b hackertarget` jako sztywne, niekonfigurowalne
     przez użytkownika źródło (bez klucza API, odpowiada w kilka-kilkanaście sekund; `crtsh`
     sprawdzony na żywo i nie zwracał wyników — najwyraźniej ich API się zmieniło/padło, więc
     zostało odrzucone). Wynik czytany z pliku (`-f <prefix>` → `<prefix>.json`, `hosts:
     ["subdomena:ip", ...]`), nie ze stdout — stabilniejsze niż scraping tekstu. Plik tymczasowy
     w `tempfile.TemporaryDirectory()`, sprzątany automatycznie.
   - **exiftool** (`POST /scan/exiftool[/ui]`): jedyne narzędzie z uploadem pliku zamiast tekstu
     (`UploadFile`, `hx-encoding="multipart/form-data"` po stronie HTMX). `-j -G` daje czysty JSON
     z metadanymi. Upload trafia do `tempfile.NamedTemporaryFile`, usuwany w `finally` zaraz po
     analizie — nic z przesłanego pliku nie zostaje na dysku. **Pułapka złapana na żywo:**
     `File:FileName`/`File:Directory` w wyniku exiftoola opisują tymczasową ścieżkę serwera, nie
     oryginalną nazwę pliku — trzeba je jawnie usuwać z metadanych przed pokazaniem/zapisem, inaczej
     UI pokazuje losową nazwę `tmpXXXXXX.jpg` zamiast np. `food.jpg`. Limit uploadu: 25 MB.
   - Wspólny insert do `osint_scans` wydzielony do `db.save_osint_scan(tool, target, found_count,
     results)` — bez tego trzeci powtórzony blok SQL przestawał mieć sens.
   - Layout dashboardu (ustalone 2026-08-29): **panele pionowo na jednej stronie**, nie
     zakładki/osobne routy — każdy panel ma własny `hx-target`, więc długi skan jednego narzędzia
     nie blokuje reszty.
4. Dalej: manualny log dla rzeczy nieautomatyzowalnych (ukończona maszyna HTB, przećwiczona
   technika AD, przeczytany rozdział ISO, postawiony SIEM) — tabela `manual_notes` (nazwa robocza),
   osobny endpoint. Czysta historia/notatnik, bez punktacji.

## Otwarte pytania / do ustalenia po drodze

- **Decyzja 2026-08-29: async od razu, nie "na później".** Sherlock/theHarvester sprawdzają setki
  serwisów i mogą trwać 30-60+ s — wołanie ich przez zwykły `subprocess.run()` w endpointcie
  `async def` blokowałoby cały event loop FastAPI (żaden inny request nie byłby obsłużony w tym
  czasie). Zamiast tego: `asyncio.create_subprocess_exec` + `await proc.communicate()` —
  prawdziwe, nieblokujące wywołanie procesu zewnętrznego.
- Appka na czas budowy odpalana lokalnie (`uvicorn`, poza Dockerem) — Sherlock/theHarvester/exiftool
  muszą być zainstalowane w kontenerze dopiero, gdy appka wraca do `docker-compose`.

## Zasada pracy (zmieniona 2026-08-29)

Pierwotnie: kod appki pisze Szymon sam, z głowy. **Nadpisane 2026-08-29** — Szymon poprosił, żeby
przy GhostShell kod pisał Claude bezpośrednio (bez podziału tłumaczenie+jego kod). Dotyczy tylko
tego projektu; w innych (PayPaper, przygotowanie do egzaminu) stara zasada nadal obowiązuje. Patrz
`[[teaching-code-user-writes-not-me]]`, `NOTES.md`.
