FROM python:3.12-slim
WORKDIR /app

# System tools the app shells out to via subprocess, plus git (theHarvester
# has no real PyPI release — the name is squatted — so it's installed
# straight from its GitHub repo) and exiftool's CLI (Debian package name is
# libimage-exiftool-perl; it installs the `exiftool` binary on PATH).
#   - gobuster: web directory/file brute-forcer.
#   - dirb: not used for its own binary — its Debian package bundles
#     /usr/share/dirb/wordlists/common.txt, the default wordlist gobuster
#     runs against (GOBUSTER_WORDLIST env var can point elsewhere).
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        libimage-exiftool-perl \
        nmap \
        gobuster \
        dirb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# sherlock-project's own deps (requests, pandas, ...) don't collide with the
# app's — safe to install straight into the app's environment.
RUN pip install --no-cache-dir sherlock-project

# theHarvester is a different story: caught by a real build, not guessed —
# installing it straight into the app's environment downgraded our pinned
# fastapi (0.141.1 -> 0.136.3) and uvicorn (0.52.4 -> 0.48.0) to satisfy its
# own dependency pins. pipx gives it an isolated venv (its `theHarvester`
# console script still lands on PATH) so it can't touch the app's deps.
# Also pinned to the 4.11.1 tag — HEAD on GitHub had already bumped to
# requiring Python 3.14.
RUN pip install --no-cache-dir pipx
ENV PATH="/root/.local/bin:${PATH}"
RUN pipx install "theHarvester @ git+https://github.com/laramies/theHarvester.git@4.11.1"

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
