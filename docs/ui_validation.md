# UI Validation

## What this covers
This document records the current workbench layout checks against the desktop-first UI contract.

The intent is to verify:
- baseline mode: `1920x1080`
- expanded mode: `2560x1440`
- ultrawide mode: `3440x1440`
- constrained fallback: `1440x900`

The current pass used an offscreen Qt layout probe against the live app shell after startup data loaded.

## Files involved
- `src/jobscraper/ui/theme.py`: size tokens and global GTK-like flat styling
- `src/jobscraper/ui/panes.py`: minimum pane widths and pane composition
- `src/jobscraper/ui/window.py`: shell splitter layout and startup lifecycle

## Probe results

### Baseline mode: 1920x1080
- requested / actual window size: `1920x1080`
- command bar height: `34`
- main tabs visible: `3`
- analysis tabs visible: `3`
- settings hidden by default: `true`
- activity hidden by default: `true`
- pane widths:
  - companies: `317`
  - jobs: `928`
  - description: `657`

### Expanded mode: 2560x1440
- requested / actual window size: `2560x1440`
- command bar height: `34`
- main tabs visible: `3`
- analysis tabs visible: `3`
- pane widths:
  - companies: `424`
  - jobs: `1240`
  - description: `878`

### Ultrawide mode: 3440x1440
- requested / actual window size: `3440x1440`
- command bar height: `34`
- main tabs visible: `3`
- analysis tabs visible: `3`
- pane widths:
  - companies: `570`
  - jobs: `1671`
  - description: `1181`

### Constrained mode: 1440x900
- requested / actual window size: `1440x900`
- command bar height: `34`
- main tabs visible: `3`
- analysis tabs visible: `3`
- settings hidden by default: `true`
- activity hidden by default: `true`
- pane widths:
  - companies: `237`
  - jobs: `694`
  - description: `491`

## Interpretation
- The shell stays compact in all tested sizes.
- The top chrome remains thin and does not grow with larger resolutions.
- Wider displays reveal more usable pane width instead of inflating padding.
- The constrained mode now uses shrink-friendly metadata labels plus pane minimums so the company rail does not collapse into an unreadable strip.

## Remaining manual checks
The offscreen probe validates structure, not visual polish. These still need human spot checks in the running app:
- tab text clipping at `125%` and `150%` scaling
- dense table readability on a real `1920x1080` display
- splitter behavior after user resizing in constrained mode
- analytics and roadmap readability with large filtered result sets

## How to modify
- To change baseline size or density, edit `src/jobscraper/ui/theme.py`.
- To change pane minimum widths or composition, edit `src/jobscraper/ui/panes.py`.
- To change splitter defaults or startup behavior, edit `src/jobscraper/ui/window.py`.

## Performance notes
- The validation pass relies on the async startup path, so the shell does not block on SQLite initialization before first paint.
- Table content in the probe came through summary queries, not full-detail payloads.
