#!/usr/bin/env python3
"""
Async company job scraper core.

This module handles direct ATS feeds, public API/RSS sources, and Hacker News
hiring threads.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from time import perf_counter
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

import aiohttp

from .. import paths
from ..ai import client as ai
from ..storage import db
from ..storage import fs
from . import hackernews as hn_topic


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
DEFAULT_REQUIRE_WORDS: List[str] = []
DEFAULT_INCLUDE_GROUPS: List[List[str]] = []
DEFAULT_EXCLUDE_WORDS = ["visa", "relocation"]
DEFAULT_INTEREST_TERMS = [
    "Python",
    "Rust",
    "C++",
    "Go",
    "Linux",
    "Kernel",
    "Networking",
    "Backend",
    "Systems",
    "Infrastructure",
    "Platform",
    "Distributed Systems",
    "eBPF",
    "TCP/IP",
    "VPN",
    "WireGuard",
    "BGP",
    "DNS",
    "Proxy",
    "Firewall",
    "Kubernetes",
    "Docker",
    "Terraform",
    "Founding Engineer",
]

_TAG_RE = re.compile(r"<[^>]+>")
_TERM_RE_CACHE: Dict[str, re.Pattern[str]] = {}
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_JSON_LD_RE = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_GOOGLE_LINK_RE = re.compile(r'href="([^"]*jobs/results/[^"]+)"', re.IGNORECASE)
_WELLFOUND_LINK_RE = re.compile(r'href="([^"]*(?:wellfound\.com)?/jobs/[^"?#]+(?:/)?[^"#]*)"', re.IGNORECASE)
_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_DICE_LINK_RE = re.compile(r"/job-detail/[0-9a-f-]{24,}", re.IGNORECASE)
_JOB_ID_PATH_RE = re.compile(r"/job[s]?/(\d+|[0-9a-f-]{24,})(?:/|$)", re.IGNORECASE)
_OPTIVER_LINK_RE = re.compile(
    r"https://optiver\.com/working-at-optiver/career-opportunities/(\d+)/",
    re.IGNORECASE,
)
_DRW_SLUG_RE = re.compile(r'"slug":"([^"]+)"')
_GRESEARCH_LINK_RE = re.compile(
    r"https://www\.gresearch\.com/vacancies/[^\"']+/",
    re.IGNORECASE,
)
_URL_IN_TEXT_RE = re.compile(r"(https?://[^\s<>\"]+)", re.IGNORECASE)
_HN_EMPLOYMENT_RE = re.compile(r"\b(full[\s-]?time|part[\s-]?time|contract|contractor|internship|intern|freelance|temporary|temp)\b", re.IGNORECASE)
_HN_ROLE_RE = re.compile(
    r"\b(software|engineer|engineering|developer|programmer|architect|manager|scientist|research|sre|devops|qa|security|product|designer|frontend|front-end|backend|back-end|full stack|full-stack|mobile|data|platform|infrastructure|head|lead|director|cto|technical staff)\b",
    re.IGNORECASE,
)

ProgressCallback = Callable[[str], None]
ShouldStop = Callable[[], bool]
JobBatch = List[Dict[str, Any]]
JobAdapter = Callable[[aiohttp.ClientSession, Dict[str, Any]], Awaitable[JobBatch]]
StreamingJobAdapter = Callable[
    [aiohttp.ClientSession, Dict[str, Any], Optional[ProgressCallback]],
    AsyncIterator[JobBatch],
]


@dataclass
class ScrapeOptions:
    require_words: List[str] = field(default_factory=lambda: DEFAULT_REQUIRE_WORDS[:])
    include_groups: List[List[str]] = field(default_factory=lambda: [g[:] for g in DEFAULT_INCLUDE_GROUPS])
    exclude_words: List[str] = field(default_factory=lambda: DEFAULT_EXCLUDE_WORDS[:])
    include_words: List[str] = field(default_factory=list)
    include_mode: str = "any"
    interest_terms: List[str] = field(default_factory=lambda: DEFAULT_INTEREST_TERMS[:])
    enable_remote: bool = True
    enable_india_office_hybrid: bool = True
    concurrency: int = 6
    hackernews_parser_engine: str = "auto"
    only_source_ids: List[int] = field(default_factory=list)
    only_companies: List[str] = field(default_factory=list)


@dataclass
class ScrapeResult:
    source_id: int
    company: str
    ats: str
    fetched: int = 0
    saved: int = 0
    matching: int = 0
    closed: int = 0
    error: str = ""
    skipped: bool = False


@dataclass
class BrowserRenderResult:
    html: str
    final_url: str
    backend: str = "direct"
    page_title: str = ""
    json_responses: List[Dict[str, Any]] = field(default_factory=list)
    response_urls: List[str] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


class SourceStatusError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = unescape(str(html_text))
    replacements = {
        "<br>": "\n",
        "<br/>": "\n",
        "<br />": "\n",
        "<p>": "\n\n",
        "</p>": "\n",
        "<li>": "\n- ",
        "</li>": "\n",
        "</ul>": "\n",
        "</ol>": "\n",
    }
    for src, dest in replacements.items():
        text = text.replace(src, dest)
    text = _TAG_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def compact_text(value: Any, limit: int = 120) -> str:
    """Collapse whitespace and clamp long status text for logs and UI details."""
    text = compact_ws(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def clean_title_suffix(title: str, *suffixes: str) -> str:
    out = compact_ws(title)
    for suffix in suffixes:
        if out.endswith(suffix):
            out = compact_ws(out[: -len(suffix)])
    return out


def extract_title_tag(html_text: str) -> str:
    match = _TITLE_RE.search(html_text or "")
    if not match:
        return ""
    return compact_ws(strip_html(match.group(1)))


def extract_first_h1(html_text: str) -> str:
    for raw in _H1_RE.findall(html_text or ""):
        text = compact_ws(strip_html(raw))
        if text:
            return text
    return ""


def extract_meta_description(html_text: str) -> str:
    patterns = [
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']description["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text or "", re.IGNORECASE)
        if match:
            return compact_ws(strip_html(match.group(1)))
    return ""


def iter_json_nodes(node: Any) -> Iterable[Any]:
    if isinstance(node, list):
        for item in node:
            yield from iter_json_nodes(item)
        return
    if isinstance(node, dict):
        yield node
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from iter_json_nodes(item)


def extract_json_ld_objects(html_text: str) -> List[Any]:
    out: List[Any] = []
    for raw in _JSON_LD_RE.findall(html_text or ""):
        payload = raw.strip()
        if not payload:
            continue
        try:
            out.append(json.loads(unescape(payload)))
        except json.JSONDecodeError:
            continue
    return out


def find_job_posting_json_ld(html_text: str) -> Dict[str, Any]:
    for obj in extract_json_ld_objects(html_text):
        for node in iter_json_nodes(obj):
            if not isinstance(node, dict):
                continue
            job_type = node.get("@type")
            if job_type == "JobPosting":
                return node
            if isinstance(job_type, list) and "JobPosting" in job_type:
                return node
    return {}


def extract_balanced_json_array(text: str, key: str) -> List[Any]:
    start_key = text.find(key)
    if start_key < 0:
        return []
    start = text.find("[", start_key)
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return []
                return parsed if isinstance(parsed, list) else []
    return []


def extract_label_value(html_text: str, label: str) -> str:
    pattern = (
        rf"<dt[^>]*>\s*{re.escape(label)}\s*</dt>\s*"
        rf"<dd[^>]*>(.*?)</dd>"
    )
    match = re.search(pattern, html_text or "", re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return compact_ws(strip_html(match.group(1)))


def extract_html_block_after_marker(
    html_text: str,
    marker: str,
    *,
    end_markers: Optional[List[str]] = None,
) -> str:
    start = html_text.find(marker)
    if start < 0:
        return ""
    block = html_text[start:]
    if end_markers:
        end_positions = [pos for pos in (block.find(item) for item in end_markers) if pos > 0]
        if end_positions:
            block = block[: min(end_positions)]
    return block


def iso_from_seconds(value: Any) -> Optional[str]:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return strip_html(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(filter(None, (value_to_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("name", "title", "text", "label", "value", "city", "country"):
            text = value_to_text(value.get(key))
            if text:
                return text
        return ", ".join(
            filter(
                None,
                (value_to_text(v) for k, v in value.items() if k not in {"id", "uid"}),
            )
        )
    return strip_html(str(value))


def combine_text(*parts: Any) -> str:
    out: List[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, list):
            text = combine_text(*part)
        elif isinstance(part, dict):
            text = value_to_text(part)
        else:
            text = strip_html(str(part))
        if text:
            out.append(text)
    return "\n\n".join(out)


def parse_word_list(csv: str | Iterable[str]) -> List[str]:
    if isinstance(csv, str):
        if not csv.strip():
            return []
        return [w.strip() for w in csv.split(",") if w.strip()]
    return [str(w).strip() for w in csv if str(w).strip()]


def parse_include_groups(spec: str | Iterable[Iterable[str]]) -> List[List[str]]:
    if not isinstance(spec, str):
        return [[str(w).strip() for w in group if str(w).strip()] for group in spec]
    spec = spec.strip()
    if not spec:
        return []
    groups: List[List[str]] = []
    for raw_group in spec.split(";"):
        group = raw_group.strip()
        if not group:
            continue
        if "_" in group:
            words = [w.strip() for w in group.split("_") if w.strip()]
        else:
            # Split "remote+python" while preserving language names like "c++".
            words = [w.strip() for w in re.split(r"(?<!\+)\+(?!\+)", group) if w.strip()]
        if words:
            groups.append(words)
    return groups


def _term_re(term: str) -> re.Pattern[str]:
    key = term.lower().strip()
    cached = _TERM_RE_CACHE.get(key)
    if cached:
        return cached
    escaped = re.escape(key)
    if re.fullmatch(r"[a-z0-9]+", key):
        pattern = rf"\b{escaped}\b"
    else:
        pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    compiled = re.compile(pattern, re.IGNORECASE)
    _TERM_RE_CACHE[key] = compiled
    return compiled


def contains_term(text: str, term: str) -> bool:
    return _term_re(term).search(text) is not None


def contains_all(text: str, words: List[str]) -> bool:
    return all(contains_term(text, word) for word in words)


def matched_words(text: str, words: List[str]) -> List[str]:
    return [word for word in words if contains_term(text, word)]


REMOTE_TERMS = [
    "remote",
    "remote-first",
    "remote first",
    "remote friendly",
    "work from home",
    "work from anywhere",
    "distributed team",
    "fully distributed",
]

INDIA_TERMS = [
    "india",
    "bangalore",
    "bengaluru",
    "hyderabad",
    "pune",
    "mumbai",
    "delhi",
    "new delhi",
    "gurgaon",
    "gurugram",
    "noida",
    "chennai",
    "kochi",
    "cochin",
    "trivandrum",
    "thiruvananthapuram",
]

INDIA_WORK_MODE_TERMS = [
    "hybrid",
    "office",
    "onsite",
    "on-site",
    "in office",
    "in-office",
]

GO_CONTEXT_PATTERNS = [
    r"\bgo\b\s*(?:/|,|and|&)?\s*(?:backend|platform|infrastructure|services|microservices|api|apis|kubernetes|systems)",
    r"\b(?:backend|platform|infrastructure|services|microservices|api|apis|kubernetes|systems)\s*(?:/|,|and|&)?\s*\bgo\b",
]

FOUNDING_ENGINEER_TERMS = [
    "founding engineer",
    "founding software engineer",
    "founding backend engineer",
    "founding full stack engineer",
    "early engineer",
    "engineer #1",
    "engineer no. 1",
    "engineer number one",
    "first engineer",
    "technical co-founder",
]


def has_remote_signal(text: str) -> bool:
    return any(contains_term(text, term) for term in REMOTE_TERMS)


def has_india_signal(text: str) -> bool:
    return any(contains_term(text, term) for term in INDIA_TERMS)


def has_india_work_mode_signal(text: str) -> bool:
    return any(contains_term(text, term) for term in INDIA_WORK_MODE_TERMS)


def detect_location_modes(text: str, location: str = "") -> List[str]:
    full_text = f"{location}\n{text}"
    modes: List[str] = []
    if has_remote_signal(full_text):
        modes.append("remote")

    india_in_location = has_india_signal(location)
    india_in_text = has_india_signal(full_text)
    hybrid = contains_term(full_text, "hybrid")
    office = any(contains_term(full_text, term) for term in ["office", "onsite", "on-site", "in office", "in-office"])
    if india_in_location or (india_in_text and (hybrid or office)):
        if hybrid:
            modes.append("india_hybrid")
        if office:
            modes.append("india_office")
        if not hybrid and not office:
            modes.append("india_office_hybrid")

    return sorted(set(modes))


def contains_go_interest(text: str) -> bool:
    if contains_term(text, "golang") or contains_term(text, "go lang"):
        return True
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in GO_CONTEXT_PATTERNS)


def normalize_interest(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "", value.lower())


def selected_interest_tags(all_tags: List[str], terms: List[str], text: str) -> List[str]:
    if not terms:
        return all_tags
    wanted = {normalize_interest(term) for term in terms if term.strip()}
    direct_matches = {
        normalize_interest(term)
        for term in terms
        if term.strip() and (contains_term(text, term) or (normalize_interest(term) == "go" and contains_go_interest(text)))
    }
    out = [
        tag
        for tag in all_tags
        if normalize_interest(tag) in wanted or normalize_interest(tag) in direct_matches
    ]
    return sorted(set(out), key=str.lower)


def filter_and_match(
    text: str,
    options: ScrapeOptions,
    *,
    location: str = "",
) -> Dict[str, Any]:
    t = text.strip()
    meta: Dict[str, Any] = {
        "matched_required_words": [],
        "matched_include_words": [],
        "matched_include_group": [],
        "matched_builtin_groups": [],
        "location_modes": [],
        "interest_tags": [],
        "passes_filter": False,
    }
    if not t:
        return meta

    for word in options.exclude_words:
        if contains_term(t, word):
            return meta

    if options.require_words and not contains_all(t, options.require_words):
        return meta
    meta["matched_required_words"] = matched_words(t, options.require_words)

    modes = detect_location_modes(t, location)
    meta["location_modes"] = modes
    location_pass = (
        (options.enable_remote and "remote" in modes)
        or (
            options.enable_india_office_hybrid
            and any(mode.startswith("india_") for mode in modes)
        )
    )
    if not location_pass:
        return meta

    if any(contains_term(t, term) for term in FOUNDING_ENGINEER_TERMS):
        meta["matched_builtin_groups"] = ["founding_engineer"]

    detected = detect_stack(t)
    all_tags = detected.get("stack", [])
    interests = selected_interest_tags(all_tags, options.interest_terms, t)
    meta["interest_tags"] = interests
    if not interests:
        return meta

    if options.include_groups:
        for group in options.include_groups:
            if contains_all(t, group):
                meta["matched_include_group"] = group[:]
                break
        else:
            return meta

    if options.include_words:
        matched = matched_words(t, options.include_words)
        meta["matched_include_words"] = matched
        if options.include_mode == "all":
            if len(matched) != len(options.include_words):
                return meta
        else:
            if not matched:
                return meta

    meta["passes_filter"] = True
    return meta


TECH_TAXONOMY: Dict[str, Dict[str, List[str]]] = {
    "languages": {
        "Python": ["python"],
        "Rust": ["rust"],
        "C++": ["c++", "cpp", "c plus plus"],
        "Go": ["golang", "go lang"],
        "Java": ["java"],
        "JavaScript": ["javascript", "js"],
        "TypeScript": ["typescript"],
        "C#": ["c#"],
        "Ruby": ["ruby"],
        "Kotlin": ["kotlin"],
        "Swift": ["swift"],
    },
    "frameworks": {
        "Django": ["django"],
        "FastAPI": ["fastapi", "fast api"],
        "Flask": ["flask"],
        "React": ["react", "react.js", "reactjs"],
        "Vue": ["vue", "vue.js", "vuejs"],
        "Node.js": ["node.js", "nodejs", "node js"],
        "Rails": ["rails", "ruby on rails"],
    },
    "domains": {
        "Backend": ["backend", "back end", "server-side", "server side"],
        "Distributed Systems": ["distributed systems", "distributed system"],
        "Infrastructure": ["infrastructure", "infra"],
        "Kernel": ["kernel", "linux kernel"],
        "Linux": ["linux"],
        "Networking": ["networking", "tcp/ip", "bgp", "dns", "vpn", "wireguard", "firewall", "proxy"],
        "Platform": ["platform engineering", "platform engineer", "platform team"],
        "Systems": ["systems engineer", "systems engineering", "system software", "systems software"],
    },
    "groups": {
        "Founding Engineer": FOUNDING_ENGINEER_TERMS,
    },
    "tools": {
        "AWS": ["aws", "amazon web services"],
        "Azure": ["azure"],
        "BGP": ["bgp"],
        "CUDA": ["cuda"],
        "DNS": ["dns"],
        "Docker": ["docker"],
        "eBPF": ["ebpf", "eBPF"],
        "Embedded": ["embedded", "firmware"],
        "Firewall": ["firewall"],
        "GCP": ["gcp", "google cloud"],
        "Kafka": ["kafka"],
        "Kubernetes": ["kubernetes", "k8s"],
        "MySQL": ["mysql"],
        "Postgres": ["postgres", "postgresql"],
        "Proxy": ["proxy"],
        "Redis": ["redis"],
        "TCP/IP": ["tcp/ip", "tcp ip"],
        "Terraform": ["terraform"],
        "VPN": ["vpn"],
        "WireGuard": ["wireguard"],
    },
}


def detect_stack(text: str) -> Dict[str, List[str]]:
    found: Dict[str, List[str]] = {"languages": [], "frameworks": [], "domains": [], "groups": [], "tools": []}
    for category, entries in TECH_TAXONOMY.items():
        for name, aliases in entries.items():
            if name == "Go":
                matched = contains_go_interest(text)
            else:
                matched = any(contains_term(text, alias) for alias in aliases)
            if matched:
                found[category].append(name)
    for category in found:
        found[category].sort(key=str.lower)
    found["stack"] = sorted(
        found["languages"] + found["frameworks"] + found["domains"] + found["groups"] + found["tools"],
        key=str.lower,
    )
    return found


def detect_remote(text: str, location: str = "") -> bool:
    return has_remote_signal(f"{location}\n{text}")


def _retry_after_seconds(headers: Any) -> Optional[float]:
    """Parse Retry-After into seconds for polite portal/API retries."""
    value = str((headers or {}).get("Retry-After") or "").strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def _retry_delay_seconds(attempt: int, headers: Any = None) -> float:
    """Return bounded exponential-ish backoff with jitter."""
    retry_after = _retry_after_seconds(headers)
    if retry_after is not None:
        return min(30.0, retry_after)
    return min(8.0, 0.6 * attempt + random.uniform(0.05, 0.35))


def _should_retry_http_error(exc: Exception) -> bool:
    """Avoid retrying deterministic 4xx responses while keeping network retries."""
    if isinstance(exc, aiohttp.ClientResponseError):
        return int(exc.status or 0) in RETRYABLE_HTTP_STATUSES
    return True


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout_s: int = 30,
    retries: int = 3,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as exc:
            last_error = exc
            if attempt < retries and _should_retry_http_error(exc):
                await asyncio.sleep(_retry_delay_seconds(attempt, getattr(exc, "headers", None)))
    raise last_error or RuntimeError(f"fetch failed: {url}")


async def post_json(
    session: aiohttp.ClientSession,
    url: str,
    payload: Dict[str, Any],
    *,
    headers: Optional[Dict[str, str]] = None,
    timeout_s: int = 30,
    retries: int = 3,
) -> Any:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as exc:
            last_error = exc
            if attempt < retries and _should_retry_http_error(exc):
                await asyncio.sleep(_retry_delay_seconds(attempt, getattr(exc, "headers", None)))
    raise last_error or RuntimeError(f"post failed: {url}")


async def fetch_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout_s: int = 30,
    retries: int = 3,
) -> str:
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_s)) as resp:
                resp.raise_for_status()
                return await resp.text()
        except Exception as exc:
            last_error = exc
            if attempt < retries and _should_retry_http_error(exc):
                await asyncio.sleep(_retry_delay_seconds(attempt, getattr(exc, "headers", None)))
    raise last_error or RuntimeError(f"fetch failed: {url}")


def source_portal_name(source: Dict[str, Any]) -> str:
    return str(source.get("portal") or "").strip().lower()


def path_token(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[0] if parts else ""


def greenhouse_url(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if url and "boards-api.greenhouse.io" in url:
        return url if "content=true" in url else f"{url}{'&' if '?' in url else '?'}content=true"
    if not token and url:
        token = path_token(url)
    if not token:
        raise ValueError("Greenhouse source needs token or board URL")
    return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def lever_url(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if url and "api.lever.co" in url:
        return url if "mode=json" in url else f"{url}{'&' if '?' in url else '?'}mode=json"
    if not token and url:
        token = path_token(url)
    if not token:
        raise ValueError("Lever source needs token or jobs.lever.co URL")
    return f"https://api.lever.co/v0/postings/{token}?mode=json"


def ashby_url(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if url and "api.ashbyhq.com" in url:
        return url
    if not token and url:
        token = path_token(url)
    if not token:
        raise ValueError("Ashby source needs token or jobs.ashbyhq.com URL")
    return f"https://api.ashbyhq.com/posting-api/job-board/{token}"


def recruitee_url(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if url and "/api/offers" in url:
        return url
    if url and "recruitee.com" in url:
        parsed = urlparse(url)
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/api/offers/"
    if token:
        return f"https://{token}.recruitee.com/api/offers/"
    raise ValueError("Recruitee source needs token or careers URL")


def personio_url(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if url and url.endswith(".xml"):
        return url
    if url and "jobs.personio.de" in url:
        parsed = urlparse(url)
        return f"{parsed.scheme or 'https'}://{parsed.netloc}/xml"
    if token:
        return f"https://{token}.jobs.personio.de/xml"
    raise ValueError("Personio source needs token or jobs.personio.de URL")


def smartrecruiters_identifier(source: Dict[str, Any]) -> str:
    token = source.get("token") or ""
    url = source.get("url") or ""
    if token:
        return str(token).strip()
    if url:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if "api.smartrecruiters.com" in parsed.netloc:
            for index, part in enumerate(parts):
                if part == "companies" and index + 1 < len(parts):
                    return parts[index + 1]
        if "smartrecruiters.com" in parsed.netloc and parts:
            return parts[0]
    raise ValueError("SmartRecruiters source needs token or careers/api URL")


def smartrecruiters_company_url(source: Dict[str, Any]) -> str:
    return f"https://api.smartrecruiters.com/v1/companies/{smartrecruiters_identifier(source)}"


def smartrecruiters_list_url(source: Dict[str, Any], *, limit: int, offset: int) -> str:
    return f"{smartrecruiters_company_url(source)}/postings?limit={limit}&offset={offset}"


def smartrecruiters_detail_url(source: Dict[str, Any], posting_id: str) -> str:
    return f"{smartrecruiters_company_url(source)}/postings/{posting_id}"


def _is_locale_segment(value: str) -> bool:
    return re.fullmatch(r"[a-z]{2}(?:-[A-Za-z0-9]{2,})?", value or "") is not None


def workday_config(source: Dict[str, Any]) -> Dict[str, str]:
    token = str(source.get("token") or "").strip()
    url = str(source.get("url") or "").strip()
    if not url:
        raise ValueError("Workday source needs a public careers URL")

    parsed = urlparse(url)
    if "myworkdayjobs.com" not in parsed.netloc:
        raise ValueError("Workday source URL must point to a myworkdayjobs.com site")

    parts = [part for part in parsed.path.split("/") if part]
    if not token:
        token = parsed.netloc.split(".")[0]

    site = ""
    if len(parts) >= 4 and parts[0] == "wday" and parts[1] == "cxs":
        token = parts[2] or token
        site = parts[3]
    else:
        if parts and _is_locale_segment(parts[0]):
            parts = parts[1:]
        if "job" in parts:
            site_index = parts.index("job") - 1
            if site_index >= 0:
                site = parts[site_index]
        elif parts:
            site = parts[0]

    if not site:
        raise ValueError("Could not determine Workday site path from source URL")

    public_base = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    if len([part for part in parsed.path.split('/') if part]) >= 4 and parsed.path.startswith("/wday/cxs/"):
        public_site = ""
    else:
        public_parts = [part for part in parsed.path.split("/") if part]
        if public_parts and _is_locale_segment(public_parts[0]):
            public_parts = public_parts[:2]
        else:
            public_parts = public_parts[:1]
        public_site = f"{public_base}/{'/'.join(public_parts)}" if public_parts else public_base

    return {
        "token": token,
        "site": site,
        "api_base": f"{public_base}/wday/cxs/{token}/{site}",
        "public_site": public_site or public_base,
    }


def workday_jobs_url(source: Dict[str, Any]) -> str:
    return f"{workday_config(source)['api_base']}/jobs"


def workday_detail_url(source: Dict[str, Any], external_path: str) -> str:
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{workday_config(source)['api_base']}{path}"


def workday_public_job_url(source: Dict[str, Any], external_path: str) -> str:
    path = external_path if external_path.startswith("/") else f"/{external_path}"
    return f"{workday_config(source)['public_site']}{path}"


def google_results_url(source: Dict[str, Any]) -> str:
    url = str(source.get("url") or "").strip()
    if url:
        return url
    return "https://www.google.com/about/careers/applications/jobs/results/"


def google_page_url(source: Dict[str, Any], page_num: int) -> str:
    base = google_results_url(source)
    if page_num <= 1:
        return base
    return f"{base}{'&' if '?' in base else '?'}page={page_num}"


def source_url_required(source: Dict[str, Any], label: str) -> str:
    url = str(source.get("url") or "").strip()
    if not url:
        raise ValueError(f"{label} source needs a URL")
    return url


def source_search_text(source: Dict[str, Any], default: str = "software engineer") -> str:
    terms = source.get("search_terms")
    if isinstance(terms, list):
        return " ".join(str(term).strip() for term in terms if str(term).strip()) or default
    if isinstance(terms, str) and terms.strip():
        return terms.strip()
    return default


def source_location_text(source: Dict[str, Any], default: str = "Remote") -> str:
    locations = source.get("locations")
    if isinstance(locations, list):
        return " ".join(str(location).strip() for location in locations if str(location).strip()) or default
    if isinstance(locations, str) and locations.strip():
        return locations.strip()
    return default


def first_search_term(source: Dict[str, Any], default: str = "software engineer") -> str:
    terms = source.get("search_terms")
    if isinstance(terms, list):
        for term in terms:
            text = str(term).strip()
            if text:
                return text
    if isinstance(terms, str) and terms.strip():
        return terms.strip()
    return default


def iso_from_ms(value: Any) -> Optional[str]:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def first_present(*values: Any) -> str:
    for value in values:
        text = value_to_text(value)
        if text:
            return text
    return ""


def make_job_key(ats: str, company: str, source_job_id: str) -> str:
    return f"{ats}:{company.lower()}:{source_job_id}"


def normalize_base(
    source: Dict[str, Any],
    *,
    source_job_id: Any,
    title: str,
    location: str,
    department: str,
    employment_type: str,
    job_url: str,
    apply_url: str,
    published_at: Optional[str],
    updated_at: Optional[str],
    text: str,
    raw: Any,
) -> Dict[str, Any]:
    source_id = str(source_job_id or job_url or title).strip()
    body = combine_text(title, location, department, employment_type, text)
    return {
        "job_key": make_job_key(source["ats"], source["company"], source_id),
        "company": source["company"],
        "ats": source["ats"],
        "source_job_id": source_id,
        "title": compact_ws(title),
        "location": compact_ws(location),
        "department": compact_ws(department),
        "employment_type": compact_ws(employment_type),
        "remote": detect_remote(body, location),
        "job_url": job_url or apply_url,
        "apply_url": apply_url or job_url,
        "published_at": published_at,
        "updated_at": updated_at,
        "text": body,
        "raw": raw,
    }


def smartrecruiters_custom_field(detail: Dict[str, Any], *labels: str) -> str:
    wanted = {label.lower() for label in labels}
    for field in detail.get("customField") or []:
        if not isinstance(field, dict):
            continue
        label = str(field.get("fieldLabel") or "").strip().lower()
        if label in wanted:
            return first_present(field.get("valueLabel"), field.get("value"))
    return ""


def smartrecruiters_sections_text(detail: Dict[str, Any]) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections") or {})
    if isinstance(sections, dict):
        ordered = [
            sections.get("jobDescription"),
            sections.get("qualifications"),
            sections.get("additionalInformation"),
            sections.get("companyDescription"),
        ]
        return combine_text(*ordered)
    if isinstance(sections, list):
        parts: List[str] = []
        for section in sections:
            if isinstance(section, dict):
                parts.append(combine_text(section.get("title"), section.get("text"), section.get("content")))
            else:
                parts.append(value_to_text(section))
        return combine_text(parts)
    return value_to_text(sections)


async def fetch_greenhouse(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, greenhouse_url(source))
    jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
    out: List[Dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        departments = item.get("departments") or []
        offices = item.get("offices") or []
        department = ", ".join(
            filter(None, (value_to_text(d) for d in departments if isinstance(d, dict)))
        )
        office_text = ", ".join(
            filter(None, (value_to_text(o) for o in offices if isinstance(o, dict)))
        )
        location = first_present(item.get("location"), office_text)
        out.append(
            normalize_base(
                source,
                source_job_id=item.get("id") or item.get("internal_job_id"),
                title=first_present(item.get("title")),
                location=location,
                department=department,
                employment_type="",
                job_url=first_present(item.get("absolute_url")),
                apply_url=first_present(item.get("absolute_url")),
                published_at=first_present(item.get("first_published"), item.get("created_at")) or None,
                updated_at=first_present(item.get("updated_at")) or None,
                text=strip_html(item.get("content") or ""),
                raw=item,
            )
        )
    return out


async def fetch_lever(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, lever_url(source))
    jobs = payload if isinstance(payload, list) else []
    out: List[Dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        list_parts: List[str] = []
        for section in item.get("lists") or []:
            if isinstance(section, dict):
                list_parts.append(combine_text(section.get("text"), section.get("content")))
        description = combine_text(
            item.get("descriptionPlain"),
            item.get("description"),
            item.get("additionalPlain"),
            item.get("additional"),
            list_parts,
        )
        out.append(
            normalize_base(
                source,
                source_job_id=item.get("id"),
                title=first_present(item.get("text")),
                location=first_present(categories.get("location")),
                department=first_present(categories.get("team"), categories.get("department")),
                employment_type=first_present(categories.get("commitment")),
                job_url=first_present(item.get("hostedUrl")),
                apply_url=first_present(item.get("applyUrl"), item.get("hostedUrl")),
                published_at=iso_from_ms(item.get("createdAt")),
                updated_at=iso_from_ms(item.get("updatedAt")),
                text=description,
                raw=item,
            )
        )
    return out


async def fetch_ashby(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, ashby_url(source))
    if isinstance(payload, dict):
        jobs = payload.get("jobs") or payload.get("data") or []
    else:
        jobs = payload if isinstance(payload, list) else []
    out: List[Dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        description = combine_text(
            item.get("descriptionPlain"),
            item.get("descriptionHtml"),
            item.get("description"),
        )
        location = first_present(item.get("location"))
        secondary = value_to_text(item.get("secondaryLocations"))
        if secondary:
            location = ", ".join(filter(None, [location, secondary]))
        out.append(
            normalize_base(
                source,
                source_job_id=item.get("id") or item.get("jobId"),
                title=first_present(item.get("title")),
                location=location,
                department=first_present(item.get("department"), item.get("team")),
                employment_type=first_present(item.get("employmentType"), item.get("commitment")),
                job_url=first_present(item.get("jobUrl"), item.get("url")),
                apply_url=first_present(item.get("applyUrl"), item.get("jobUrl"), item.get("url")),
                published_at=first_present(item.get("publishedAt")) or None,
                updated_at=first_present(item.get("updatedAt")) or None,
                text=description,
                raw=item,
            )
        )
    return out


async def fetch_recruitee(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, recruitee_url(source))
    if isinstance(payload, dict):
        jobs = payload.get("offers") or payload.get("jobs") or []
    else:
        jobs = payload if isinstance(payload, list) else []
    out: List[Dict[str, Any]] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        description = combine_text(
            item.get("description"),
            item.get("requirements"),
            item.get("notes"),
        )
        out.append(
            normalize_base(
                source,
                source_job_id=item.get("id") or item.get("slug") or item.get("code"),
                title=first_present(item.get("title"), item.get("name")),
                location=first_present(item.get("location"), item.get("locations")),
                department=first_present(item.get("department"), item.get("team")),
                employment_type=first_present(item.get("employment_type"), item.get("kind")),
                job_url=first_present(item.get("careers_url"), item.get("url")),
                apply_url=first_present(item.get("careers_apply_url"), item.get("careers_url"), item.get("url")),
                published_at=first_present(item.get("published_at"), item.get("created_at")) or None,
                updated_at=first_present(item.get("updated_at")) or None,
                text=description,
                raw=item,
            )
        )
    return out


def element_text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return strip_html("".join(el.itertext()))


def find_child_text(el: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(el):
        tag = child.tag.split("}")[-1].lower()
        if tag in wanted:
            text = element_text(child)
            if text:
                return text
    return ""


def element_to_dict(el: ET.Element) -> Any:
    children = list(el)
    if not children:
        return element_text(el)
    out: Dict[str, Any] = {}
    for child in children:
        key = child.tag.split("}")[-1]
        value = element_to_dict(child)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    return out


async def fetch_personio(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = personio_url(source)
    xml_text = await fetch_text(session, url)
    root = ET.fromstring(xml_text)
    positions = [
        el for el in root.iter() if el.tag.split("}")[-1].lower() in {"position", "job"}
    ]
    parsed = urlparse(url)
    base_url = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    out: List[Dict[str, Any]] = []
    for pos in positions:
        source_job_id = find_child_text(pos, "id", "jobid")
        title = find_child_text(pos, "name", "title")
        descriptions: List[str] = []
        for desc in pos.iter():
            tag = desc.tag.split("}")[-1].lower()
            if tag == "jobdescription":
                section_name = find_child_text(desc, "name", "title")
                value = find_child_text(desc, "value", "description")
                descriptions.append(combine_text(section_name, value))
        if not descriptions:
            descriptions.append(find_child_text(pos, "description", "value"))
        job_url = find_child_text(pos, "url", "joburl", "applicationformurl")
        if not job_url and source_job_id:
            job_url = f"{base_url}/job/{source_job_id}"
        out.append(
            normalize_base(
                source,
                source_job_id=source_job_id,
                title=title,
                location=first_present(find_child_text(pos, "office"), find_child_text(pos, "location")),
                department=find_child_text(pos, "department", "recruitingcategory"),
                employment_type=find_child_text(pos, "employmenttype", "schedule"),
                job_url=job_url,
                apply_url=job_url,
                published_at=find_child_text(pos, "createdat", "publishedat") or None,
                updated_at=find_child_text(pos, "updatedat") or None,
                text=combine_text(descriptions),
                raw=element_to_dict(pos),
            )
        )
    return out


def google_extract_job_links(page_html: str) -> List[str]:
    links: List[str] = []
    for raw in _GOOGLE_LINK_RE.findall(page_html or ""):
        href = unescape(raw)
        if "accounts.google.com" in href:
            continue
        if "jobs/results/" not in href:
            continue
        full = urljoin("https://www.google.com/about/careers/applications/", href).split("?", 1)[0]
        if re.search(r"/jobs/results/\d+", full) and full not in links:
            links.append(full)
    return links


def google_detail_sections(detail_html: str) -> str:
    sections: List[str] = []
    for heading, body in re.findall(
        r"<h3[^>]*>(.*?)</h3>(.*?)(?=<h3[^>]*>|</main>|<footer|</body>)",
        detail_html or "",
        re.IGNORECASE | re.DOTALL,
    ):
        heading_text = compact_ws(strip_html(heading)).rstrip(":")
        wanted = {
            "about the job",
            "minimum qualifications",
            "preferred qualifications",
            "responsibilities",
        }
        if heading_text.lower() not in wanted:
            continue
        body_text = compact_ws(strip_html(body))
        if body_text:
            sections.append(f"{heading_text}\n{body_text}")
    return combine_text(sections)


async def fetch_google(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    detail_links: List[str] = []
    seen_links: set[str] = set()
    page_num = 1
    max_pages = 80
    while page_num <= max_pages:
        page_html = await fetch_text(session, google_page_url(source, page_num))
        page_links = google_extract_job_links(page_html)
        new_links = [link for link in page_links if link not in seen_links]
        if not page_links:
            break
        if page_num > 1 and not new_links:
            break
        for link in new_links:
            seen_links.add(link)
            detail_links.append(link)
        page_num += 1

    detail_limit = asyncio.Semaphore(8)

    async def fetch_one(detail_url: str) -> Optional[Dict[str, Any]]:
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url)
        source_job_id_match = re.search(r"/jobs/results/(\d+)", detail_url)
        location_match = re.search(
            r"preferred working location from the following:\s*<b>(.*?)</b>",
            detail_html,
            re.IGNORECASE | re.DOTALL,
        )
        location = compact_ws(strip_html(location_match.group(1))) if location_match else ""
        title = clean_title_suffix(extract_title_tag(detail_html), " - Google Careers", " â€” Google Careers")
        text = google_detail_sections(detail_html)
        if not text:
            text = combine_text(extract_meta_description(detail_html))
        return normalize_base(
            source,
            source_job_id=source_job_id_match.group(1) if source_job_id_match else detail_url,
            title=title,
            location=location,
            department="",
            employment_type="",
            job_url=detail_url,
            apply_url=detail_url,
            published_at=None,
            updated_at=None,
            text=text,
            raw={"url": detail_url},
        )

    gathered = await asyncio.gather(*(fetch_one(link) for link in detail_links), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


def eightfold_positions_from_html(html_text: str) -> List[Dict[str, Any]]:
    decoded = unescape(html_text or "")
    positions = extract_balanced_json_array(decoded, '"positions":')
    return [item for item in positions if isinstance(item, dict)]


def location_from_job_posting(job_posting: Dict[str, Any]) -> str:
    if not isinstance(job_posting, dict):
        return ""
    location = job_posting.get("jobLocation")
    if isinstance(location, list):
        parts = [location_from_job_posting({"jobLocation": item}) for item in location]
        return ", ".join(part for part in parts if part)
    if not isinstance(location, dict):
        return ""
    address = location.get("address")
    if isinstance(address, dict):
        pieces = [
            value_to_text(address.get("streetAddress")),
            value_to_text(address.get("addressLocality")),
            value_to_text(address.get("addressRegion")),
            value_to_text(address.get("addressCountry")),
        ]
        return ", ".join(piece for piece in pieces if piece)
    return value_to_text(location)


async def fetch_eightfold(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_html = await fetch_text(session, source_url_required(source, "Eightfold"))
    positions = eightfold_positions_from_html(list_html)
    detail_limit = asyncio.Semaphore(6)

    async def fetch_one(position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        detail_url = first_present(position.get("canonicalPositionUrl"), position.get("url"))
        if not detail_url:
            return None
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url)
        job_posting = find_job_posting_json_ld(detail_html)
        detail_positions = eightfold_positions_from_html(detail_html)
        detail_position = detail_positions[0] if detail_positions else {}
        location = first_present(
            location_from_job_posting(job_posting),
            detail_position.get("location") if isinstance(detail_position, dict) else "",
            position.get("location"),
            value_to_text(position.get("locations")),
        )
        work_type = first_present(
            detail_position.get("work_location_option") if isinstance(detail_position, dict) else "",
            position.get("work_location_option"),
        )
        if work_type and work_type.lower() == "remote" and "remote" not in location.lower():
            location = first_present("Remote", location)
        description = first_present(job_posting.get("description"), extract_meta_description(detail_html))
        return normalize_base(
            source,
            source_job_id=first_present(position.get("id"), position.get("ats_job_id"), detail_url),
            title=first_present(job_posting.get("title"), position.get("name"), position.get("posting_name")),
            location=location,
            department=first_present(position.get("department"), detail_position.get("department") if isinstance(detail_position, dict) else ""),
            employment_type=first_present(job_posting.get("employmentType")),
            job_url=detail_url,
            apply_url=first_present(job_posting.get("url"), detail_url),
            published_at=first_present(job_posting.get("datePosted")) or iso_from_seconds(position.get("t_create")),
            updated_at=iso_from_seconds(position.get("t_update")),
            text=description,
            raw={"list": position, "detail": detail_position, "job_posting": job_posting},
        )

    gathered = await asyncio.gather(*(fetch_one(position) for position in positions), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


async def fetch_optiver(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_html = await fetch_text(session, source_url_required(source, "Optiver"))
    detail_urls = [
        f"https://optiver.com/working-at-optiver/career-opportunities/{job_id}/"
        for job_id in sorted(set(_OPTIVER_LINK_RE.findall(list_html)))
    ]
    detail_limit = asyncio.Semaphore(6)

    async def fetch_one(detail_url: str) -> Optional[Dict[str, Any]]:
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url)
        article_match = re.search(r"<article[^>]*>(.*?)</article>", detail_html, re.IGNORECASE | re.DOTALL)
        article_text = strip_html(article_match.group(1)) if article_match else ""
        source_job_id_match = re.search(r"/career-opportunities/(\d+)/", detail_url)
        title = first_present(
            extract_first_h1(detail_html),
            clean_title_suffix(extract_title_tag(detail_html), " - Optiver", " | Optiver"),
        )
        return normalize_base(
            source,
            source_job_id=source_job_id_match.group(1) if source_job_id_match else detail_url,
            title=title,
            location=first_present(
                re.search(r'"office":\["([^"]+)"\]', detail_html).group(1)
                if re.search(r'"office":\["([^"]+)"\]', detail_html)
                else "",
            ),
            department=first_present(
                re.search(r'"department":\["([^"]+)"\]', detail_html).group(1)
                if re.search(r'"department":\["([^"]+)"\]', detail_html)
                else "",
            ),
            employment_type="",
            job_url=detail_url,
            apply_url=detail_url,
            published_at=None,
            updated_at=None,
            text=combine_text(extract_meta_description(detail_html), article_text),
            raw={"url": detail_url},
        )

    gathered = await asyncio.gather(*(fetch_one(url) for url in detail_urls), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


async def fetch_drw(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    list_html = await fetch_text(session, source_url_required(source, "DRW"))
    slugs = sorted(set(_DRW_SLUG_RE.findall(list_html)))
    detail_urls = [urljoin(source_url_required(source, "DRW"), slug) for slug in slugs]
    detail_limit = asyncio.Semaphore(6)

    async def fetch_one(detail_url: str) -> Optional[Dict[str, Any]]:
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url)
        slug = detail_url.rstrip("/").rsplit("/", 1)[-1]
        content_html = extract_html_block_after_marker(
            detail_html,
            '<div class="mt-6 prose',
            end_markers=["</main>", "</article>", '<section class="relative bg-sand-100">'],
        )
        source_job_id_match = re.search(r"-(\d+)$", slug)
        title = first_present(extract_first_h1(detail_html), extract_title_tag(detail_html))
        return normalize_base(
            source,
            source_job_id=source_job_id_match.group(1) if source_job_id_match else slug,
            title=title,
            location=extract_label_value(detail_html, "Job Location"),
            department=extract_label_value(detail_html, "Department"),
            employment_type=extract_label_value(detail_html, "Employment type"),
            job_url=detail_url,
            apply_url=detail_url,
            published_at=None,
            updated_at=None,
            text=strip_html(content_html),
            raw={"url": detail_url},
        )

    gathered = await asyncio.gather(*(fetch_one(url) for url in detail_urls), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


async def fetch_gresearch(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    base_url = source_url_required(source, "G-Research").rstrip("/") + "/"
    detail_links: List[str] = []
    seen_links: set[str] = set()
    page_num = 1
    max_pages = 20
    while page_num <= max_pages:
        page_url = base_url if page_num == 1 else urljoin(base_url, f"page/{page_num}/")
        page_html = await fetch_text(session, page_url)
        links = [
            link
            for link in _GRESEARCH_LINK_RE.findall(page_html)
            if "/page/" not in link and link not in seen_links
        ]
        if not links:
            break
        for link in links:
            seen_links.add(link)
            detail_links.append(link)
        page_num += 1

    detail_limit = asyncio.Semaphore(6)

    async def fetch_one(detail_url: str) -> Optional[Dict[str, Any]]:
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url)
        job_posting = find_job_posting_json_ld(detail_html)
        slug = detail_url.rstrip("/").rsplit("/", 1)[-1]
        identifier = job_posting.get("identifier") if isinstance(job_posting, dict) else {}
        if not isinstance(identifier, dict):
            identifier = {}
        title = first_present(
            job_posting.get("title") if isinstance(job_posting, dict) else "",
            extract_first_h1(detail_html),
            clean_title_suffix(extract_title_tag(detail_html), " | G-Research"),
        )
        location = location_from_job_posting(job_posting)
        if location.startswith(", "):
            location = location[2:]
        return normalize_base(
            source,
            source_job_id=first_present(identifier.get("value"), slug),
            title=title,
            location=location,
            department=first_present(job_posting.get("industry")),
            employment_type=first_present(job_posting.get("employmentType")),
            job_url=detail_url,
            apply_url=detail_url,
            published_at=first_present(job_posting.get("datePosted")) or None,
            updated_at=first_present(job_posting.get("validThrough")) or None,
            text=first_present(job_posting.get("description"), extract_meta_description(detail_html)),
            raw={"job_posting": job_posting, "url": detail_url},
        )

    gathered = await asyncio.gather(*(fetch_one(link) for link in detail_links), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


def discover_json_job_objects(payload: Any) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        keys = {str(key).lower() for key in value.keys()}
        has_title = bool(keys & {"title", "jobtitle", "job_title", "name", "posting_name"})
        has_id = bool(keys & {"id", "jobid", "job_id", "jobreqid", "requisitionid", "reqid", "ats_job_id"})
        has_url = bool(keys & {"url", "joburl", "job_url", "canonicalpositionurl", "externalpath"})
        has_location = bool(keys & {"location", "locations", "locationname", "primarylocation"})
        if has_title and (has_id or has_url) and (has_location or "description" in keys or "jobdescription" in keys):
            found.append(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                walk(child)

    walk(payload)
    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in found:
        key = first_present(
            item.get("id"),
            item.get("jobId"),
            item.get("job_id"),
            item.get("jobReqId"),
            item.get("requisitionId"),
            item.get("ats_job_id"),
            item.get("url"),
            item.get("jobUrl"),
            item.get("canonicalPositionUrl"),
            item.get("title"),
            item.get("name"),
        )
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def normalize_discovered_json_job(
    source: Dict[str, Any],
    item: Dict[str, Any],
    *,
    source_url: str,
) -> Dict[str, Any]:
    job_url = first_present(
        item.get("jobUrl"),
        item.get("job_url"),
        item.get("url"),
        item.get("canonicalPositionUrl"),
        item.get("externalUrl"),
    )
    if job_url and job_url.startswith("/"):
        job_url = urljoin(source_url, job_url)
    location = first_present(
        item.get("location"),
        item.get("locations"),
        item.get("locationName"),
        item.get("primaryLocation"),
        item.get("jobLocation"),
    )
    description = combine_text(
        item.get("description"),
        item.get("jobDescription"),
        item.get("job_description"),
        item.get("summary"),
        item.get("qualifications"),
        item.get("responsibilities"),
    )
    company_data = item.get("company") if isinstance(item.get("company"), dict) else {}
    hiring_org = item.get("hiringOrganization") if isinstance(item.get("hiringOrganization"), dict) else {}
    company_name = first_present(
        item.get("companyName"),
        item.get("company_name"),
        item.get("organizationName"),
        company_data.get("name") if isinstance(company_data, dict) else "",
        hiring_org.get("name") if isinstance(hiring_org, dict) else "",
    )
    job = normalize_base(
        source,
        source_job_id=first_present(
            item.get("id"),
            item.get("jobId"),
            item.get("job_id"),
            item.get("jobReqId"),
            item.get("requisitionId"),
            item.get("reqId"),
            item.get("ats_job_id"),
            job_url,
        ),
        title=first_present(item.get("title"), item.get("jobTitle"), item.get("job_title"), item.get("name"), item.get("posting_name")),
        location=location,
        department=first_present(item.get("department"), item.get("team"), item.get("category"), item.get("jobFamily")),
        employment_type=first_present(item.get("employmentType"), item.get("type"), item.get("timeType")),
        job_url=job_url or source_url,
        apply_url=first_present(item.get("applyUrl"), item.get("apply_url"), job_url, source_url),
        published_at=first_present(item.get("datePosted"), item.get("postedDate"), item.get("createdAt"), item.get("postedOn")) or None,
        updated_at=first_present(item.get("updatedAt"), item.get("modifiedDate")) or None,
        text=description,
        raw=item,
    )
    return set_job_company(job, company_name) if company_name else job


def set_job_company(job: Dict[str, Any], company_name: str) -> Dict[str, Any]:
    clean = compact_ws(company_name)
    if not clean:
        return job
    source_job_id = str(job.get("source_job_id") or job.get("job_url") or job.get("title") or "")
    job["company"] = clean
    job["job_key"] = make_job_key(str(job.get("ats") or ""), clean, source_job_id)
    return job


def normalize_json_ld_job(source: Dict[str, Any], item: Dict[str, Any], *, source_url: str) -> Dict[str, Any]:
    identifier = item.get("identifier")
    if not isinstance(identifier, dict):
        identifier = {}
    job = normalize_base(
        source,
        source_job_id=first_present(identifier.get("value"), item.get("jobIdentifier"), item.get("url"), source_url),
        title=first_present(item.get("title"), item.get("name")),
        location=location_from_job_posting(item),
        department=first_present(item.get("industry"), item.get("occupationalCategory")),
        employment_type=first_present(item.get("employmentType")),
        job_url=first_present(item.get("url"), source_url),
        apply_url=first_present(item.get("url"), source_url),
        published_at=first_present(item.get("datePosted")) or None,
        updated_at=first_present(item.get("validThrough")) or None,
        text=first_present(item.get("description"), item.get("responsibilities"), item.get("qualifications")),
        raw=item,
    )
    hiring_org = item.get("hiringOrganization") if isinstance(item.get("hiringOrganization"), dict) else {}
    return set_job_company(job, first_present(hiring_org.get("name")))


def portal_entry_url(source: Dict[str, Any]) -> str:
    entry = str(source.get("entry_url") or source.get("url") or "").strip()
    if not entry:
        raise ValueError(f"Portal source is missing entry URL: {source.get('company')}")
    return entry


def portal_source_job_id(portal: str, url: str) -> str:
    parsed = urlparse(url)
    portal = str(portal or "").strip().lower()
    if portal == "wellfound":
        parts = [part for part in parsed.path.split("/") if part]
        return parts[-1] if parts else url
    return url


def portal_company_location_from_title(portal: str, title_tag: str) -> tuple[str, str, str]:
    title_tag = compact_ws(title_tag)
    if not title_tag:
        return "", "", ""
    if portal == "wellfound":
        match = re.match(r"^(.*?) at (.*?) [\u2022\-] (.*?) \| Wellfound$", title_tag, re.IGNORECASE)
        if match:
            return compact_ws(match.group(1)), compact_ws(match.group(2)), compact_ws(match.group(3))
    return title_tag, "", ""


def portal_html_field(html_text: str, *patterns: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, html_text or "", re.IGNORECASE | re.DOTALL)
        if match:
            return compact_ws(strip_html(match.group(1)))
    return ""


def portal_links_from_render(portal: str, render: BrowserRenderResult) -> List[str]:
    html_text = render.html or ""
    raw_links = _WELLFOUND_LINK_RE.findall(html_text) if portal == "wellfound" else []
    out: List[str] = []
    seen: set[str] = set()
    for href in raw_links:
        url = urljoin(render.final_url, unquote(href))
        if portal == "wellfound" and "/jobs/" not in url:
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def first_nonempty_line(text: str) -> str:
    """Return the first non-empty line from a text block for compact HN headers."""
    for raw in str(text or "").splitlines():
        line = compact_ws(raw)
        if line:
            return line
    return ""


def first_url_in_text(text: str) -> str:
    """Extract the first HTTP(S) URL from freeform text."""
    match = _URL_IN_TEXT_RE.search(str(text or ""))
    if not match:
        return ""
    return str(match.group(1)).rstrip(").,")


def clean_hackernews_label(value: str) -> str:
    """Normalize lightweight markdown and punctuation from HN header labels."""
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", text)
    text = text.strip(" -*\t")
    return compact_ws(text)


def hackernews_company_from_url(url: str) -> str:
    """Infer a readable company name from a comment URL when the header omits it."""
    host = urlparse(str(url or "")).netloc.lower().strip()
    if not host:
        return ""
    host = re.sub(r"^www\.", "", host)
    labels = [label for label in host.split(".") if label]
    if not labels:
        return ""
    if len(labels) >= 2 and labels[-1] in {"com", "io", "ai", "dev", "org", "net", "co", "tech", "jobs"}:
        candidate = labels[-2]
    else:
        candidate = labels[0]
    generic_hosts = {
        "ashbyhq",
        "applytojob",
        "boards",
        "builtin",
        "example",
        "getro",
        "github",
        "grnh",
        "greenhouse",
        "job-boards",
        "jobs",
        "lever",
        "smartrecruiters",
        "wellfound",
        "workable",
        "workday",
        "ycombinator",
    }
    if candidate in generic_hosts:
        return ""
    words = [part for part in re.split(r"[-_]+", candidate) if part]
    if not words:
        return ""
    return " ".join(word.upper() if len(word) <= 3 else word.capitalize() for word in words)


def hackernews_part_is_location(part: str) -> bool:
    """Return True when one HN header segment looks like a location marker."""
    low = str(part or "").strip().lower()
    if not low:
        return False
    location_terms = ("remote", "hybrid", "on-site", "onsite", "anywhere", "relocate", "relocation")
    if any(term in low for term in location_terms):
        return True
    if "," in low:
        return True
    if " or " in low or " / " in low:
        return True
    country_terms = ("us", "usa", "uk", "eu", "europe", "canada", "germany", "france", "india", "singapore", "australia")
    if any(token in low for token in country_terms) and ("(" in low or ")" in low):
        return True
    return False


def hackernews_part_is_compensation(part: str) -> bool:
    """Return True when one HN header segment looks like salary or equity text."""
    low = str(part or "").strip().lower()
    if not low:
        return False
    return "$" in low or "usd" in low or "eur" in low or "salary" in low or "equity" in low


def hackernews_part_is_employment(part: str) -> bool:
    """Return True when one HN header segment describes employment type."""
    return _HN_EMPLOYMENT_RE.search(str(part or "")) is not None


def hackernews_part_is_role(part: str) -> bool:
    """Return True when one HN header segment looks like a job title."""
    low = str(part or "").strip().lower()
    if not low:
        return False
    if hackernews_part_is_location(low) or hackernews_part_is_employment(low) or hackernews_part_is_compensation(low):
        return False
    return _HN_ROLE_RE.search(low) is not None


def hackernews_comment_looks_like_job(comment_text: str, apply_url: str) -> bool:
    """Return True when a HN top-level comment looks like a hiring post."""
    header = first_nonempty_line(comment_text)
    header_low = header.lower()
    if not header:
        return False
    if "comments as of" in header_low or "thread opened" in header_low:
        return False
    if "who is hiring" in header_low:
        return False
    parts = [clean_hackernews_label(part) for part in header.split("|") if clean_hackernews_label(part)]
    if len(parts) >= 2 and any(
        hackernews_part_is_role(part)
        or hackernews_part_is_location(part)
        or hackernews_part_is_employment(part)
        for part in parts
    ):
        return True
    text_low = str(comment_text or "").lower()
    if apply_url and ("we're hiring" in text_low or "we are hiring" in text_low or "apply" in text_low):
        return True
    if apply_url and _HN_ROLE_RE.search(comment_text):
        return True
    return False


HACKERNEWS_PARSE_SYSTEM_PROMPT = """
You normalize Hacker News Who Is Hiring comments into structured job metadata.

