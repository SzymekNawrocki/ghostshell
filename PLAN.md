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
4. ✅ **Manualny log** (zrobione 2026-08-29). Tabela `manual_notes(category, note, created_at)`.
   `POST /notes` (JSON) i `POST /notes/ui` (HTMX) dzielą `db.save_manual_note()`, zwracają od razu
   zapisany wiersz — bez re-query. Panel formularza dokłada nowy wpis na górę listy przez
   `hx-swap="afterbegin"` (nie podmienia całej listy — jeden insert, jeden nowy `<li>`), reset pola
   przez `hx-on::after-request="this.reset()"`. Lista renderuje się też przy `GET /`
   (`db.get_manual_notes()`) — ten sam partial `_manual_note_item.html` używany przez oba miejsca
   (`{% include %}` w pętli i bezpośrednio jako odpowiedź HTMX) dzięki wspólnej nazwie zmiennej
   kontekstu (`note`). Pusta lista → `{% for ... %} ... {% else %}` w Jinja, nie osobny warunek.

5. ✅ **Powrót do Dockera** (zrobione 2026-08-29) — plan domknięty w całości. `Dockerfile` bazuje
   teraz na `python:3.12-slim` (nie `alpine`) — trzy realne narzędzia mają natywne zależności
   (lxml, psycopg) i pakiety systemowe (`exiftool`, `git`), które na Debianie są prostym `apt-get`
   zamiast kompilowania na musl. Złapane na żywym buildzie, nie z dokumentacji:
   - `theHarvester` **nie ma realnego wydania na PyPI** (nazwa jest zasquattowana, pusty pakiet
     0.0.1) — instalacja musi iść przez `pip install ... git+https://github.com/laramies/theHarvester.git`.
   - HEAD tego repo w międzyczasie podniósł wymóg do Pythona 3.14 — build padał, dopóki instalacja
     nie została przypięta do taga `4.11.1` (tej samej wersji co lokalny dev-install).
   - **Zainstalowany prosto do środowiska appki, theHarvester degradował pinned zależności**:
     `fastapi 0.141.1→0.136.3`, `uvicorn 0.52.4→0.48.0` — bo jego własne pinny wygrały rozwiązywanie
     zależności pip. Naprawione przez `pipx install` — izolowany venv dla theHarvestera, jego
     `theHarvester` command i tak ląduje na `PATH` (`/root/.local/bin`), ale bez dotykania zależności
     appki. `sherlock-project` nie miał tego problemu — zainstalowany zwykłym `pip install` prosto
     do środowiska appki.
   - `exiftool` na Debianie: pakiet `libimage-exiftool-perl` (nie `exiftool`).
   - Przetestowane end-to-end przez `docker compose up` (bez żadnego lokalnego `venv`/`uvicorn`):
     wszystkie cztery funkcje (sherlock, theHarvester, exiftool, manualny log) + dashboard +
     statyki — działają tak samo jak lokalnie.

## Decyzje architektoniczne (zamknięte)

- **Async od razu, nie "na później".** Sherlock/theHarvester sprawdzają setki serwisów i mogą
  trwać 30-60+ s — wołanie ich przez zwykły `subprocess.run()` w endpointcie `async def`
  blokowałoby cały event loop FastAPI (żaden inny request nie byłby obsłużony w tym czasie).
  Zamiast tego: `asyncio.create_subprocess_exec` + `await proc.communicate()` — prawdziwe,
  nieblokujące wywołanie procesu zewnętrznego.

## Plan zrealizowany (2026-08-29)

Wszystkie 4 kroki budowy + powrót do Dockera — zrobione i przetestowane na żywo (nie tylko
"powinno działać"). Pomysły wykraczające poza ten plan (historia skanów w UI, eksport CSV/JSON,
więcej źródeł OSINT typu Holehe/Maigret, rate-limiting skanów) omówione osobno, nie tutaj —
zaczynać dopiero po wyraźnej decyzji, nie domyślnie.

## Faza 2 — pod HTB (zaplanowane 2026-08-30, jeszcze nie zaczęte)

Kontekst: Szymon zaczyna uczyć się na HackTheBox i pytał, jak appka może to zautomatyzować.
Przegląd całego arsenału (Nmap, Nikto, gobuster, searchsploit, Hydra, hashcat/john, Wireshark,
Burp Suite, Metasploit, LinPEAS/WinPEAS) pod kątem: co faktycznie pasuje do wzorca appki
(subprocess z maszyny atakującej → parsowalny wynik w rozsądnym czasie → zapis), a co jest z natury
interaktywne/stanowe i appka by to tylko udawała.

