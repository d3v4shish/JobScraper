# JobScraper

PyQt desktop app for:
- scraping public company and job-board sources
- browsing jobs from SQLite
- managing direct source health, exports, analytics, and topic roadmaps

## Source run

First-time setup after cloning:

```bash
git clone <repo-url>
cd JobScraper-Public
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[build,test]"
python -m jobscraper
```

Windows PowerShell:

```powershell
git clone <repo-url>
cd JobScraper-Public
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e ".[build,test]"
.\.venv\Scripts\python.exe -m jobscraper
```

Run in source mode:

```bash
source .venv/bin/activate
python -m jobscraper
```

If the editable install is not in place yet:

```bash
PYTHONPATH=src python -m jobscraper
```

## Workspace

Mutable runtime data lives under:

```text
Documents\JobScraper
```

Key paths:
- `Documents\JobScraper\config\settings.json`
- `Documents\JobScraper\config\sources.json`
- `Documents\JobScraper\config\source_watchlist.json`
- `Documents\JobScraper\data\jobs.sqlite`
- `Documents\JobScraper\logs\jobscraper.log`
- `Documents\JobScraper\backups\`
- `Documents\JobScraper\reports\`

On first run, the app auto-migrates the legacy repo-root artifacts when they exist.
The Settings dialog can point the UI at a different SQLite database and source JSON file.
Source import validates the JSON, shows a preview, asks for confirmation, and creates a SQLite backup before applying changes.
The source watchlist stores candidate public ATS rows that can be probed from the Sources tab before promotion into `sources.json`.
Probe reports are saved under `Documents\JobScraper\reports`.
Application logs rotate under `Documents\JobScraper\logs`; use `Tools -> Open Logs Folder` from the app.

The public release does not seed or support login-backed sources such as LinkedIn or Indeed saved views, subscription sources, local API-token sources, or browser-rendered source discovery.

## Windows EXE build

```powershell
.\build\build_windows.ps1
```

Artifacts:
- executable: `dist\JobScraper\JobScraper.exe`
- app folder: `dist\JobScraper\`
- installer: `dist\installer\JobScraperSetup.exe` if Inno Setup is available

The Windows build script removes stale packaged output before rebuilding. The installer closes a running `JobScraper.exe` during upgrade and the uninstaller asks whether to remove `Documents\JobScraper` user data.

## Linux onedir build

```bash
./build/build_linux.sh
```

Artifacts:
- executable: `dist-linux/JobScraper/JobScraper`
- app folder: `dist-linux/JobScraper/`
- launcher: `dist-linux/JobScraper/JobScraper.desktop`
- icon: `dist-linux/JobScraper/JobScraper.png`

Install the desktop launcher and icon for the current user:

```bash
./build/install_linux_desktop.sh
```

Linux desktop environments show icons through `.desktop` launchers and icon themes. The packaged app still sets its Qt window icon from the bundled app assets.