Rules:
- Use the first meaningful line as the main header, but inspect the whole comment when the header is ambiguous.
- Return should_keep=false only for comments that are clearly not job posts.
- Extract only one primary company, title, location, and employment_type.
- Prefer direct company names from the comment over generic ATS hostnames.
- If a field is unknown, return an empty string for that field.
- Keep the output short, factual, and JSON-only.
""".strip()


def _hackernews_parse_schema() -> Dict[str, Any]:
    """Return the strict JSON schema used for LLM-based HN normalization."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "should_keep": {"type": "boolean"},
            "company": {"type": "string"},
            "title": {"type": "string"},
            "location": {"type": "string"},
            "employment_type": {"type": "string"},
            "confidence": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["should_keep", "company", "title", "location", "employment_type", "confidence", "reason"],
    }


def resolve_hackernews_parser_engine(requested: str) -> str:
    """Resolve the requested HN parser engine into a usable runtime choice."""
    engine = str(requested or "auto").strip().lower() or "auto"
    if engine == "local_ai":
        return "local_ai" if bool(ai.local_ai_status().get("ready")) else "local"
    if engine == "openai":
        return "openai" if ai.openai_available() else "local"
    if engine == "auto":
        if bool(ai.local_ai_status().get("ready")):
            return "local_ai"
        if ai.openai_available():
            return "openai"
        return "local"
    return "local"