### Zostają jako osobne narzędzia (nie wchodzą do GhostShell)

- **Wireshark, Burp Suite** — już zainstalowane osobno u Szymona. Interaktywne z natury (Wireshark:
  ciągłe przechwytywanie, wynik to binarny `.pcap`, nie tekst; Burp: proxy do ręcznej pracy z
  requestami — Repeater/Intruder to praca człowieka, nie jednorazowe wywołanie).
- **Metasploit (`msfconsole`)** — wieloetapowa sesja (wybór modułu → opcje → sesja → działania w
  niej), nie "jedno wywołanie → jeden wynik". Do zainstalowania osobno, nieodzowne na wielu
  maszynach HTB, ale poza appką.
- **LinPEAS / WinPEAS** — działają NA zaatakowanej maszynie (po zdobyciu shella), appka działa z
  zewnątrz do środka — przeciwny kierunek, fizycznie nie do zorkiestrowania zdalnie. Ściągnąć same
  skrypty (`carlospolop/PEASS-ng`), trzymać gotowe do wgrania na target.
- **searchsploit** — technicznie pasuje (CLI, natychmiastowy wynik), ale odrzucone: to pojedyncza,
  natychmiastowa komenda lokalna — owinięcie jej w formularz webowy nie oszczędza czasu względem
  wpisania tego samego w terminalu obok przeglądarki. Jedyny zysk (wspólna historia) nie uzasadnia
  kolejnego panelu.
- **Hydra** — odrzucone: brute-force wymaga starannego doboru wordlisty użytkowników/haseł *per
  target*, generyczny formularz i tak zredukowałby się do gorszego CLI. Do tego appka nie ma dziś
  żadnej autoryzacji/ograniczenia celu — jednoklikowy brute-force w web UI to realna furtka do
  nadużycia, gdyby appka kiedyś wyszła poza `localhost`. Koszt zabezpieczenia (allowlist celu) nie
  broni się względem korzyści.
- **hashcat / john** — odrzucone: czas działania praktycznie nieograniczony (może kręcić się
  godzinami), kompletnie nie pasuje do modelu appki "krótki request → wynik" (Sherlock z 2-3 min
  już jest na granicy sensu). Zostaje w terminalu, najlepiej w `tmux`.

### Wchodzą do GhostShell — kolejność (żadna jeszcze nie zaczęta)

1. ✅ **Nmap** + **grupowanie po `target`/`engagement`** (zrobione 2026-08-30). Nmap: ten sam
   wzorzec co Sherlock (`asyncio.create_subprocess_exec`, `-oX -` → XML na stdout → parsowanie
   portów/usług → `db.save_osint_scan`). Grupowanie: dodana kolumna `engagement` (np. "HTB: Lame")
   do `osint_scans`, dashboard grupuje historię po niej — bez tego czwarty (i kolejne) panel tylko
   pogłębiałby bałagan w płaskiej liście. Zrobione od razu razem, nie osobno: koszt osobnej
   migracji później > koszt zrobienia raz teraz. Przetestowane na żywo na `scanme.nmap.org`.
   Przy okazji: pasek postępu + licznik czasu przy każdym skanie (żeby 2-3 minutowy Sherlock/Nmap
   nie wyglądał jak zawieszona appka) i naprawiony `docker-compose.yml` (brakujący healthcheck na
   `db` — `app` potrafił wystartować przed gotowością Postgresa).
2. ✅ **gobuster** (zrobione 2026-08-30, tryb `dir`) — dorzucony wcześniej niż planowano: Szymon
   zaczyna THM i chciał mieć web-recon gotowy z góry, zamiast czekać na potwierdzenie wzorca.
   `ScanSpec` jak reszta (`GOBUSTER_SPEC` w `scan_tools.py`). Wordlista: `common.txt` z pakietu
   `dirb` (`/usr/share/dirb/wordlists/common.txt` — Debian trzyma go pod inną ścieżką niż Kali,
   złapane na żywym buildzie, nie z dokumentacji), ~4600 słów, nadpisywalna przez
   `GOBUSTER_WORDLIST`. Rozszerzenia (`php,txt,bak,zip,html,config`) na stałe w kodzie, nie w
   formularzu. Dwa problemy złapane na żywym skanie, nie w teorii:
   - **Czas.** Sam Nmap-owy strach się potwierdził: bez rozszerzeń pełny `common.txt` to ~85s przy
     domyślnych 10 wątkach gobustera, ale `-x` z 6 rozszerzeniami mnoży liczbę requestów ×7 —
     przy 10 wątkach ekstrapoluje się to na ~10 minut. Podniesione do 50 wątków (`GOBUSTER_THREADS`)
     — z rozszerzeniami realnie ~2 minuty na `scanme.nmap.org`. Budżet (`GOBUSTER_TIMEOUT_SECONDS`)
     ustawiony na 240s.
   - **Parsowanie.** `-q --no-color --no-progress` i tak wypisuje kod ANSI czyszczący linię
     (`\x1b[2K`) tuż przed każdym wynikiem — nieudokumentowane, złapane dopiero na żywym stdout.
     Parser czyści to wyrażeniem regularnym przed dopasowaniem, z testem pilnującym regresji.
   Nikto zostaje odłożony — decyzja: zobaczyć jak gobuster sprawdza się w praktyce, zanim dorzucimy
   drugie narzędzie na tym samym wzorcu.

