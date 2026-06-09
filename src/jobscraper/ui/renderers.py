#!/usr/bin/env python3
"""Rich-text rendering helpers for description and derived-content panes."""

from __future__ import annotations

import html
from typing import Any, Dict, Sequence

from .utils import format_ts


def html_shell(title: str, body: str) -> str:
    """Wrap pane content in the shared rich-text shell used by the analysis browsers."""
    return f"""
    <html>
      <head>
        <style>
          body {{ background: #0f141b; color: #c9d1d9; font-family: Inter, Arial, sans-serif; margin: 0; padding: 8px; }}
          h1, h2, h3 {{ color: #f0f6fc; margin: 0 0 8px 0; font-weight: 600; }}
          h1 {{ font-size: 18px; }}
          h2 {{ font-size: 14px; margin-top: 14px; }}
          h3 {{ font-size: 12px; margin-top: 12px; }}
          p, li {{ line-height: 1.42; }}
          code, pre {{ font-family: 'JetBrains Mono', Consolas, monospace; }}
          pre {{ background: #11161d; border: 1px solid #21262d; padding: 8px; white-space: pre-wrap; }}
          .meta {{ color: #8b949e; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; }}
          .chips {{ margin: 6px 0 10px 0; }}
          .chip {{ display: inline-block; padding: 2px 6px; margin: 0 5px 5px 0; border: 1px solid #30363d; background: #11161d; color: #c9d1d9; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 10px; }}
          .section {{ margin-bottom: 12px; }}
          .accent {{ border-left: 3px solid #6366f1; padding-left: 8px; }}
          a {{ color: #4f8cff; text-decoration: none; }}
          ul {{ margin-top: 4px; padding-left: 18px; }}
          table {{ width: 100%; border-collapse: collapse; }}
          th, td {{ border: 1px solid #21262d; padding: 5px 7px; text-align: left; }}
          th {{ background: #161b22; }}
          svg {{ max-width: 100%; height: auto; background: #11161d; border: 1px solid #21262d; }}
        </style>
        <title>{html.escape(title)}</title>
      </head>
      <body>{body}</body>
    </html>
    """


def render_description_html(job: Dict[str, Any]) -> str:
    """Render the selected job detail document for the description pane."""
    title = html.escape(str(job.get("title") or "Job"))
    company = html.escape(str(job.get("company") or ""))
    location = html.escape(str(job.get("location") or ""))
    department = html.escape(str(job.get("department") or ""))
    stack = [item.strip() for item in str(job.get("detected_stack") or "").split(",") if item.strip()]
    chips = "".join(f'<span class="chip">{html.escape(item)}</span>' for item in stack[:24])
    location_modes = "".join(f'<span class="chip">{html.escape(str(item))}</span>' for item in (job.get("location_modes") or []))
    interest = "".join(f'<span class="chip">{html.escape(str(item))}</span>' for item in (job.get("interest_tags") or []))
    raw = dict(job.get("raw") or {})
    meta_bits = [
        ("Company", company),
        ("Location", location),
        ("Department", department),
        ("Source Row", html.escape(str(job.get("source_name") or ""))),
        ("ATS", html.escape(str(job.get("ats") or ""))),
        ("Portal", html.escape(str(job.get("source_portal") or "company_board"))),
        ("Updated", html.escape(format_ts(job.get("last_seen_at")))),
    ]
    hn_parser_bits = []
    if str(job.get("source_portal") or "").strip().lower() == "hackernews":
        parser_engine = str(raw.get("parser_engine") or "").strip()
        parser_confidence = str(raw.get("parser_confidence") or "").strip()
        parser_reason = str(raw.get("parser_reason") or "").strip()
        if parser_engine:
            hn_parser_bits.append(("HN Parser", html.escape(parser_engine)))
        if parser_confidence:
            hn_parser_bits.append(("Parser Confidence", html.escape(parser_confidence)))
        if parser_reason:
            hn_parser_bits.append(("Parser Note", html.escape(parser_reason)))
    meta_bits.extend(hn_parser_bits)
    meta = "".join(f"<tr><th>{label}</th><td>{value}</td></tr>" for label, value in meta_bits if value)
    links = []
    if job.get("job_url"):
        links.append(f'<a href="{html.escape(str(job["job_url"]))}">Job URL</a>')
    if job.get("apply_url"):
        links.append(f'<a href="{html.escape(str(job["apply_url"]))}">Apply URL</a>')
    text_block = html.escape(str(job.get("text") or "")).replace("\n", "<br/>")
    body = f"""
      <div class="accent">
        <h1>{title}</h1>
        <div class="meta">{company} | {location}</div>
      </div>
      <div class="chips">{chips}{location_modes}{interest}</div>
      <div class="section">{' | '.join(links)}</div>
      <div class="section"><table>{meta}</table></div>
      <div class="section"><h2>Description</h2><p>{text_block}</p></div>
    """
    return html_shell(str(job.get("title") or "Job"), body)


