# Codebase Map

## Package structure
```text
src/jobscraper/
|-- __main__.py
|-- bootstrap.py
|-- paths.py
|-- ui/
|   |-- main.py
|   |-- window.py
|   |-- panes.py
|   |-- models.py
|   |-- tasks.py
|   |-- workers.py
|   |-- renderers.py
|   |-- theme.py
|   |-- utils.py
|   \-- workbench_ui.py
|-- scraping/
|   |-- core.py
|   \-- hackernews.py
|-- storage/
|   |-- db.py
|   \-- fs.py
|-- ai/
|   |-- client.py
|   \-- roadmap.py
\-- runtime/
    |-- helper_main.py
    \-- trace_viewer.py
```

## Execution flow
```text
python -m jobscraper
  -> jobscraper.__main__.main()
  -> bootstrap.initialize_runtime()
  -> ui.main.main()
  -> MainWindow()
  -> startup_initialize()
  -> initialize_database_task()
  -> visible-surface request graph
  -> model / rich-text browser updates
```

## Where to modify
- Workbench, Analysis, Sources orchestration: `src/jobscraper/ui/window.py`
- Pane widget structure and control grouping: `src/jobscraper/ui/panes.py`
- Table models, busy strips, and row rendering: `src/jobscraper/ui/models.py`
- Tooltip coverage and static view configuration: `src/jobscraper/ui/workbench_ui.py`
- Description and analysis HTML rendering: `src/jobscraper/ui/renderers.py`
- Background request helpers: `src/jobscraper/ui/tasks.py`
- Long-running worker threads: `src/jobscraper/ui/workers.py`
- Theme, spacing, row height, and stylesheet: `src/jobscraper/ui/theme.py`
- Data access, caching keys, and filtered summaries: `src/jobscraper/storage/db.py`
- Scrape adapters and Hacker News parsing: `src/jobscraper/scraping/core.py`

## Performance cautions
- Do not move DB, export, scrape, or roadmap work onto the GUI thread.
- Keep tables model/view based.
- Use summary queries for list panes and lazy-load deep detail.
- Any new request path should go through the request-token pattern in `src/jobscraper/ui/window.py`.
- If a new analysis surface repeats the current jobs slice, key its cache by the full filter signature rather than ad hoc widget state.