## Refaktor architektury: scan_tools.py (2026-08-30)

Po dodaniu Nmapa cztery integracje narzędzi (`run_sherlock`, `run_theharvester`, `run_exiftool`,
`run_nmap` w `main.py`) miały identyczny interfejs i niemal identyczną implementację — kopiuj-wklej,
nie prawdziwa złożoność specyficzna dla narzędzia (test na usunięcie: usunięcie jednej z tych funkcji
nie koncentrowało złożoności, bo ta sama logika nadal siedziała wklejona w trzech pozostałych).

Zebrane w nowy plik `scan_tools.py`:
- **`ScanSpec`** — deklaratywny opis jednego narzędzia (binarka, budowanie argumentów, timeout,
  sposób czytania wyniku, parser). Cztery instancje: `SHERLOCK_SPEC`, `THEHARVESTER_SPEC`,
  `EXIFTOOL_SPEC`, `NMAP_SPEC`.
- **`run_cli_scan(spec, target)`** — jedyne miejsce wołające `asyncio.create_subprocess_exec`;
  obsługuje timeout, kod wyjścia, czytanie wyniku (stdout dla trójki, plik dla theHarvestera —
  jedyne narzędzie z asymetrią stdout/plik, obsłużone przez wymienny `read_output` w specu, nie
  przez `if` w środku).
- **`perform_scan(spec, target, engagement, display_target=None)`** — uruchamia, zapisuje do bazy,
  buduje odpowiedź. `display_target` istnieje wyłącznie dla exiftoola: appka nadal sama zapisuje
  wgrany plik na dysk tymczasowo i przekazuje tę ścieżkę jako `target` do wykonania, ale w bazie i
  odpowiedzi ma się pokazać prawdziwa nazwa wgranego pliku, nie ścieżka tymczasowa.
- `main.py` zostaje czystym routingiem HTTP — 8 endpointów (bez zmian w URL-ach/zachowaniu), każdy
  woła `perform_scan(ODPOWIEDNI_SPEC, ...)`.

Dodane pierwsze testy w projekcie: `tests/test_scan_tools.py` (16 testów — `run_cli_scan` z
zamockowanym subprocessem: happy path, zły kod wyjścia, brak binarki, timeout+kill, oraz wszystkie
cztery czyste funkcje `parse_*_output`), `pytest.ini`, `requirements-dev.txt` (pytest + pytest-asyncio,
osobno od `requirements.txt`, żeby obraz Dockera nie ciągnął zależności testowych).

Nowy `CONTEXT.md` — słownik domenowy (`ScanSpec`, `Engagement`, `Scan`, `Manual note`), żeby nazwy
pojęć nie rozjeżdżały się między kodem a rozmowami o architekturze.

Zweryfikowane na żywo po refaktorze (nie tylko testy jednostkowe): pełny `docker compose up --build`,
realny skan Nmap na `scanme.nmap.org`, theHarvester na `python.org` (ścieżka plikowa — najbardziej
ryzykowna zmiana), exiftool z uploadem (potwierdzone: `display_target` poprawnie pokazuje prawdziwą
nazwę pliku) — wszystkie trzy dały identyczne wyniki jak przed refaktorem.

## Zasada pracy (zmieniona 2026-08-29)

Pierwotnie: kod appki pisze Szymon sam, z głowy. **Nadpisane 2026-08-29** — Szymon poprosił, żeby
przy GhostShell kod pisał Claude bezpośrednio (bez podziału tłumaczenie+jego kod). Dotyczy tylko
tego projektu; w innych (PayPaper, przygotowanie do egzaminu) stara zasada nadal obowiązuje. Patrz
`[[teaching-code-user-writes-not-me]]`, `NOTES.md`.