def hackernews_parse_needs_llm(parsed: Dict[str, str], comment_text: str, apply_url: str) -> bool:
    """Return True when the heuristic HN parse is weak enough to justify LLM repair."""
    header            = first_nonempty_line(comment_text)
    inferred_company  = hackernews_company_from_url(apply_url)
    company           = str(parsed.get("company") or "").strip()
    title             = str(parsed.get("title") or "").strip()
    normalized_company = company.lower()
    normalized_title   = title.lower()
    if not company or normalized_company in {"hacker news", "careers", "jobs", "apply"}:
        return True
    if not title or normalized_title in {"hn hiring post", company.lower()}:
        return True
    if len(title) < 8:
        return True
    if inferred_company and company.lower() == inferred_company.lower() and "|" not in header:
        return True
    if "|" in header and not parsed.get("location") and "remote" in header.lower():
        return True
    return False


def _hackernews_llm_prompt(comment_text: str, apply_url: str, heuristic: Dict[str, str]) -> str:
    """Build the prompt body sent to the local or remote model for HN parsing."""
    payload = {
        "apply_url": apply_url,
        "header": first_nonempty_line(comment_text),
        "comment_text": comment_text[:4000],
        "heuristic_parse": heuristic,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


async def maybe_upgrade_hackernews_parse(
    comment_text: str,
    apply_url: str,
    parsed: Dict[str, str],
    *,
    engine: str,
) -> tuple[Dict[str, str], Dict[str, str], bool]:
    """Optionally repair weak HN parses through Local AI or OpenAI."""
    resolved_engine = str(engine or "local").strip().lower() or "local"
    if resolved_engine not in {"local", "local_ai", "openai"}:
        resolved_engine = resolve_hackernews_parser_engine(resolved_engine)
    if resolved_engine == "local" or not hackernews_parse_needs_llm(parsed, comment_text, apply_url):
        return parsed, {"parser_engine": "local", "parser_confidence": "heuristic", "parser_reason": "heuristic parse retained"}, True
    try:
        prompt = _hackernews_llm_prompt(comment_text, apply_url, parsed)
        if resolved_engine == "local_ai":
            payload = await asyncio.to_thread(
                ai.call_local_ai_json,
                system_prompt=HACKERNEWS_PARSE_SYSTEM_PROMPT,
                user_prompt=prompt,
                source_label="Local AI",
                timeout=60.0,
            )
        else:
            payload = await asyncio.to_thread(
                ai.call_openai_json,
                system_prompt=HACKERNEWS_PARSE_SYSTEM_PROMPT,
                user_prompt=prompt,
                schema_name="hackernews_comment_parse",
                schema=_hackernews_parse_schema(),
                source_label="OpenAI",
                timeout=60.0,
                max_output_tokens=1200,
            )
        keep = bool(payload.get("should_keep", True))
        merged = dict(parsed)
        for key in ("company", "title", "location", "employment_type"):
            value = compact_text(clean_hackernews_label(str(payload.get(key) or "")), limit=140 if key == "title" else 120)
            if value:
                merged[key] = value
        meta = {
            "parser_engine": resolved_engine,
            "parser_confidence": compact_text(str(payload.get("confidence") or "llm"), limit=40),
            "parser_reason": compact_text(str(payload.get("reason") or "LLM upgraded heuristic parse."), limit=220),
        }
        return merged, meta, keep
    except Exception as exc:
        return parsed, {
            "parser_engine": "local",
            "parser_confidence": "heuristic",
            "parser_reason": compact_text(f"LLM parser fallback: {exc}", limit=220),
        }, True


def parse_hackernews_comment_header(comment_text: str, apply_url: str) -> Dict[str, str]:
    """Best-effort split of a HN hiring comment into company/title/location fields."""
    header = first_nonempty_line(comment_text)
    inferred_company = hackernews_company_from_url(apply_url)
    if not header:
        return {
            "company": inferred_company,
            "title": inferred_company or "HN hiring post",
            "location": "",
            "employment_type": "",
        }
    parts = [clean_hackernews_label(part) for part in header.split("|") if clean_hackernews_label(part)]
    if not parts:
        return {
            "company": inferred_company,
            "title": compact_text(header, limit=140),
            "location": "Remote" if "remote" in header.lower() else "",
            "employment_type": "",
        }

    company = ""
    title = ""
    location = ""
    employment_type = ""

    first = parts[0]
    if hackernews_part_is_location(first) or hackernews_part_is_employment(first) or hackernews_part_is_compensation(first):
        company = inferred_company
    elif hackernews_part_is_role(first) and inferred_company:
        company = inferred_company
        title = first
    else:
        company = first

    for part in parts[1:]:
        if not location and hackernews_part_is_location(part):
            location = part
            continue
        if not employment_type and hackernews_part_is_employment(part):
            employment_type = part
            continue
        if not title and hackernews_part_is_role(part):
            title = part
            continue

    if not title:
        fallback_parts = [
            part
            for part in parts[1:]
            if not hackernews_part_is_location(part)
            and not hackernews_part_is_employment(part)
            and not hackernews_part_is_compensation(part)
            and "http://" not in part.lower()
            and "https://" not in part.lower()
        ]
        if fallback_parts:
            title = fallback_parts[0]
        elif hackernews_part_is_role(first):
            title = first
        elif len(parts) > 1 and inferred_company and company.lower() == inferred_company.lower():
            title = parts[1]
        else:
            title = first

    if not company:
        company = inferred_company or "Hacker News"
    if company and inferred_company and hackernews_part_is_role(company):
        company = inferred_company
    if not location and "remote" in header.lower():
        location = "Remote"

    return {
        "company": compact_text(clean_hackernews_label(company), limit=120),
        "title": compact_text(clean_hackernews_label(title), limit=140),
        "location": compact_text(clean_hackernews_label(location), limit=120),
        "employment_type": compact_text(clean_hackernews_label(employment_type), limit=80),
    }


async def fetch_hackernews_hiring(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Fetch the latest HN Who Is Hiring thread and normalize top-level comments as jobs.

    This adapter intentionally reuses the repo-local `hn_topic_extractor.py`
    search and item helpers instead of introducing a second HN fetch path.
    """
    topic = str(source.get("token") or "who is hiring").strip() or "who is hiring"
    since_epoch = int(datetime.now(tz=timezone.utc).timestamp()) - (120 * 24 * 60 * 60)
    hits = await hn_topic.search_stories(
        session,
        topic=topic,
        max_pages=4,
        hits_per_page=100,
        since_epoch=since_epoch,
    )
    if not hits:
        raise SourceStatusError("manual_review", f"No recent Hacker News stories matched topic={topic!r}.")
    latest_story = None
    story_item = None
    comment_ids: List[int] = []
    for hit in sorted(hits, key=lambda item: item.created_at, reverse=True):
        title = str(hit.title or "").strip().lower()
        is_monthly_hiring_thread = bool(re.search(r"\bask hn:\s*who is hiring\?", title))
        if not is_monthly_hiring_thread and ("show hn:" in title or "who wants to be hired" in title):
            continue
        candidate_item = await hn_topic.fetch_item(session, hit.id)
        if not candidate_item or candidate_item.get("type") != "story":
            continue
        candidate_comment_ids = hn_topic.top_level_comment_ids(candidate_item)
        if not candidate_comment_ids:
            continue
        if not is_monthly_hiring_thread and len(candidate_comment_ids) < 25:
            continue
        latest_story = hit
        story_item = candidate_item
        comment_ids = candidate_comment_ids
        break
    if latest_story is None or story_item is None:
        raise SourceStatusError("manual_review", f"No recent Hacker News hiring thread with comments matched topic={topic!r}.")
    if not comment_ids:
        return []
    parser_engine = resolve_hackernews_parser_engine(str(source.get("_hn_parser_engine") or "auto"))

    semaphore = asyncio.Semaphore(64)

    async def fetch_comment(comment_id: int) -> Optional[Dict[str, Any]]:
        async with semaphore:
            item = await hn_topic.fetch_item(session, comment_id)
        if not item or item.get("type") != "comment":
            return None
        if item.get("dead") or item.get("deleted"):
            return None
        raw_text = hn_topic.strip_html(str(item.get("text") or ""))
        text = compact_ws(raw_text) if "\n" not in raw_text else raw_text.strip()
        if len(compact_ws(text)) < 24:
            return None
        published_at = iso_from_seconds(item.get("time"))
        comment_url = f"https://news.ycombinator.com/item?id={comment_id}"
        external_url = first_url_in_text(text)
        apply_url = external_url or comment_url
        if not hackernews_comment_looks_like_job(text, apply_url):
            return None
        parsed = parse_hackernews_comment_header(text, apply_url)
        parsed, parser_meta, keep = await maybe_upgrade_hackernews_parse(
            text,
            apply_url,
            parsed,
            engine=parser_engine,
        )
        if not keep:
            return None
        raw = {
            "story_id": latest_story.id,
            "story_title": latest_story.title,
            "story_url": latest_story.url,
            "comment_id": comment_id,
            "comment_author": str(item.get("by") or ""),
            "parsed_company": parsed["company"],
            "parsed_title": parsed["title"],
            "parsed_location": parsed["location"],
            "text": text,
            "parser_engine": parser_meta.get("parser_engine"),
            "parser_confidence": parser_meta.get("parser_confidence"),
            "parser_reason": parser_meta.get("parser_reason"),
        }
        job = normalize_base(
            source,
            source_job_id=comment_id,
            title=parsed["title"],
            location=parsed["location"],
            department="Hacker News",
            employment_type=parsed["employment_type"],
            job_url=comment_url,
            apply_url=apply_url,
            published_at=published_at,
            updated_at=None,
            text=combine_text(
                latest_story.title,
                f"HN thread: {latest_story.id}",
                text,
            ),
            raw=raw,
        )
        job["company"] = parsed["company"] or source["company"]
        return job

    gathered = await asyncio.gather(*(fetch_comment(comment_id) for comment_id in comment_ids), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for item in gathered:
        if isinstance(item, dict):
            out.append(item)
    if not out:
        raise SourceStatusError("blocked_skipped", f"Hacker News story {latest_story.id} rendered no usable hiring comments.")
    return out


def normalize_portal_detail(source: Dict[str, Any], portal: str, detail_url: str, render: BrowserRenderResult) -> Optional[Dict[str, Any]]:
    json_ld = find_job_posting_json_ld(render.html)
    if json_ld:
        job = normalize_json_ld_job(source, json_ld, source_url=detail_url)
    else:
        title_tag = extract_title_tag(render.html)
        fallback_title, fallback_company, fallback_location = portal_company_location_from_title(portal, title_tag)
        title = first_present(
            extract_first_h1(render.html),
            portal_html_field(render.html, r'"jobTitle"\s*:\s*"([^"]+)"', r'"title"\s*:\s*"([^"]+)"'),
            fallback_title,
        )
        company_name = first_present(
            portal_html_field(
                render.html,
                r'"companyName"\s*:\s*"([^"]+)"',
                r'"orgName"\s*:\s*"([^"]+)"',
                r'"hiringOrganization"\s*:\s*\{[^\}]*"name"\s*:\s*"([^"]+)"',
            ),
            fallback_company,
        )
        location = first_present(
            portal_html_field(render.html, r'"formattedLocation"\s*:\s*"([^"]+)"', r'"location"\s*:\s*"([^"]+)"'),
            fallback_location,
        )
        text = combine_text(extract_meta_description(render.html), strip_html(render.html)[:12000])
        job = normalize_base(
            source,
            source_job_id=portal_source_job_id(portal, detail_url),
            title=title,
            location=location,
            department="",
            employment_type="",
            job_url=detail_url,
            apply_url=detail_url,
            published_at=None,
            updated_at=None,
            text=text,
            raw={"html_title": title_tag, "portal": portal, "detail_url": detail_url},
        )
        job = set_job_company(job, company_name)
    if not job.get("title"):
        return None
    if portal == "wellfound":
        job = set_job_company(job, str(job.get("company") or source.get("company") or ""))
    return job


async def fetch_remoteok_api(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, source_url_required(source, "RemoteOK"))
    items = payload if isinstance(payload, list) else []
    out: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id") or not item.get("position"):
            continue
        salary_min = first_present(item.get("salary_min"))
        salary_max = first_present(item.get("salary_max"))
        salary = ""
        if salary_min and salary_min != "0" and salary_max and salary_max != "0":
            salary = f"Salary: {salary_min}-{salary_max}"
        job = normalize_base(
            source,
            source_job_id=first_present(item.get("id"), item.get("slug")),
            title=first_present(item.get("position")),
            location=first_present(item.get("location"), "Remote"),
            department=first_present(item.get("tags")),
            employment_type="",
            job_url=first_present(item.get("url"), item.get("apply_url")),
            apply_url=first_present(item.get("apply_url"), item.get("url")),
            published_at=first_present(item.get("date")) or iso_from_seconds(item.get("epoch")),
            updated_at=None,
            text=combine_text(item.get("description"), salary, item.get("tags")),
            raw=item,
        )
        out.append(set_job_company(job, first_present(item.get("company"))))
    return out


async def fetch_remotive_api(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, source_url_required(source, "Remotive"))
    items = payload.get("jobs") if isinstance(payload, dict) else []
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        salary = first_present(item.get("salary"))
        job = normalize_base(
            source,
            source_job_id=first_present(item.get("id"), item.get("url")),
            title=first_present(item.get("title")),
            location=first_present(item.get("candidate_required_location"), "Remote"),
            department=first_present(item.get("category"), item.get("tags")),
            employment_type=first_present(item.get("job_type")),
            job_url=first_present(item.get("url")),
            apply_url=first_present(item.get("url")),
            published_at=first_present(item.get("publication_date")) or None,
            updated_at=None,
            text=combine_text(item.get("description"), salary, item.get("tags")),
            raw=item,
        )
        out.append(set_job_company(job, first_present(item.get("company_name"))))
    return out


async def fetch_weworkremotely_rss(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    xml_text = await fetch_text(session, source_url_required(source, "We Work Remotely RSS"))
    root = ET.fromstring(xml_text)
    out: List[Dict[str, Any]] = []
    for item in root.iter():
        if item.tag.split("}")[-1].lower() != "item":
            continue
        title_text = find_child_text(item, "title")
        company_name = ""
        title = title_text
        if ":" in title_text:
            company_name, title = (part.strip() for part in title_text.split(":", 1))
        link = find_child_text(item, "link")
        guid = find_child_text(item, "guid")
        description = find_child_text(item, "description")
        category = find_child_text(item, "category")
        job = normalize_base(
            source,
            source_job_id=first_present(guid, link, title_text),
            title=title,
            location="Remote",
            department=category,
            employment_type="",
            job_url=link,
            apply_url=link,
            published_at=find_child_text(item, "pubDate") or None,
            updated_at=None,
            text=combine_text(description, category),
            raw=element_to_dict(item),
        )
        out.append(set_job_company(job, company_name))
    return out


def powertofly_search_url(source: Dict[str, Any]) -> str:
    base = source_url_required(source, "PowerToFly").split("?", 1)[0].rstrip("/") + "/"
    fields = ",".join(
        [
            "id",
            "title",
            "headline",
            "description",
            "location",
            "levels_of_experience",
            "employment_type",
            "published_on",
            "display_company_name",
            "company",
            "required_skills",
            "secondary_locations",
            "country",
            "state",
            "city",
            "custom_apply_url",
            "salary",
            "qualifications",
            "editorial_description",
        ]
    )
    params = {
        "keywords": first_search_term(source),
        "location": source_location_text(source),
        "page": "1",
        "per_page": "50",
        "fields": fields,
    }
    return f"{base}?{urlencode(params)}"


async def fetch_powertofly_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, powertofly_search_url(source))
    items = payload.get("data") if isinstance(payload, dict) else []
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        company_name = first_present(item.get("display_company_name"), company.get("name"))
        location = first_present(
            item.get("location"),
            ", ".join(part for part in [first_present(item.get("city")), first_present(item.get("state")), first_present(item.get("country"))] if part),
            item.get("secondary_locations"),
        )
        job_url = urljoin("https://powertofly.com", f"/jobs/detail/{item.get('id')}")
        job = normalize_base(
            source,
            source_job_id=first_present(item.get("id"), job_url),
            title=first_present(item.get("title")),
            location=location,
            department=first_present(item.get("levels_of_experience")),
            employment_type=first_present(item.get("employment_type")),
            job_url=job_url,
            apply_url=first_present(item.get("custom_apply_url"), job_url),
            published_at=first_present(item.get("published_on")) or None,
            updated_at=None,
            text=combine_text(item.get("headline"), item.get("description"), item.get("qualifications"), item.get("salary"), item.get("required_skills")),
            raw=item,
        )
        out.append(set_job_company(job, company_name))
    return out


def authenticjobs_url(source: Dict[str, Any], page: int) -> str:
    url = source_url_required(source, "Authentic Jobs")
    if "wp-json" in url:
        base = url
    else:
        base = "https://authenticjobs.com/wp-json/wp/v2/job-listings?per_page=100"
    separator = "&" if "?" in base else "?"
    if "search=" not in base:
        base = f"{base}{separator}{urlencode({'search': first_search_term(source)})}"
        separator = "&"
    return f"{base}{separator}page={page}"


async def fetch_authenticjobs_wp(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(1, 4):
        try:
            payload = await fetch_json(session, authenticjobs_url(source, page), retries=1)
        except Exception:
            if page == 1:
                raise
            break
        items = payload if isinstance(payload, list) else []
        if not items:
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            title = first_present((item.get("title") or {}).get("rendered") if isinstance(item.get("title"), dict) else item.get("title"))
            content = first_present((item.get("content") or {}).get("rendered") if isinstance(item.get("content"), dict) else item.get("content"))
            job = normalize_base(
                source,
                source_job_id=first_present(item.get("id"), item.get("link")),
                title=title,
                location=first_present(meta.get("_job_location"), "Remote" if meta.get("_remote_position") else ""),
                department=first_present(item.get("job-categories"), item.get("job_listing_tag")),
                employment_type=first_present(item.get("job-types")),
                job_url=first_present(item.get("link")),
                apply_url=first_present(meta.get("_application"), item.get("link")),
                published_at=first_present(item.get("date"), item.get("date_gmt")) or None,
                updated_at=first_present(item.get("modified"), item.get("modified_gmt")) or None,
                text=combine_text(content, meta.get("_job_salary")),
                raw=item,
            )
            out.append(set_job_company(job, first_present(meta.get("_company_name"))))
        if len(items) < 100:
            break
    return out


def jobs_from_browser_render(source: Dict[str, Any], render: BrowserRenderResult) -> List[Dict[str, Any]]:
    jobs: List[Dict[str, Any]] = []
    for response in render.json_responses:
        payload = response.get("payload")
        for item in discover_json_job_objects(payload):
            normalized = normalize_discovered_json_job(source, item, source_url=render.final_url)
            if normalized.get("source_job_id") and normalized.get("title"):
                jobs.append(normalized)

    if not jobs:
        json_ld_jobs = [
            node
            for obj in extract_json_ld_objects(render.html)
            for node in iter_json_nodes(obj)
            if isinstance(node, dict) and (
                node.get("@type") == "JobPosting"
                or (isinstance(node.get("@type"), list) and "JobPosting" in node.get("@type", []))
            )
        ]
        jobs = [normalize_json_ld_job(source, item, source_url=render.final_url) for item in json_ld_jobs]

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        key = str(job.get("job_key") or job.get("source_job_id") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(job)
    return deduped


def public_board_link_allowed(portal: str, entry_url: str, candidate_url: str) -> bool:
    parsed = urlparse(candidate_url)
    entry = urlparse(entry_url)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    entry_path = entry.path.rstrip("/")
    if not host or candidate_url == entry_url or path == entry_path:
        return False
    if re.search(r"\.(css|js|png|jpe?g|gif|svg|ico|webp|woff2?|pdf)$", path, re.IGNORECASE):
        return False
    low = candidate_url.lower()
    if any(part in low for part in ("/login", "/sign-in", "/saved", "/pricing", "/post", "/employer", "/blog", "/contact", "oembed")):
        return False
    if portal == "dice":
        return host.endswith("dice.com") and _DICE_LINK_RE.search(path) is not None
    if portal == "remote_co":
        if not host.endswith("remote.co"):
            return False
        if path.startswith("/job/") or path.startswith("/job-details/"):
            return True
        if path.startswith("/remote-jobs/"):
            category_names = {
                "accounting",
                "bookkeeping",
                "customer-service",
                "data-science",
                "design",
                "developer",
                "healthcare",
                "marketing",
                "product-manager",
                "project-manager",
                "qa",
                "sales",
                "writing",
            }
            last = path.rsplit("/", 1)[-1]
            return bool(last and last not in category_names)
        return False
    if portal == "skipthedrive":
        return host.endswith("skipthedrive.com") and path.startswith("/job/")
    if portal == "flexjobs":
        return host.endswith("flexjobs.com") and ("/publicjobs/" in path or _JOB_ID_PATH_RE.search(path) is not None)
    if portal == "jobs24x":
        return host.endswith("jobs24x.com") and path.startswith("/jobs/") and path != "/jobs"
    if portal == "remotefront":
        return host.endswith("remotefront.com") and path.startswith("/jobs/")
    if portal == "justremote":
        return host.endswith("justremote.co") and path.startswith("/remote-jobs/")
    if portal == "generic":
        return bool(
            re.search(r"/(?:job|jobs|career|careers|position|positions|opening|openings|role|roles|vacanc)", path.lower())
        ) and not path.startswith("/job-category/")
    return "job" in path.lower()


def public_board_links_from_render(portal: str, render: BrowserRenderResult) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    entry_url = render.final_url
    for href in _HREF_RE.findall(render.html or ""):
        url = urljoin(entry_url, unescape(unquote(href)))
        if not public_board_link_allowed(portal, entry_url, url):
            continue
        clean_url = url.split("#", 1)[0]
        if clean_url in seen:
            continue
        seen.add(clean_url)
        out.append(clean_url)
    return out


def remote_co_jobs_from_render(source: Dict[str, Any], render: BrowserRenderResult) -> List[Dict[str, Any]]:
    html_text = render.html or ""
    matches = list(
        re.finditer(
            r'<a\s+href="(?P<href>/job-details/[^"]+)"[^>]*id="job-name-(?P<job_id>[^"]+)"[^>]*>(?P<body>.*?)</a>',
            html_text,
            re.IGNORECASE | re.DOTALL,
        )
    )
    out: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        block_end = matches[index + 1].start() if index + 1 < len(matches) else html_text.find("Remote Work Q&A", match.end())
        if block_end < 0:
            block_end = min(len(html_text), match.end() + 4000)
        block = html_text[match.start() : block_end]
        title_parts = [compact_ws(strip_html(part)) for part in re.findall(r"<span[^>]*>(.*?)</span>", match.group("body"), re.IGNORECASE | re.DOTALL)]
        title = title_parts[-1] if title_parts else compact_ws(strip_html(match.group("body")))
        company_match = re.search(
            rf'id="company-name-{re.escape(match.group("job_id"))}"[^>]*>(.*?)</h3>',
            block,
            re.IGNORECASE | re.DOTALL,
        )
        location_match = re.search(r'fa-location-dot"[^>]*title="([^"]+)"', block, re.IGNORECASE)
        location = first_present(
            location_match.group(1) if location_match else "",
            portal_html_field(block, r"<span[^>]*>(Remote[^<]+)</span>"),
        )
        traits = [compact_ws(strip_html(item)) for item in re.findall(r"<li[^>]*>(.*?)</li>", block, re.IGNORECASE | re.DOTALL)]
        job_url = urljoin(render.final_url, match.group("href"))
        job = normalize_base(
            source,
            source_job_id=match.group("job_id"),
            title=title,
            location=location,
            department="Remote.co",
            employment_type=", ".join(trait for trait in traits if trait),
            job_url=job_url,
            apply_url=job_url,
            published_at=None,
            updated_at=None,
            text=combine_text(traits),
            raw={"source": "remote.co list", "url": job_url},
        )
        out.append(set_job_company(job, strip_html(company_match.group(1)) if company_match else ""))
    return out


def skipthedrive_jobs_from_render(source: Dict[str, Any], render: BrowserRenderResult) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for article in re.findall(r"<article\b[^>]*\bjob\b[^>]*>(.*?)</article>", render.html or "", re.IGNORECASE | re.DOTALL):
        link_match = re.search(r'<h2[^>]*>\s*<a\s+href="([^"]+)">(.*?)</a>\s*</h2>', article, re.IGNORECASE | re.DOTALL)
        if not link_match:
            continue
        job_url = urljoin(render.final_url, unescape(link_match.group(1)))
        id_match = re.search(r"/job/[^/]*?-(\d+)/?$", job_url)
        company = portal_html_field(article, r"custom_fields_company_name_display_search_results[^>]*>(.*?)</span>")
        excerpt = portal_html_field(article, r"<span[^>]*class=\"excerpt_part\"[^>]*>(.*?)</span>")
        published_at = portal_html_field(article, r'<time[^>]*datetime="([^"]+)"')
        job = normalize_base(
            source,
            source_job_id=id_match.group(1) if id_match else job_url,
            title=strip_html(link_match.group(2)),
            location="Remote",
            department="SkipTheDrive",
            employment_type="",
            job_url=job_url,
            apply_url=job_url,
            published_at=published_at or None,
            updated_at=None,
            text=excerpt,
            raw={"source": "skipthedrive list", "url": job_url},
        )
        out.append(set_job_company(job, company))
    return out


def public_board_jobs_from_render(source: Dict[str, Any], portal: str, render: BrowserRenderResult) -> List[Dict[str, Any]]:
    if portal == "remote_co":
        return remote_co_jobs_from_render(source, render)
    if portal == "skipthedrive":
        return skipthedrive_jobs_from_render(source, render)
    return []


async def fetch_public_board_detail(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
    *,
    portal: str,
    detail_url: str,
) -> Optional[Dict[str, Any]]:
    try:
        html_text = await fetch_text(session, detail_url, retries=1)
        render = BrowserRenderResult(html=html_text, final_url=detail_url, backend="direct")
    except Exception:
        return None
    direct_jobs = jobs_from_browser_render(source, render)
    if direct_jobs:
        return direct_jobs[0]
    return normalize_portal_detail(source, portal, detail_url, render)


async def fetch_public_board_search(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
    *,
    portal: str,
) -> List[Dict[str, Any]]:
    url = portal_entry_url(source)
    render: Optional[BrowserRenderResult] = None
    attempts: List[str] = []
    if source.get("browser_required"):
        raise SourceStatusError(
            "unsupported",
            f"{source.get('company') or portal} requires browser rendering, which is not shipped in the public build.",
        )
    try:
        html_text = await fetch_text(session, url, retries=1)
        render = BrowserRenderResult(html=html_text, final_url=url, backend="direct")
    except Exception as exc:
        attempts.append(f"direct: {type(exc).__name__}: {exc}")
    if render is None:
        detail = " | ".join(attempts)
        raise SourceStatusError(
            "blocked_skipped",
            f"No direct public HTML could be fetched from {url}."
            + (f" Attempts: {detail}" if detail else ""),
        )

    direct_jobs = jobs_from_browser_render(source, render)
    list_jobs = public_board_jobs_from_render(source, portal, render)
    if list_jobs:
        return list_jobs
    links = public_board_links_from_render(portal, render)
    if direct_jobs and not links:
        return direct_jobs

    if not links:
        if direct_jobs:
            return direct_jobs
        detail = " | ".join(attempts)
        raise SourceStatusError(
            "blocked_skipped",
            f"No parseable {portal} job links or public job payloads were found on {url}."
            + (f" Attempts: {detail}" if detail else ""),
        )

    limit = asyncio.Semaphore(5)

    async def fetch_one(detail_url: str) -> Optional[Dict[str, Any]]:
        async with limit:
            return await fetch_public_board_detail(session, source, portal=portal, detail_url=detail_url)

    gathered = await asyncio.gather(*(fetch_one(link) for link in links[:80]), return_exceptions=True)
    jobs: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict) and result.get("title"):
            jobs.append(result)
    if jobs:
        return jobs
    if direct_jobs:
        return direct_jobs
    raise SourceStatusError("blocked_skipped", f"{portal} pages rendered, but no jobs were normalized.")


async def fetch_dice_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="dice")


async def fetch_remote_co_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="remote_co")


async def fetch_justremote_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="justremote")


async def fetch_skipthedrive_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="skipthedrive")


async def fetch_flexjobs_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="flexjobs")


async def fetch_jobs24x_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="jobs24x")


async def fetch_remotefront_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="remotefront")


async def fetch_underdog_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="generic")


async def fetch_generic_public_board_search(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await fetch_public_board_search(session, source, portal="generic")


def source_int_setting(source: Dict[str, Any], env_name: str, default: int, *, floor: int = 1, ceiling: int = 5000) -> int:
    raw = str(os.environ.get(env_name) or source.get("limit") or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(ceiling, max(floor, value))


def url_query_dict(url: str) -> Dict[str, str]:
    parsed = urlparse(url)
    return {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}


def dataframe_records(frame: Any) -> List[Dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        records = frame.to_dict("records")
        return [row for row in records if isinstance(row, dict)]
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, dict)]
    return []


def is_blankish(value: Any) -> bool:
    if value is None:
        return True
    try:
        if value != value:  # NaN without importing pandas/numpy.
            return True
    except Exception:
        pass
    return str(value).strip().lower() in {"", "none", "nan", "nat"}


def clean_optional_text(value: Any) -> str:
    return "" if is_blankish(value) else value_to_text(value)


def iso_from_seconds_or_text(value: Any) -> Optional[str]:
    parsed = iso_from_seconds(value)
    if parsed:
        return parsed
    text = clean_optional_text(value)
    return text or None


def uber_location_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(filter(None, (uber_location_text(item) for item in value)))
    if isinstance(value, dict):
        return ", ".join(
            filter(
                None,
                (
                    clean_optional_text(value.get("city")),
                    clean_optional_text(value.get("region")),
                    clean_optional_text(value.get("countryName") or value.get("country")),
                ),
            )
        )
    return clean_optional_text(value)


async def fetch_uber_careers(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = source_url_required(source, "Uber Careers")
    query = first_present(url_query_dict(url).get("query"), first_search_term(source, "software engineer"))
    limit = source_int_setting(source, "JOBSCRAPER_UBER_LIMIT", 500, ceiling=2000)
    page_size = min(100, max(10, source_int_setting(source, "JOBSCRAPER_UBER_PAGE_SIZE", 50, ceiling=100)))
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    page = 0
    while len(out) < limit:
        payload = await post_json(
            session,
            "https://www.uber.com/api/loadSearchJobsResults?localeCode=en",
            {
                "limit": min(page_size, limit - len(out)),
                "page": page,
                "params": {"query": query},
            },
            headers={
                "Content-Type": "application/json",
                "Referer": url,
                "x-csrf-token": "x",
            },
            retries=2,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        items = data.get("results") if isinstance(data, dict) else []
        jobs = [item for item in items or [] if isinstance(item, dict)]
        if not jobs:
            break
        for item in jobs:
            job_id = first_present(item.get("id"))
            title = first_present(item.get("title"))
            if not job_id or not title or job_id in seen:
                continue
            seen.add(job_id)
            job_url = f"https://www.uber.com/careers/list/{job_id}"
            out.append(
                normalize_base(
                    source,
                    source_job_id=job_id,
                    title=title,
                    location=first_present(uber_location_text(item.get("allLocations")), uber_location_text(item.get("location"))),
                    department=first_present(item.get("department"), item.get("team"), item.get("programAndPlatform")),
                    employment_type=first_present(item.get("timeType"), item.get("type")),
                    job_url=job_url,
                    apply_url=job_url,
                    published_at=first_present(item.get("creationDate")) or None,
                    updated_at=first_present(item.get("updatedDate")) or None,
                    text=combine_text(item.get("description"), item.get("uniqueSkills")),
                    raw=item,
                )
            )
        if len(jobs) < page_size:
            break
        page += 1
    return out


async def fetch_microsoft_careers(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    query_from_url = url_query_dict(str(source.get("url") or ""))
    query = first_present(query_from_url.get("query"), source_search_text(source, ""))
    location = first_present(query_from_url.get("location"), source_location_text(source, ""))
    sort_by = first_present(query_from_url.get("sort_by"), "solr")
    filter_profession = first_present(query_from_url.get("filter_profession"), "")
    limit = source_int_setting(source, "JOBSCRAPER_MICROSOFT_LIMIT", 500, ceiling=3000)
    out: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    start = 0
    while start < limit:
        params = {
            "domain": "microsoft.com",
            "query": query,
            "location": location,
            "start": str(start),
            "sort_by": sort_by,
        }
        if filter_profession:
            params["filter_profession"] = filter_profession
        payload = await fetch_json(
            session,
            "https://apply.careers.microsoft.com/api/pcsx/search",
            params=params,
            retries=2,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        positions = data.get("positions") if isinstance(data, dict) else []
        items = [item for item in positions or [] if isinstance(item, dict)]
        if not items:
            break
        detail_limit = asyncio.Semaphore(8)

        async def fetch_detail(position: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            position_id = first_present(position.get("id"))
            if not position_id or position_id in seen_ids:
                return None
            seen_ids.add(position_id)
            detail_url = f"https://apply.careers.microsoft.com/careers/job/{position_id}"
            async with detail_limit:
                detail_payload = await fetch_json(
                    session,
                    "https://apply.careers.microsoft.com/api/pcsx/position_details",
                    params={"position_id": position_id, "domain": "microsoft.com", "hl": "en"},
                    retries=2,
                )
            detail = detail_payload.get("data") if isinstance(detail_payload, dict) else {}
            if not isinstance(detail, dict):
                detail = position
            return normalize_base(
                source,
                source_job_id=first_present(detail.get("displayJobId"), detail.get("atsJobId"), position_id),
                title=first_present(detail.get("name"), position.get("name")),
                location=first_present(detail.get("standardizedLocations"), detail.get("locations"), position.get("standardizedLocations"), position.get("locations")),
                department=first_present(detail.get("department"), position.get("department")),
                employment_type=first_present(detail.get("workLocationOption"), position.get("workLocationOption")),
                job_url=detail_url,
                apply_url=detail_url,
                published_at=iso_from_seconds(detail.get("postedTs") or position.get("postedTs")),
                updated_at=iso_from_seconds(detail.get("creationTs") or position.get("creationTs")),
                text=combine_text(detail.get("jobDescription"), detail.get("qualifications"), detail.get("responsibilities")),
                raw={"list": position, "detail": detail},
            )

        gathered = await asyncio.gather(*(fetch_detail(item) for item in items), return_exceptions=True)
        for result in gathered:
            if isinstance(result, dict):
                out.append(result)
        if len(items) < 20:
            break
        start += len(items)
    return out


async def fetch_amazon_jobs(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = source_url_required(source, "Amazon Jobs")
    parsed = urlparse(url)
    params = url_query_dict(url)
    params.setdefault("base_query", source_search_text(source))
    params.setdefault("loc_query", "" if source_location_text(source, "").lower() == "remote" else source_location_text(source, ""))
    params.setdefault("sort", "relevant")
    limit = source_int_setting(source, "JOBSCRAPER_AMAZON_LIMIT", 1000, ceiling=5000)
    page_size = min(100, max(10, source_int_setting(source, "JOBSCRAPER_AMAZON_PAGE_SIZE", 100, ceiling=100)))
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    while offset < limit:
        params["offset"] = str(offset)
        params["result_limit"] = str(min(page_size, limit - offset))
        api_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/en/search.json'}"
        payload = await fetch_json(session, api_url, params=params, retries=2)
        items = payload.get("jobs") if isinstance(payload, dict) else []
        jobs = [item for item in items or [] if isinstance(item, dict)]
        if not jobs:
            break
        for item in jobs:
            job_id = first_present(item.get("id_icims"), item.get("id"), item.get("job_path"), item.get("title"))
            if not job_id or job_id in seen:
                continue
            seen.add(job_id)
            job_url = urljoin("https://www.amazon.jobs", first_present(item.get("job_path"), ""))
            job = normalize_base(
                source,
                source_job_id=job_id,
                title=first_present(item.get("title")),
                location=first_present(item.get("normalized_location"), item.get("location"), item.get("city"), item.get("country_code")),
                department=first_present(item.get("job_category"), item.get("job_family"), item.get("business_category")),
                employment_type=first_present(item.get("job_schedule_type"), item.get("job_type")),
                job_url=job_url,
                apply_url=first_present(item.get("url_next_step"), job_url),
                published_at=first_present(item.get("posted_date")) or None,
                updated_at=first_present(item.get("updated_time")) or None,
                text=combine_text(item.get("description"), item.get("basic_qualifications"), item.get("preferred_qualifications")),
                raw=item,
            )
            out.append(set_job_company(job, first_present(item.get("company_name"), "Amazon")))
        if len(jobs) < page_size:
            break
        offset += len(jobs)
    return out


_APPLE_HYDRATION_RE = re.compile(r"window\.__staticRouterHydrationData\s*=\s*JSON\.parse\(\"(.*?)\"\);", re.DOTALL)


def apple_hydration_data(html_text: str) -> Dict[str, Any]:
    match = _APPLE_HYDRATION_RE.search(html_text or "")
    if not match:
        return {}
    try:
        decoded = json.loads(f'"{match.group(1)}"')
        data = json.loads(decoded)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def find_nested_dict_with_key(value: Any, key: str) -> Dict[str, Any]:
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if key in current and isinstance(current.get(key), dict):
                return current[key]
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return {}


def apple_search_page_url(source: Dict[str, Any], page: int) -> str:
    url = source_url_required(source, "Apple Jobs")
    parsed = urlparse(url)
    params = url_query_dict(url)
    if source_search_text(source, "") and "search" not in params:
        params["search"] = source_search_text(source)
    params.setdefault("location", "united-states-USA")
    if page > 1:
        params["page"] = str(page)
    elif "page" in params:
        params.pop("page", None)
    return parsed._replace(query=urlencode(params)).geturl()


async def fetch_apple_jobs(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    max_pages = source_int_setting(source, "JOBSCRAPER_APPLE_PAGES", 12, ceiling=60)
    detail_urls: List[str] = []
    seen_urls: set[str] = set()
    for page in range(1, max_pages + 1):
        html_text = await fetch_text(session, apple_search_page_url(source, page), retries=2)
        links = [
            urljoin("https://jobs.apple.com", unescape(href))
            for href in re.findall(r'href=["\']([^"\']*/details/[^"\']+)["\']', html_text, re.IGNORECASE)
        ]
        added = 0
        for link in links:
            normalized_link = link.split("#", 1)[0]
            if normalized_link in seen_urls:
                continue
            seen_urls.add(normalized_link)
            detail_urls.append(normalized_link)
            added += 1
        if not added:
            break
    detail_limit = asyncio.Semaphore(8)

    async def fetch_detail(detail_url: str) -> Optional[Dict[str, Any]]:
        async with detail_limit:
            detail_html = await fetch_text(session, detail_url, retries=2)
        job_details = find_nested_dict_with_key(apple_hydration_data(detail_html), "jobDetails")
        jobs_data = job_details.get("jobsData") if isinstance(job_details.get("jobsData"), dict) else {}
        if not jobs_data:
            title = clean_title_suffix(extract_title_tag(detail_html), " - Jobs - Careers at Apple")
            return normalize_base(
                source,
                source_job_id=portal_source_job_id("apple", detail_url),
                title=title,
                location="",
                department="Apple",
                employment_type="",
                job_url=detail_url,
                apply_url=detail_url,
                published_at=None,
                updated_at=None,
                text=combine_text(extract_meta_description(detail_html), strip_html(detail_html)[:8000]),
                raw={"url": detail_url},
            )
        return normalize_base(
            source,
            source_job_id=first_present(jobs_data.get("jobNumber"), jobs_data.get("reqId"), detail_url),
            title=first_present(jobs_data.get("postingTitle")),
            location=first_present(jobs_data.get("locations"), jobs_data.get("localeLocation"), jobs_data.get("selectedLocation")),
            department=first_present(jobs_data.get("teamNames"), jobs_data.get("jobType")),
            employment_type=first_present(jobs_data.get("employmentType"), jobs_data.get("standardWeeklyHours")),
            job_url=detail_url,
            apply_url=detail_url,
            published_at=first_present(jobs_data.get("postDateInGMT"), jobs_data.get("postingDate")) or None,
            updated_at=None,
            text=combine_text(
                jobs_data.get("jobSummary"),
                jobs_data.get("description"),
                jobs_data.get("responsibilities"),
                jobs_data.get("minimumQualifications"),
                jobs_data.get("preferredQualifications"),
            ),
            raw=jobs_data,
        )

    gathered = await asyncio.gather(*(fetch_detail(url) for url in detail_urls), return_exceptions=True)
    return [result for result in gathered if isinstance(result, dict) and result.get("title")]


def iter_deep_json_nodes(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_deep_json_nodes(child)
    elif isinstance(value, list):
        for item in value:
            yield from iter_deep_json_nodes(item)


async def fetch_oracle_careers(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    search_text = first_search_term(source, "software engineer")
    limit = source_int_setting(source, "JOBSCRAPER_ORACLE_LIMIT", 300, ceiling=2000)
    page_size = min(100, max(10, source_int_setting(source, "JOBSCRAPER_ORACLE_PAGE_SIZE", 50, ceiling=100)))
    out: List[Dict[str, Any]] = []
    offset = 0
    while offset < limit:
        finder = (
            "findReqs;"
            "siteNumber=CX_45001,"
            "facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,"
            f"limit={min(page_size, limit - offset)},"
            f"offset={offset},"
            f'keyword="{search_text}",'
            "sortBy=RELEVANCY"
        )
        payload = await fetch_json(
            session,
            "https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
            params={
                "onlyData": "true",
                "expand": "requisitionList.workLocation,requisitionList.otherWorkLocations,requisitionList.secondaryLocations,flexFieldsFacet.values,requisitionList.requisitionFlexFields",
                "finder": finder,
            },
            retries=2,
        )
        items = payload.get("items") if isinstance(payload, dict) else []
        item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        requisitions = item.get("requisitionList") if isinstance(item, dict) else []
        jobs = [job for job in requisitions or [] if isinstance(job, dict)]
        if not jobs:
            break
        for job in jobs:
            job_id = first_present(job.get("Id"))
            job_url = f"https://careers.oracle.com/en/sites/jobsearch/job/{job_id}" if job_id else source_url_required(source, "Oracle Careers")
            out.append(
                normalize_base(
                    source,
                    source_job_id=first_present(job_id, job.get("Title")),
                    title=first_present(job.get("Title")),
                    location=first_present(job.get("PrimaryLocation"), job.get("PrimaryLocationCountry"), job.get("secondaryLocations")),
                    department=first_present(job.get("JobFunction"), job.get("JobFamily"), job.get("Organization"), job.get("BusinessUnit")),
                    employment_type=first_present(job.get("WorkerType"), job.get("ContractType"), job.get("JobSchedule"), job.get("WorkplaceType")),
                    job_url=job_url,
                    apply_url=job_url,
                    published_at=first_present(job.get("PostedDate")) or None,
                    updated_at=first_present(job.get("PostingEndDate")) or None,
                    text=combine_text(job.get("ShortDescriptionStr"), job.get("ExternalResponsibilitiesStr"), job.get("ExternalQualificationsStr")),
                    raw=job,
                )
            )
        total = int(item.get("TotalJobsCount") or 0) if isinstance(item, dict) else 0
        offset += len(jobs)
        if len(jobs) < page_size or (total and offset >= total):
            break
    return out


async def fetch_ibm_careers(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    raise SourceStatusError(
        "manual_review",
        "IBM Careers is attached but disabled because the current public page emits noisy non-job payloads without a stable job-only endpoint.",
    )


async def fetch_arbeitnow_api(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = await fetch_json(session, source_url_required(source, "Arbeitnow"), params={"page": "1"})
    items = payload.get("data") if isinstance(payload, dict) else payload
    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        slug = first_present(item.get("slug"))
        job_url = first_present(item.get("url"), urljoin("https://www.arbeitnow.com/jobs/", slug) if slug else "")
        tags = item.get("tags") if isinstance(item.get("tags"), list) else []
        job_types = item.get("job_types") if isinstance(item.get("job_types"), list) else []
        job = normalize_base(
            source,
            source_job_id=first_present(slug, item.get("id"), job_url, item.get("title")),
            title=first_present(item.get("title")),
            location=first_present(item.get("location"), "Remote" if item.get("remote") else ""),
            department=first_present(tags),
            employment_type=first_present(job_types),
            job_url=job_url or source_url_required(source, "Arbeitnow"),
            apply_url=job_url or source_url_required(source, "Arbeitnow"),
            published_at=iso_from_seconds(item.get("created_at")),
            updated_at=None,
            text=combine_text(item.get("description"), tags, job_types),
            raw=item,
        )
        out.append(set_job_company(job, first_present(item.get("company_name"), item.get("company"))))
    return out


async def fetch_smartrecruiters(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
) -> List[Dict[str, Any]]:
    limit = 100
    offset = 0
    listings: List[Dict[str, Any]] = []
    while True:
        payload = await fetch_json(
            session,
            smartrecruiters_list_url(source, limit=limit, offset=offset),
        )
        content = payload.get("content") if isinstance(payload, dict) else []
        items = content if isinstance(content, list) else []
        if not items:
            break
        listings.extend(item for item in items if isinstance(item, dict))
        total = int(payload.get("totalFound") or 0) if isinstance(payload, dict) else 0
        offset += len(items)
        if total and offset >= total:
            break

    detail_limit = asyncio.Semaphore(8)

    async def fetch_one(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        posting_id = str(item.get("id") or "").strip()
        if not posting_id:
            return None
        async with detail_limit:
            detail = await fetch_json(session, smartrecruiters_detail_url(source, posting_id))
        location_data = detail.get("location") if isinstance(detail, dict) else {}
        location = first_present(
            location_data.get("fullLocation") if isinstance(location_data, dict) else "",
            combine_text(
                location_data.get("city") if isinstance(location_data, dict) else "",
                location_data.get("region") if isinstance(location_data, dict) else "",
                location_data.get("country") if isinstance(location_data, dict) else "",
            ),
            (item.get("location") or {}).get("fullLocation") if isinstance(item.get("location"), dict) else "",
        )
        department = first_present(
            detail.get("department"),
            smartrecruiters_custom_field(detail, "Department", "Team"),
        )
        text = combine_text(
            smartrecruiters_sections_text(detail),
            smartrecruiters_custom_field(detail, "Job Description", "Qualifications"),
        )
        return normalize_base(
            source,
            source_job_id=detail.get("id") or detail.get("jobId") or posting_id,
            title=first_present(detail.get("name"), item.get("name")),
            location=location,
            department=department,
            employment_type=first_present(detail.get("typeOfEmployment")),
            job_url=first_present(detail.get("postingUrl"), detail.get("applyUrl")),
            apply_url=first_present(detail.get("applyUrl"), detail.get("postingUrl")),
            published_at=first_present(detail.get("releasedDate"), item.get("releasedDate")) or None,
            updated_at=None,
            text=text,
            raw=detail,
        )

    gathered = await asyncio.gather(*(fetch_one(item) for item in listings), return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


async def fetch_workday_detail_batch(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
    items: List[Dict[str, Any]],
) -> JobBatch:
    detail_limit = asyncio.Semaphore(8)

    async def fetch_one(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        external_path = str(item.get("externalPath") or "").strip()
        if not external_path:
            return None
        async with detail_limit:
            detail = await fetch_json(session, workday_detail_url(source, external_path))
        info = detail.get("jobPostingInfo") if isinstance(detail, dict) else {}
        info = info if isinstance(info, dict) else {}
        description = combine_text(
            info.get("jobDescription"),
            info.get("additionalJobDescription"),
        )
        location = first_present(
            info.get("location"),
            info.get("locationsText"),
            info.get("jobRequisitionLocation"),
            item.get("locationsText"),
        )
        department = first_present(
            info.get("jobFamilyGroup"),
            info.get("jobFamily"),
            info.get("jobProfile"),
        )
        public_job_url = workday_public_job_url(source, external_path)
        return normalize_base(
            source,
            source_job_id=first_present(
                info.get("jobReqId"),
                info.get("id"),
                value_to_text(item.get("bulletFields")),
                external_path,
            ),
            title=first_present(info.get("title"), item.get("title")),
            location=location,
            department=department,
            employment_type=first_present(info.get("timeType"), item.get("timeType")),
            job_url=public_job_url,
            apply_url=first_present(info.get("externalUrl"), public_job_url),
            published_at=first_present(info.get("posted"), info.get("postedOn"), item.get("postedOn")) or None,
            updated_at=None,
            text=description,
            raw=detail,
        )

    gathered = await asyncio.gather(*(fetch_one(item) for item in items), return_exceptions=True)
    out: JobBatch = []
    for result in gathered:
        if isinstance(result, dict):
            out.append(result)
    return out


async def stream_workday(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
    progress: Optional[ProgressCallback] = None,
) -> AsyncIterator[JobBatch]:
    url = workday_jobs_url(source)
    offset = 0
    limit = 20
    total_seen = 0
    page_num = 0
    while True:
        payload = await post_json(
            session,
            url,
            {
                "appliedFacets": {},
                "limit": limit,
                "offset": offset,
                "searchText": "",
            },
        )
        jobs = payload.get("jobPostings") if isinstance(payload, dict) else []
        items = jobs if isinstance(jobs, list) else []
        if not items:
            break
        page_num += 1
        valid_items = [item for item in items if isinstance(item, dict)]
        total = int(payload.get("total") or 0) if isinstance(payload, dict) else 0
        total_seen += len(valid_items)
        emit(
            progress,
            (
                f"Workday {source['company']}: page={page_num}, offset={offset}, "
                f"listed={len(valid_items)}, total_seen={total_seen}"
                + (f"/{total}" if total else "")
            ),
        )
        batch = await fetch_workday_detail_batch(session, source, valid_items)
        emit(
            progress,
            (
                f"Workday {source['company']}: page={page_num} details processed, "
                f"normalized={len(batch)}, total_seen={total_seen}"
                + (f"/{total}" if total else "")
            ),
        )
        if batch:
            yield batch
        offset += len(items)
        if total and offset >= total:
            break

async def fetch_workday(session: aiohttp.ClientSession, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: JobBatch = []
    async for batch in stream_workday(session, source, None):
        out.extend(batch)
    return out


ADAPTERS: Dict[str, JobAdapter] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "recruitee": fetch_recruitee,
    "personio": fetch_personio,
    "google": fetch_google,
    "microsoft_careers": fetch_microsoft_careers,
    "amazon_jobs": fetch_amazon_jobs,
    "apple_jobs": fetch_apple_jobs,
    "oracle_careers": fetch_oracle_careers,
    "ibm_careers": fetch_ibm_careers,
    "uber_careers": fetch_uber_careers,
    "eightfold": fetch_eightfold,
    "optiver": fetch_optiver,
    "drw": fetch_drw,
    "gresearch": fetch_gresearch,
    "workday": fetch_workday,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "hackernews_hiring": fetch_hackernews_hiring,
    "remoteok_api": fetch_remoteok_api,
    "remotive_api": fetch_remotive_api,
    "weworkremotely_rss": fetch_weworkremotely_rss,
    "powertofly_search": fetch_powertofly_search,
    "authenticjobs_wp": fetch_authenticjobs_wp,
    "dice_search": fetch_dice_search,
    "remote_co_search": fetch_remote_co_search,
    "justremote_search": fetch_justremote_search,
    "skipthedrive_search": fetch_skipthedrive_search,
    "flexjobs_search": fetch_flexjobs_search,
    "jobs24x_search": fetch_jobs24x_search,
    "remotefront_search": fetch_remotefront_search,
    "underdog_search": fetch_underdog_search,
    "devsnap_search": fetch_generic_public_board_search,
    "workable": fetch_generic_public_board_search,
    "teamtailor": fetch_generic_public_board_search,
    "bamboohr": fetch_generic_public_board_search,
    "breezy_hr": fetch_generic_public_board_search,
    "jazzhr": fetch_generic_public_board_search,
    "icims": fetch_generic_public_board_search,
    "jobvite": fetch_generic_public_board_search,
    "oracle_taleo": fetch_generic_public_board_search,
    "sap_successfactors": fetch_generic_public_board_search,
    "ukg": fetch_generic_public_board_search,
    "ultipro": fetch_generic_public_board_search,
    "adp": fetch_generic_public_board_search,
    "paylocity": fetch_generic_public_board_search,
    "pinpoint": fetch_generic_public_board_search,
    "comeet": fetch_generic_public_board_search,
    "rippling": fetch_generic_public_board_search,
    "yc_work_at_startup": fetch_generic_public_board_search,
    "himalayas_search": fetch_generic_public_board_search,
    "workingnomads_search": fetch_generic_public_board_search,
    "nodesk_search": fetch_generic_public_board_search,
    "jobspresso_search": fetch_generic_public_board_search,
    "remote_rocketship_search": fetch_generic_public_board_search,
    "arc_dev_search": fetch_generic_public_board_search,
    "levels_fyi_jobs": fetch_generic_public_board_search,
    "builtin_jobs": fetch_generic_public_board_search,
    "climatebase_jobs": fetch_generic_public_board_search,
    "arbeitnow_api": fetch_arbeitnow_api,
    "naukri_search": fetch_generic_public_board_search,
    "instahyre_search": fetch_generic_public_board_search,
    "cutshort_search": fetch_generic_public_board_search,
    "hirist_search": fetch_generic_public_board_search,
    "foundit_search": fetch_generic_public_board_search,
    "timesjobs_search": fetch_generic_public_board_search,
    "ai_jobs_search": fetch_generic_public_board_search,
    "ml_jobs_search": fetch_generic_public_board_search,
    "data_jobs_search": fetch_generic_public_board_search,
    "rust_jobs_search": fetch_generic_public_board_search,
    "golangprojects_search": fetch_generic_public_board_search,
    "python_jobs_search": fetch_generic_public_board_search,
    "cybersecjobs_search": fetch_generic_public_board_search,
    "otta_search": fetch_generic_public_board_search,
    "welcome_to_the_jungle_search": fetch_generic_public_board_search,
}

STREAMING_ADAPTERS: Dict[str, StreamingJobAdapter] = {
    "workday": stream_workday,
}


def emit(progress: Optional[ProgressCallback], message: str) -> None:
    if progress:
        progress(message)
    else:
        logging.info(message)


def persist_jobs_batch(
    conn: Any,
    source: Dict[str, Any],
    jobs: JobBatch,
    opts: ScrapeOptions,
    result: ScrapeResult,
    seen_keys: List[str],
    *,
    seen_at: int,
) -> None:
    for job in jobs:
        if not job.get("source_job_id") or not job.get("title"):
            continue
        full_text = str(job.get("text") or "")
        match = filter_and_match(
            full_text,
            opts,
            location=str(job.get("location") or ""),
        )
        stack = detect_stack(full_text)
        db.upsert_job(conn, source, job, match, stack, seen_at=seen_at)
        seen_keys.append(str(job["job_key"]))
        result.saved += 1
        if match.get("passes_filter"):
            result.matching += 1


async def scrape_all_async(
    *,
    db_path: Path | str = db.DEFAULT_DB_PATH,
    sources_path: Optional[Path | str] = paths.default_sources_path(),
    options: Optional[ScrapeOptions] = None,
    progress: Optional[ProgressCallback] = None,
    should_stop: Optional[ShouldStop] = None,
) -> List[ScrapeResult]:
    opts = options or ScrapeOptions()
    db.init_db(db_path)
    scrape_started_at = db.now_ts()
    jobs_before_scrape = db.job_count(db_path)
    if sources_path and Path(sources_path).exists() and not db.has_sources(db_path):
        count = db.import_sources(db_path, sources_path)
        emit(progress, f"Imported {count} company sources into SQLite.")

    with db.connect(db_path) as conn:
        db.migrate_connection(conn)
        sources = db.list_sources_conn(conn, enabled_only=True)
    only_ids = {int(source_id) for source_id in opts.only_source_ids if int(source_id)}
    only_companies = {str(company).strip().casefold() for company in opts.only_companies if str(company).strip()}
    if only_ids:
        sources = [source for source in sources if int(source.get("id") or 0) in only_ids]
    if only_companies:
        sources = [source for source in sources if str(source.get("company") or "").strip().casefold() in only_companies]

    if not sources:
        emit(progress, "No enabled sources found.")
        report = db.new_job_report_since(db_path, scrape_started_at, before_count=jobs_before_scrape)
        emit(
            progress,
            (
                "SCRAPE_SUMMARY "
                f"jobs_before={report['jobs_before']} jobs_after={report['jobs_after']} "
                f"new_since_last_scrape={report['new_since_started']} matching_new={report['matching_new']} "
                "sources=0"
            ),
        )
        return []

    headers = {"User-Agent": USER_AGENT}
    semaphore = asyncio.Semaphore(max(1, opts.concurrency))
    db_lock = asyncio.Lock()
    results: List[ScrapeResult] = []

    async with aiohttp.ClientSession(headers=headers) as session:
        with db.connect(db_path) as conn:
            db.migrate_connection(conn)

            async def process_source(source: Dict[str, Any]) -> ScrapeResult:
                source = dict(source)
                result = ScrapeResult(
                    source_id=int(source["id"]),
                    company=str(source["company"]),
                    ats=str(source["ats"]),
                )
                if result.ats == "hackernews_hiring":
                    source["_hn_parser_engine"] = str(opts.hackernews_parser_engine or "auto")
                if should_stop and should_stop():
                    result.skipped = True
                    emit(progress, f"Skipped {result.company}: stop requested.")
                    return result

                streaming_adapter = STREAMING_ADAPTERS.get(result.ats)
                adapter = ADAPTERS.get(result.ats)
                if adapter is None and streaming_adapter is None:
                    result.error = f"unsupported ATS: {result.ats}"
                    async with db_lock:
                        db.update_source_status(
                            conn,
                            result.source_id,
                            status="unsupported",
                            error=result.error[:2000],
                            duration_ms=0,
                        )
                        conn.commit()
                    return result

                async with semaphore:
                    source_started = perf_counter()

                    def elapsed_ms() -> int:
                        return max(0, int((perf_counter() - source_started) * 1000.0))

                    emit(progress, f"Scraping {result.company} ({result.ats})...")
                    async with db_lock:
                        db.update_source_status(conn, result.source_id, status="running", error="")
                        if source_portal_name(source):
                            db.update_source_session(
                                conn,
                                result.source_id,
                                session_status="public_ok",
                                session_detail=str(source.get("session_detail") or ""),
                            )
                        conn.commit()

                    seen_at = db.now_ts()
                    seen_keys: List[str] = []
                    if streaming_adapter is not None:
                        try:
                            async for batch in streaming_adapter(session, source, progress):
                                result.fetched += len(batch)
                                async with db_lock:
                                    persist_jobs_batch(
                                        conn,
                                        source,
                                        batch,
                                        opts,
                                    result,
                                    seen_keys,
                                    seen_at=seen_at,
                                    )
                                    conn.commit()
                        except SourceStatusError as exc:
                            result.error = str(exc)
                            result.skipped = True
                            async with db_lock:
                                db.update_source_status(
                                    conn,
                                    result.source_id,
                                    status=exc.status,
                                    error=result.error[:2000],
                                    duration_ms=elapsed_ms(),
                                )
                                if source_portal_name(source):
                                    db.update_source_session(
                                        conn,
                                        result.source_id,
                                        session_status=exc.status,
                                        session_detail=result.error[:500],
                                    )
                                conn.commit()
                            emit(progress, f"{exc.status.upper()} {result.company}: {result.error}")
                            return result
                        except Exception as exc:
                            result.error = str(exc)
                            async with db_lock:
                                db.update_source_status(
                                    conn,
                                    result.source_id,
                                    status="error",
                                    error=result.error[:2000],
                                    duration_ms=elapsed_ms(),
                                )
                                if source_portal_name(source):
                                    db.update_source_session(
                                        conn,
                                        result.source_id,
                                        session_status="manual_review",
                                        session_detail=result.error[:500],
                                    )
                                conn.commit()
                            emit(progress, f"ERROR {result.company}: {result.error}")
                            return result
                    else:
                        try:
                            jobs = await adapter(session, source)
                        except SourceStatusError as exc:
                            result.error = str(exc)
                            result.skipped = True
                            async with db_lock:
                                db.update_source_status(
                                    conn,
                                    result.source_id,
                                    status=exc.status,
                                    error=result.error[:2000],
                                    duration_ms=elapsed_ms(),
                                )
                                if source_portal_name(source):
                                    db.update_source_session(
                                        conn,
                                        result.source_id,
                                        session_status=exc.status,
                                        session_detail=result.error[:500],
                                    )
                                conn.commit()
                            emit(progress, f"{exc.status.upper()} {result.company}: {result.error}")
                            return result
                        except Exception as exc:
                            result.error = str(exc)
                            async with db_lock:
                                db.update_source_status(
                                    conn,
                                    result.source_id,
                                    status="error",
                                    error=result.error[:2000],
                                    duration_ms=elapsed_ms(),
                                )
                                if source_portal_name(source):
                                    db.update_source_session(
                                        conn,
                                        result.source_id,
                                        session_status="manual_review",
                                        session_detail=result.error[:500],
                                    )
                                conn.commit()
                            emit(progress, f"ERROR {result.company}: {result.error}")
                            return result
                        result.fetched = len(jobs)
                        async with db_lock:
                            persist_jobs_batch(
                                conn,
                                source,
                                jobs,
                                opts,
                                result,
                                seen_keys,
                                seen_at=seen_at,
                            )
                            conn.commit()

                    async with db_lock:
                        result.closed = db.mark_missing_jobs_closed(
                            conn,
                            result.source_id,
                            seen_keys,
                            closed_at=seen_at,
                        )
                        success_status = "direct_api"
                        db.update_source_status(
                            conn,
                            result.source_id,
                            status=success_status,
                            error="",
                            scraped_at=seen_at,
                            duration_ms=elapsed_ms(),
                        )
                        if source_portal_name(source):
                            db.update_source_session(
                                conn,
                                result.source_id,
                                session_status="public_ok",
                                session_detail="Public source validated during scrape.",
                                checked_at=seen_at,
                            )
                        conn.commit()
                    emit(
                        progress,
                        (
                            f"Done {result.company}: fetched={result.fetched}, "
                            f"saved={result.saved}, matching={result.matching}, closed={result.closed}"
                        ),
                    )
                    return result

            tasks = [asyncio.create_task(process_source(source)) for source in sources]
            for task in asyncio.as_completed(tasks):
                result = await task
                results.append(result)
                if should_stop and should_stop():
                    emit(progress, "Stop requested; waiting for in-flight source fetches to finish.")

    report = db.new_job_report_since(db_path, scrape_started_at, before_count=jobs_before_scrape)
    emit(
        progress,
        (
            "SCRAPE_SUMMARY "
            f"jobs_before={report['jobs_before']} jobs_after={report['jobs_after']} "
            f"new_since_last_scrape={report['new_since_started']} matching_new={report['matching_new']} "
            f"net_new={report['net_new']} sources={len(results)} "
            f"fetched={sum(result.fetched for result in results)} "
            f"saved_seen={sum(result.saved for result in results)} "
            f"closed={sum(result.closed for result in results)}"
        ),
    )
    return results


def scrape_all(
    *,
    db_path: Path | str = db.DEFAULT_DB_PATH,
    sources_path: Optional[Path | str] = paths.default_sources_path(),
    options: Optional[ScrapeOptions] = None,
    progress: Optional[ProgressCallback] = None,
    should_stop: Optional[ShouldStop] = None,
) -> List[ScrapeResult]:
    return asyncio.run(
        scrape_all_async(
            db_path=db_path,
            sources_path=sources_path,
            options=options,
            progress=progress,
            should_stop=should_stop,
        )
    )


async def write_source_discovery_report_async(
    *,
    sources_path: Path | str,
    out_path: Path | str,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    payload = json.loads(Path(sources_path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"{sources_path} must contain a JSON list")
    sources = [item for item in payload if isinstance(item, dict)]
    if limit > 0:
        sources = sources[:limit]
    report: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        for raw_source in sources:
            try:
                source = db._clean_source(raw_source)
            except Exception as exc:
                report.append(
                    {
                        "company": raw_source.get("company", ""),
                        "ats": raw_source.get("ats", ""),
                        "status": "invalid_source",
                        "error": str(exc),
                    }
                )
                continue
            source["id"] = 0
            source["tags"] = raw_source.get("tags") or []
            source["browser_required"] = bool(raw_source.get("browser_required"))
            source["wait_selector"] = raw_source.get("wait_selector") or ""
            if source["browser_required"]:
                report.append(
                    {
                        "company": source["company"],
                        "ats": source["ats"],
                        "status": "unsupported",
                        "error": "Browser-rendered source discovery is not shipped in the public build.",
                    }
                )
                continue
            adapter = ADAPTERS.get(str(source["ats"]))
            if not adapter:
                report.append(
                    {
                        "company": source["company"],
                        "ats": source["ats"],
                        "status": "unsupported",
                        "error": "No adapter registered",
                    }
                )
                continue
            try:
                jobs = await adapter(session, source)
                report.append(
                    {
                        "company": source["company"],
                        "ats": source["ats"],
                        "status": "direct_api",
                        "job_count": len(jobs),
                        "confidence": "high" if jobs else "empty",
                        "sample_titles": [job.get("title", "") for job in jobs[:5]],
                    }
                )
            except SourceStatusError as exc:
                report.append(
                    {
                        "company": source["company"],
                        "ats": source["ats"],
                        "status": exc.status,
                        "job_count": 0,
                        "confidence": "blocked",
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                report.append(
                    {
                        "company": source["company"],
                        "ats": source["ats"],
                        "status": "error",
                        "job_count": 0,
                        "confidence": "failed",
                        "error": str(exc),
                    }
                )
    fs.atomic_write_json(out_path, report)
    return report


def write_source_discovery_report(
    *,
    sources_path: Path | str,
    out_path: Path | str,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    return asyncio.run(
        write_source_discovery_report_async(
            sources_path=sources_path,
            out_path=out_path,
            limit=limit,
        )
    )


DISCOVERY_PROBE_SURFACES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "icims",
    "jobvite",
    "workable",
    "teamtailor",
    "bamboohr",
)


def _candidate_slug_map(candidate: Dict[str, Any]) -> Dict[str, List[Dict[str, str]]]:
    """Normalize candidate watchlist shapes into surface/token/url probes."""
    out: Dict[str, List[Dict[str, str]]] = {surface: [] for surface in DISCOVERY_PROBE_SURFACES}
    surfaces = candidate.get("surfaces") if isinstance(candidate.get("surfaces"), dict) else {}
    slugs = candidate.get("slugs") if isinstance(candidate.get("slugs"), dict) else {}
    for surface in DISCOVERY_PROBE_SURFACES:
        raw_items: List[Any] = []
        if isinstance(surfaces, dict) and surface in surfaces:
            raw = surfaces.get(surface)
            raw_items.extend(raw if isinstance(raw, list) else [raw])
        if isinstance(slugs, dict) and surface in slugs:
            raw = slugs.get(surface)
            raw_items.extend(raw if isinstance(raw, list) else [raw])
        for item in raw_items:
            if isinstance(item, dict):
                token = str(item.get("token") or item.get("slug") or "").strip()
                url = str(item.get("url") or "").strip()
            else:
                token = str(item or "").strip()
                url = ""
            if token or url:
                out[surface].append({"token": token, "url": url})
    generic_slugs = candidate.get("slugs")
    if isinstance(generic_slugs, list):
        for slug in generic_slugs:
            token = str(slug or "").strip()
            if token:
                for surface in ("greenhouse", "lever", "ashby", "smartrecruiters"):
                    out[surface].append({"token": token, "url": ""})
    return out


def _candidate_source(company: str, surface: str, probe: Dict[str, str], tags: Sequence[str]) -> Dict[str, Any]:
    source = {
        "company": company,
        "ats": surface,
        "token": str(probe.get("token") or "").strip(),
        "url": str(probe.get("url") or "").strip(),
        "tags": list(tags),
        "enabled": False,
    }
    return source


def _jobs_are_importable(jobs: Sequence[Dict[str, Any]]) -> bool:
    for job in jobs:
        if (
            str(job.get("source_job_id") or "").strip()
            and str(job.get("title") or "").strip()
            and str(job.get("job_url") or job.get("apply_url") or "").strip()
            and str(job.get("location") or "").strip()
        ):
            return True
    return False


async def _probe_candidate_direct(
    session: aiohttp.ClientSession,
    source: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Fast candidate probe for direct public ATS list APIs."""
    ats = str(source.get("ats") or "").strip().lower()
    if ats == "greenhouse":
        payload = await fetch_json(session, greenhouse_url(source), timeout_s=10, retries=1)
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        out: List[Dict[str, Any]] = []
        for item in jobs if isinstance(jobs, list) else []:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "source_job_id": str(item.get("id") or item.get("internal_job_id") or ""),
                    "title": first_present(item.get("title")),
                    "location": first_present(item.get("location")),
                    "job_url": first_present(item.get("absolute_url")),
                }
            )
        return out
    if ats == "lever":
        payload = await fetch_json(session, lever_url(source), timeout_s=10, retries=1)
        jobs = payload if isinstance(payload, list) else []
        out = []
        for item in jobs:
            if not isinstance(item, dict):
                continue
            categories = item.get("categories") if isinstance(item.get("categories"), dict) else {}
            out.append(
                {
                    "source_job_id": str(item.get("id") or ""),
                    "title": first_present(item.get("text")),
                    "location": first_present(categories.get("location")),
                    "job_url": first_present(item.get("hostedUrl"), item.get("applyUrl")),
                }
            )
        return out
    if ats == "ashby":
        payload = await fetch_json(session, ashby_url(source), timeout_s=10, retries=1)
        jobs = (payload.get("jobs") or payload.get("data") or []) if isinstance(payload, dict) else []
        out = []
        token = str(source.get("token") or "").strip()
        for item in jobs if isinstance(jobs, list) else []:
            if not isinstance(item, dict):
                continue
            job_id = str(item.get("id") or item.get("jobId") or "")
            out.append(
                {
                    "source_job_id": job_id,
                    "title": first_present(item.get("title")),
                    "location": first_present(item.get("location"), item.get("locationName")),
                    "job_url": first_present(item.get("jobUrl"), item.get("applyUrl"), f"https://jobs.ashbyhq.com/{token}/{job_id}" if token and job_id else ""),
                }
            )
        return out
    if ats == "smartrecruiters":
        payload = await fetch_json(session, smartrecruiters_list_url(source, limit=25, offset=0), timeout_s=10, retries=1)
        jobs = payload.get("content") if isinstance(payload, dict) else []
        out = []
        for item in jobs if isinstance(jobs, list) else []:
            if not isinstance(item, dict):
                continue
            location = item.get("location") if isinstance(item.get("location"), dict) else {}
            out.append(
                {
                    "source_job_id": str(item.get("id") or ""),
                    "title": first_present(item.get("name")),
                    "location": first_present(location.get("fullLocation"), location.get("city")),
                    "job_url": first_present(item.get("postingUrl"), item.get("ref"), item.get("applyUrl")),
                }
            )
        return out
    if ats == "workday":
        payload = await post_json(
            session,
            workday_jobs_url(source),
            {"appliedFacets": {}, "limit": 25, "offset": 0, "searchText": ""},
            timeout_s=10,
            retries=1,
        )
        jobs = payload.get("jobPostings") if isinstance(payload, dict) else []
        out = []
        for item in jobs if isinstance(jobs, list) else []:
            if not isinstance(item, dict):
                continue
            external_path = str(item.get("externalPath") or "").strip()
            out.append(
                {
                    "source_job_id": first_present(item.get("id"), item.get("jobReqId"), external_path),
                    "title": first_present(item.get("title")),
                    "location": first_present(item.get("locationsText"), item.get("location")),
                    "job_url": workday_public_job_url(source, external_path) if external_path else "",
                }
            )
        return out
    raise ValueError(f"No fast candidate probe for {ats}")


