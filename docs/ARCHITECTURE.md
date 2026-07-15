# Architecture

## Overview
JobScraper is a Windows-first PyQt desktop workbench for:
- scraping direct ATS, public API/RSS/job-board, and Hacker News hiring sources
- storing normalized jobs in SQLite
- managing source health, filtered job browsing, exports, analytics, and topic roadmaps

The code is now organized as a package-first project:
- `src/jobscraper/ui/` for the Qt shell and panes
- `src/jobscraper/scraping/` for scrape adapters
- `src/jobscraper/storage/` for SQLite and filesystem helpers
- `src/jobscraper/ai/` for Local AI / OpenAI helpers and roadmaps
- `src/jobscraper/runtime/` for packaged helper dispatch
- `src/jobscraper/resources/` for shipped defaults such as the seed sources file
- `src/jobscraper/assets/` for immutable packaged UI assets such as the app icon

## Runtime layout
Entry point:
- `python -m jobscraper`
- packaged app: `JobScraper.exe`

Startup flow:
- `jobscraper.__main__`
- `jobscraper.bootstrap.initialize_runtime()`
- workspace bootstrap under `Documents\JobScraper`
- helper dispatch when `--helper` is present
- otherwise `jobscraper.ui.main.main()`

Mutable runtime state lives under:
- `Documents\JobScraper\config\settings.json`
- `Documents\JobScraper\config\sources.json`
- `Documents\JobScraper\config\source_watchlist.json`
- `Documents\JobScraper\data\jobs.sqlite`
- `Documents\JobScraper\logs\jobscraper.log`
- `Documents\JobScraper\backups\`
- `Documents\JobScraper\reports\`

## Subsystem map
- `jobscraper/bootstrap.py`
  - workspace creation
  - runtime logging
  - frozen/source helper command dispatch
- `jobscraper/paths.py`
  - Documents workspace paths
  - bundled resource paths
  - exported/debug/log/report directories
- `jobscraper/ui/window.py`
  - main shell
  - request graph
  - dialog/actions wiring
  - settings persistence for DB/source/watchlist/Local AI paths
  - visible-surface refresh policy and buffered scrape/activity progress
  - cached analytics by filter signature
  - source-centric Workbench focus and Hacker News review toggles
  - top-level surfaces: Workbench, Analytics, Topic Roadmap, Sources
- `jobscraper/storage/db.py`
  - schema creation/migration
  - WAL/busy-timeout/foreign-key SQLite connection hardening
  - source/job indexes for source-centric filters and status views
  - source metadata, source health groups, and quality scoring
  - source import preview, validation, duplicate identity checks, transaction rollback, and pre-import SQLite backups
  - disabled-source status cleanup during import
  - per-source success/failure counters and scrape duration diagnostics
  - job persistence
  - oversized raw source payload summarization before SQLite persistence
  - legacy question cache tables are left in place for non-destructive compatibility but are no longer used by the UI/runtime path
  - summary-only jobs-table queries
  - batched job-detail reads for roadmap generation
- `jobscraper/scraping/core.py`
  - main scrape pipeline
  - adapter selection
  - public source adapters
  - Hacker News normalization
- `jobscraper/runtime/helper_main.py`
  - packaged helper-mode dispatcher
- `jobscraper/ai/client.py`
  - Local AI / OpenAI endpoint helpers
- `jobscraper/ai/roadmap.py`
  - topic roadmap synthesis from stored jobs

## Key flows
Normal scrape:
- UI action
- worker/thread task
- `jobscraper.scraping.core`
- `jobscraper.storage.db`
- visible-tab reload; hidden panes are marked dirty and refreshed on demand

Frozen helper flow:
- helper dispatch remains available for narrow runtime utilities
- browser rendering is not shipped in the public build

Source-centric browse flow:
- source row selected in `Sources`
- `Focus In Workbench`
- Workbench projects that row into:
  - `Source` family filter
  - `Source Row` filter
  - optional Hacker News parsed-vs-fallback review filter
- company counts, jobs, analytics, roadmap scopes, and exports all use the same query filters

## Source inventory policy
- The bundled source set under `src/jobscraper/resources/company_sources.json` is intentionally biased toward direct public boards first:
  - `greenhouse`
  - `ashby`
  - `lever`
  - `workday`
  - public remote boards such as RemoteOK, Remotive, Jobicy, The Muse, Working Nomads, We Work Remotely, PowerToFly, Dice, SkipTheDrive, and Authentic Jobs
- The bundled source set also carries disabled-by-default rows for lower-confidence public aggregators and search pages, including Remote.co, JustRemote, FlexJobs, Jobs24x, RemoteFront, Underdog, YC Work at a Startup, Himalayas, NoDesk, Jobspresso, Remote Rocketship, Arc.dev, Levels.fyi, Built In, Climatebase, Naukri, Instahyre, Cutshort, Hirist, Foundit, TimesJobs, AI Jobs, ML Jobs, DataJobs, Rust Jobs, GolangProjects, Python.org Jobs, CyberSecJobs, Otta, Welcome to the Jungle, Devsnap, IBM Careers, and Virsec.
- Public HTML/search rows stay disabled until live direct parsing proves they return normalized job IDs, titles, URLs, and locations without browser state. Public JSON/RSS rows that live-probe successfully can be enabled by default.
- Credentialed, subscription, API-token, and login-backed rows are intentionally not bundled in the public release.
- Additional ATS families are registered for company-specific career-board rows: Workable, Teamtailor, BambooHR, Breezy HR, JazzHR, iCIMS, Jobvite, Oracle Taleo, SAP SuccessFactors, UKG/UltiPro, ADP, Paylocity, Pinpoint, Comeet, Rippling, Microsoft Careers, Amazon Jobs, Apple Jobs, Oracle Careers, IBM Careers, and Uber Careers.
- Browser-discovery rows are intentionally not bundled in the public release.
- Browser-only sources remain manual-watchlist candidates until a stable direct public board is confirmed.
- The active workspace copy under `Documents\JobScraper\config\sources.json` is the mutable operator copy; the bundled resource is the seed/default.
- The active source watchlist under `Documents\JobScraper\config\source_watchlist.json` stores candidate direct ATS rows for probing before promotion.
- Candidate discovery can probe Greenhouse, Lever, Ashby, SmartRecruiters, Workday, iCIMS, Jobvite, Workable, Teamtailor, and BambooHR watchlist surfaces; only rows with importable title, URL, location, and job ID should be promoted as enabled sources.
- The Sources tab can run a background watchlist probe, write `Documents\JobScraper\reports\source_candidate_report.json`, append verified unique rows to `sources.json`, and import the updated source file with a pre-import SQLite backup.
- `import_sources()` now treats the active source file as authoritative for source inventory sync.
- Source identities in the file are upserted.
- Malformed or duplicate identities fail fast.
- The UI previews new, updated, and stale-disabled counts before mutating SQLite.
- A timestamped SQLite backup is created under `Documents\JobScraper\backups`.
- Stale DB-only identities are disabled during sync instead of being left active with stale scrape status.

## UI-thread policy
- SQLite reads, scrape work, and roadmap generation run off the GUI thread.
- The GUI thread is now limited to compact model resets, coarse progress updates, and the final `QTextBrowser.setHtml(...)` call for whichever pane is actually visible.
- Activity logging is bounded and buffered so noisy scrape adapters do not repaint the shell on every progress line.
- Row browsing and aggregate analysis are split so Workbench keeps companies/jobs plus the selected-job description, while aggregate and roadmap surfaces remain lazy-loaded in their own top-level tabs.
- Source-family filters, source-row filters, and Hacker News review filters are treated as first-class query inputs rather than search text conventions.
- HTTP adapters use bounded retry/backoff with `Retry-After` support for transient rate limits and service errors.

## Current constraints
- `MainWindow` still owns most request orchestration.
- The public release intentionally avoids browser-rendered sources, LinkedIn/Indeed saved views, subscription portals, and local API-token sources.