def render_analytics_html(payload: Dict[str, Any]) -> str:
    """Render aggregate analytics into a compact analysis document."""
    totals = payload.get("totals") or {}
    def items(rows: Sequence[Dict[str, Any]], key: str = "name") -> str:
        if not rows:
            return "<p class=\"meta\">No data.</p>"
        return "<ul>" + "".join(
            f"<li><strong>{html.escape(str(row.get(key) or row.get('company') or ''))}</strong> - {int(row.get('count') or 0)}</li>"
            for row in rows[:16]
        ) + "</ul>"
    body = f"""
      <div class="accent">
        <h1>Analytics</h1>
        <div class="chips">
          <span class="chip">total {int(totals.get('total') or 0)}</span>
          <span class="chip">open {int(totals.get('open_total') or 0)}</span>
          <span class="chip">remote {int(totals.get('remote_total') or 0)}</span>
          <span class="chip">closed {int(totals.get('closed_total') or 0)}</span>
        </div>
      </div>
      <div class="section"><h2>Languages</h2>{items((payload.get('stack') or {}).get('language') or [])}</div>
      <div class="section"><h2>Domains</h2>{items((payload.get('stack') or {}).get('domain') or [])}</div>
      <div class="section"><h2>Tools</h2>{items((payload.get('stack') or {}).get('tool') or [])}</div>
      <div class="section"><h2>Companies</h2>{items(payload.get('companies') or [], key='company')}</div>
    """
    return html_shell("Analytics", body)


def render_roadmap_html(payload: Dict[str, Any]) -> str:
    """Render the topic roadmap document for the current scope."""
    overview = payload.get("overview") or {}
    levels = payload.get("levels") or {}
    by_company = payload.get("by_company") or []
    checklist = payload.get("checklist") or []

    def render_counter(rows: Sequence[Dict[str, Any]], key: str = "name") -> str:
        if not rows:
            return "<p class=\"meta\">No data.</p>"
        return "<ul>" + "".join(
            f"<li><strong>{html.escape(str(row.get(key) or row.get('company') or ''))}</strong> - {int(row.get('count') or row.get('job_count') or 0)}</li>"
            for row in rows[:12]
        ) + "</ul>"

    def render_level(level_name: str, rows: Sequence[Dict[str, Any]]) -> str:
        if not rows:
            return ""
        return f"<div class=\"section\"><h2>{html.escape(level_name)}</h2><ul>" + "".join(
            f"<li><strong>{html.escape(str(row.get('topic') or ''))}</strong> - {html.escape(str(row.get('item') or ''))}</li>"
            for row in rows[:24]
        ) + "</ul></div>"

    company_sections = "".join(
        f"<div class=\"section\"><h2>{html.escape(str(item.get('company') or ''))}</h2>{render_counter(item.get('topics') or [])}</div>"
        for item in by_company[:10]
    )
    checklist_html = "<ul>" + "".join(
        f"<li><strong>{html.escape(str(item.get('level') or ''))}</strong> - {html.escape(str(item.get('topic') or ''))}: {html.escape(str(item.get('task') or ''))}</li>"
        for item in checklist[:30]
    ) + "</ul>"
    body = f"""
      <div class="accent">
        <h1>Topic Roadmap</h1>
        <div class="chips">
          <span class="chip">scope {html.escape(str(payload.get('scope') or payload.get('scope_label') or ''))}</span>
          <span class="chip">jobs {int(payload.get('job_count') or 0)}</span>
        </div>
      </div>
      <div class="section"><h2>Dominant Topics</h2>{render_counter(overview.get('topics') or [])}</div>
      <div class="section"><h2>Languages</h2>{render_counter(overview.get('languages') or [])}</div>
      <div class="section"><h2>Tools</h2>{render_counter(overview.get('tools') or [])}</div>
      {render_level('Basics', levels.get('basics') or [])}
      {render_level('Intermediate', levels.get('intermediate') or [])}
      {render_level('Advanced', levels.get('advanced') or [])}
      {render_level('Expert', levels.get('expert') or [])}
      {render_level('Domain Champion', levels.get('champion') or [])}
      <div class="section"><h2>By Company</h2>{company_sections or '<p class="meta">No company-specific roadmap available.</p>'}</div>
      <div class="section"><h2>Study Checklist</h2>{checklist_html}</div>
    """
    return html_shell("Topic Roadmap", body)
