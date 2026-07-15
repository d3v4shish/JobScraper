# UI Workbench

## Overview
The JobScraper desktop UI is a dense, desktop-first PyQt workbench.

Primary areas:
- command bar
- `Workbench` tab with:
  - companies pane
  - jobs pane
  - selected-job description pane
- `Analytics` tab
- `Topic Roadmap` tab
- `Sources` tab
- activity dialog
- settings dialog

The visual target remains flat, dark, GTK-like, and information-dense.

Performance/refresh model:
- only the visible top-level tab is refreshed eagerly
- analytics and roadmaps load lazily when their top-level tabs enter view
- hidden dialogs do not consume live repaint work
- activity logging is buffered and bounded instead of repainting per scrape line
- analytics payloads are cached by filter signature so repeated filter scopes do not always hit SQLite again

## Code map
- `src/jobscraper/ui/window.py`
  - `MainWindow`
  - request graph
  - action handlers
- `src/jobscraper/ui/panes.py`
  - pane/widget composition
- `src/jobscraper/ui/models.py`
  - table models and busy strips
- `src/jobscraper/ui/workers.py`
  - scrape worker and generic background task helpers
- `src/jobscraper/ui/tasks.py`
  - background helpers used by the request graph
- `src/jobscraper/ui/renderers.py`
  - description and analysis HTML rendering
- `src/jobscraper/ui/theme.py`
  - shared style tokens and global stylesheet
- `src/jobscraper/ui/workbench_ui.py`
  - table configuration and tooltip coverage

## Workspace-aware behavior
The UI now boots against the persisted workspace settings from:
- `Documents\JobScraper\config\settings.json`

The Settings dialog exposes:
- active DB path
- active sources path
- candidate source watchlist path
- active log path
- Local AI endpoint URL
- Local AI model

Those values are persisted back into the workspace settings file instead of living only in memory.

## Sources tab
The `Sources` tab focuses on raw public source rows for diagnostics and watchlist promotion.

The `Workbench` jobs filter area now has two explicit source-scope controls:
- `Source`: source family such as company boards, Hacker News, public APIs, or public job-board rows
- `Source Row`: one configured source row inside that family

This makes source-centric browsing distinct from free-text search.

Hacker News review also has an explicit scope switch when the active source is HN:
- `All HN jobs`
- `Parsed companies`
- `Fallback bucket`

The raw source diagnostics table includes `Focus In Workbench`, which projects the selected source row into those filters and switches back to the jobs surface.
It also includes derived source health, failure counts, and last scrape duration so repeated parser or network failures are visible without opening logs.
The diagnostics table groups source health into healthy, disabled, blocked, parser failure, and new, with a 0-100 quality score.
The health filter in the Sources header isolates one group without changing the underlying workspace source inventory.
The `Probe Watchlist` action runs in the background, probes candidate direct ATS rows from the configured watchlist, writes a report under `Documents\JobScraper\reports`, and imports only verified unique rows with a SQLite backup.
The same header shows the latest `SCRAPE_SUMMARY` line as a human-readable "new jobs added" report after each scrape.

Tools menu actions include:
- previewed source import with backup
- filtered JSON export
- activity dialog
- open logs folder
- open backups folder
- open reports folder
- open source watchlist
- copy diagnostics summary without secrets

## Helper integration
The workbench no longer depends on sibling helper script paths.

Browser-backed helper flows are launched through the packaged runtime dispatcher, so the same UI logic works in:
- source mode
- PyInstaller onedir builds

## Refresh behavior
- `Workbench`, `Analytics`, `Topic Roadmap`, and `Sources` are treated as separate surfaces; hidden-tab work is deferred until the tab becomes active.
- `Description` stays on `Workbench` because it is true selected-row detail.
- `Analytics` and `Topic Roadmap` are lazy top-level panes; background results can be cached, but the rich-text browsers update only when the pane is visible.
- Scrape progress is reduced to a coarse summary for the busy strips while detailed log lines are buffered for the Activity dialog.

## Known constraints
- `MainWindow` still centralizes most async orchestration.
- Source-mode helper launching assumes the package is installed editable or run with `PYTHONPATH=src`.