async def write_candidate_discovery_report_async(
    *,
    candidates_path: Path | str,
    out_path: Path | str,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    payload = json.loads(Path(candidates_path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError(f"{candidates_path} must contain a JSON list")
    candidates = [item for item in payload if isinstance(item, dict)]
    if limit > 0:
        candidates = candidates[:limit]
    report: List[Dict[str, Any]] = []
    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        for candidate in candidates:
            company = str(candidate.get("company") or "").strip()
            if not company:
                continue
            tags = [str(tag).strip().lower() for tag in candidate.get("tags") or [] if str(tag).strip()]
            for surface, probes in _candidate_slug_map(candidate).items():
                direct_probe = surface in {"greenhouse", "lever", "ashby", "smartrecruiters", "workday"}
                adapter = ADAPTERS.get(surface)
                if not adapter and not direct_probe:
                    for probe in probes:
                        report.append(
                            {
                                "company": company,
                                "ats": surface,
                                "token": probe.get("token") or "",
                                "url": probe.get("url") or "",
                                "status": "unsupported_probe",
                                "job_count": 0,
                                "confidence": "unsupported",
                            }
                        )
                    continue
                for probe in probes:
                    source = _candidate_source(company, surface, probe, tags)
                    if surface in {"workday", "icims", "jobvite", "workable", "teamtailor", "bamboohr"} and not source.get("url"):
                        report.append(
                            {
                                "company": company,
                                "ats": surface,
                                "token": source.get("token") or "",
                                "url": "",
                                "status": "needs_url",
                                "job_count": 0,
                                "confidence": "unsupported",
                            }
                        )
                        continue
                    try:
                        if direct_probe:
                            jobs = await _probe_candidate_direct(session, source)
                        else:
                            jobs = await adapter(session, source)
                        importable = _jobs_are_importable(jobs)
                        suggested = dict(source)
                        suggested["enabled"] = bool(importable)
                        report.append(
                            {
                                "company": company,
                                "ats": surface,
                                "token": source.get("token") or "",
                                "url": source.get("url") or "",
                                "status": "valid" if importable else "empty_or_incomplete",
                                "job_count": len(jobs),
                                "confidence": "high" if importable else "empty",
                                "sample_titles": [job.get("title", "") for job in jobs[:5]],
                                "suggested_source": suggested if importable else {**suggested, "enabled": False},
                            }
                        )
                    except SourceStatusError as exc:
                        report.append(
                            {
                                "company": company,
                                "ats": surface,
                                "token": source.get("token") or "",
                                "url": source.get("url") or "",
                                "status": exc.status,
                                "job_count": 0,
                                "confidence": "blocked",
                                "error": str(exc),
                                "suggested_source": {**source, "enabled": False},
                            }
                        )
                    except Exception as exc:
                        report.append(
                            {
                                "company": company,
                                "ats": surface,
                                "token": source.get("token") or "",
                                "url": source.get("url") or "",
                                "status": "error",
                                "job_count": 0,
                                "confidence": "failed",
                                "error": str(exc),
                                "suggested_source": {**source, "enabled": False},
                            }
                        )
    fs.atomic_write_json(out_path, report)
    return report


def write_candidate_discovery_report(
    *,
    candidates_path: Path | str,
    out_path: Path | str,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    return asyncio.run(
        write_candidate_discovery_report_async(
            candidates_path=candidates_path,
            out_path=out_path,
            limit=limit,
        )
    )


_PLACEHOLDER_TITLE_PATTERNS = (
    "test job",
    "test-job",
    "demo job",
    "sn - demo",
    "ceo of kitten mittens",
    "dvsdafdsaf",
    "fdef",
    "fdfd",
)


def _source_identity_from_row(row: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("company") or "").strip().casefold(),
        str(row.get("ats") or "").strip().casefold(),
        str(row.get("token") or "").strip(),
        str(row.get("url") or "").strip(),
    )


def _candidate_report_row_is_promotable(row: Dict[str, Any]) -> bool:
    """Return whether a candidate probe row is safe to append to sources.json."""
    if str(row.get("status") or "").strip().lower() != "valid":
        return False
    suggested = row.get("suggested_source")
    if not isinstance(suggested, dict):
        return False
    if str(suggested.get("ats") or "").strip().lower() not in ADAPTERS:
        return False
    if not str(suggested.get("company") or "").strip():
        return False
    if not str(suggested.get("token") or suggested.get("url") or suggested.get("entry_url") or "").strip():
        return False
    if int(row.get("job_count") or 0) <= 0:
        return False
    titles = [str(title or "").strip().lower() for title in row.get("sample_titles") or []]
    if titles and all(any(pattern in title for pattern in _PLACEHOLDER_TITLE_PATTERNS) for title in titles):
        return False
    if str(suggested.get("ats") or "").strip().lower() == "smartrecruiters":
        joined_titles = " | ".join(titles)
        if int(row.get("job_count") or 0) <= 1 and any(pattern in joined_titles for pattern in _PLACEHOLDER_TITLE_PATTERNS):
            return False
    return True


def probe_and_promote_watchlist(
    *,
    candidates_path: Path | str,
    sources_path: Path | str,
    report_path: Path | str,
    limit: int = 0,
) -> Dict[str, Any]:
    """Probe candidate direct-source rows and append only verified public sources."""
    report = write_candidate_discovery_report(
        candidates_path=candidates_path,
        out_path=report_path,
        limit=limit,
    )
    path = Path(sources_path)
    existing_payload = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else []
    if not isinstance(existing_payload, list):
        raise ValueError(f"{path} must contain a JSON list")
    sources = [dict(row) for row in existing_payload if isinstance(row, dict)]
    skipped_non_objects = len(existing_payload) - len(sources)
    if skipped_non_objects:
        raise ValueError(f"{path} contains {skipped_non_objects} non-object source rows")

    seen = {_source_identity_from_row(row) for row in sources}
    promoted = 0
    duplicate_skipped = 0
    rejected = 0
    for row in report:
        if not _candidate_report_row_is_promotable(row):
            if str(row.get("status") or "").strip().lower() == "valid":
                rejected += 1
            continue
        suggested = dict(row.get("suggested_source") or {})
        suggested["company"] = str(suggested.get("company") or row.get("company") or "").strip()
        suggested["ats"] = str(suggested.get("ats") or row.get("ats") or "").strip().lower()
        suggested["token"] = str(suggested.get("token") or row.get("token") or "").strip()
        suggested["url"] = str(suggested.get("url") or row.get("url") or "").strip()
        suggested["enabled"] = True
        tags = suggested.get("tags") or []
        suggested["tags"] = sorted({str(tag).strip().lower() for tag in tags if str(tag).strip()})
        suggested["notes"] = str(suggested.get("notes") or "Promoted from source watchlist probe.").strip()
        suggested["discovery_notes"] = (
            f"Probe verified {int(row.get('job_count') or 0)} importable jobs; "
            f"report={Path(report_path).name}"
        )
        identity = _source_identity_from_row(suggested)
        if identity in seen:
            duplicate_skipped += 1
            continue
        sources.append(suggested)
        seen.add(identity)
        promoted += 1

    if promoted:
        fs.atomic_write_json(path, sources, trailing_newline=True)

    return {
        "candidates_path": str(candidates_path),
        "sources_path": str(sources_path),
        "report_path": str(report_path),
        "probed": len(report),
        "valid": sum(1 for row in report if str(row.get("status") or "").strip().lower() == "valid"),
        "promoted": promoted,
        "duplicate_skipped": duplicate_skipped,
        "rejected": rejected,
        "source_count": len(sources),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Company ATS job scraper")
    parser.add_argument("--db", default=str(db.DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--sources", default=str(paths.default_sources_path()), help="Source seed JSON path")
    parser.add_argument("--import-sources", action="store_true", help="Import sources before scraping")
    parser.add_argument("--no-scrape", action="store_true", help="Only initialize/import/export")
    parser.add_argument("--export-json", default="", help="Export filtered jobs to this JSON path")
    parser.add_argument("--remote", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--india-office-hybrid", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--interest-terms",
        default=",".join(DEFAULT_INTEREST_TERMS),
        help="Comma-separated target tech/domain terms.",
    )
    parser.add_argument("--require-words", default="", help="Optional extra required words.")
    parser.add_argument("--include-groups", default="", help="Optional extra include groups.")
    parser.add_argument("--exclude-words", default="visa,relocation")
    parser.add_argument("--include-words", default="")
    parser.add_argument("--include-mode", choices=["any", "all"], default="any")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--source-id", action="append", type=int, default=[], help="Scrape only the specified source id; repeatable.")
    parser.add_argument("--company", action="append", default=[], help="Scrape only sources matching this company name; repeatable.")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--discover-report",
        default="",
        help="Validate sources and write a JSON discovery report instead of scraping.",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=0,
        help="Limit the number of sources checked by --discover-report.",
    )
    parser.add_argument(
        "--discover-candidates",
        default="",
        help="Probe a candidate company watchlist against public ATS surfaces instead of scraping.",
    )
    parser.add_argument(
        "--discover-candidates-out",
        default="source_candidate_report.json",
        help="JSON output path for --discover-candidates.",
    )
    parser.add_argument(
        "--discover-candidates-limit",
        type=int,
        default=0,
        help="Limit the number of candidate companies checked by --discover-candidates.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    db_path = Path(args.db)
    sources_path = Path(args.sources)
    db.init_db(db_path)
    if args.import_sources:
        count = db.import_sources(db_path, sources_path)
        print(f"Imported {count} sources.")

    if args.discover_report:
        report = write_source_discovery_report(
            sources_path=sources_path,
            out_path=args.discover_report,
            limit=max(0, args.discover_limit),
        )
        print(f"Wrote {len(report)} source discovery rows to {args.discover_report}.")
        return

    if args.discover_candidates:
        report = write_candidate_discovery_report(
            candidates_path=args.discover_candidates,
            out_path=args.discover_candidates_out,
            limit=max(0, args.discover_candidates_limit),
        )
        valid = sum(1 for row in report if row.get("status") == "valid")
        print(f"Wrote {len(report)} candidate probe rows to {args.discover_candidates_out}; valid={valid}.")
        return

    if not args.no_scrape:
        options = ScrapeOptions(
            require_words=parse_word_list(args.require_words),
            include_groups=parse_include_groups(args.include_groups),
            exclude_words=parse_word_list(args.exclude_words),
            include_words=parse_word_list(args.include_words),
            include_mode=args.include_mode,
            interest_terms=parse_word_list(args.interest_terms),
            enable_remote=args.remote,
            enable_india_office_hybrid=args.india_office_hybrid,
            concurrency=max(1, args.concurrency),
            only_source_ids=list(args.source_id or []),
            only_companies=list(args.company or []),
        )
        results = scrape_all(
            db_path=db_path,
            sources_path=sources_path,
            options=options,
            progress=print,
        )
        print(
            json.dumps(
                [result.__dict__ for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )

    if args.export_json:
        count = db.export_jobs_json(db_path, args.export_json, matching_only=True, open_only=True)
        print(f"Exported {count} matching open jobs to {args.export_json}.")


if __name__ == "__main__":
    main()

