#!/usr/bin/env python3
"""
Hacker News Topic Extractor (async + incremental + filters + logs + matched-words + include-groups)

NEW: Include-group mode (AND groups + required words)
- You can provide multiple include groups like:
    [remote, python], [remote, rust], [remote, c++]
  Meaning: keep a comment if it matches ANY one group (AND within group).

- You can also enforce REQUIRED words (e.g. [remote]) so if "remote" isn't present,
  the comment is dropped even if it matched other things.

CLI examples:
  --require-words "remote"
  --include-groups "remote+python;remote+rust;remote+c++"

Matching rules (in order):
1) Exclude words: if comment contains ANY exclude word -> drop
2) Required words: must contain ALL required words -> else drop
3) Include logic:
   - If include-groups given -> must match at least ONE group fully (ALL words in that group)
   - Else if include-words given -> uses --include-mode (any/all) against include-words
   - Else (no include-groups, no include-words) -> comment passes (subject to required/exclude)

Output JSON (expanded to include metadata):
[
  {
    "post": "abc",
    "comments": [
      {
        "text": "...",
        "time": 1700000000,
        "matched_required_words": ["remote"],
        "matched_include_words": ["remote","python"],     # if include-words mode
        "matched_include_group": ["remote","python"]      # if include-groups mode
      }
    ]
  }
]

(Comments sorted latest-first; incremental updates via state file; async parallel fetch.)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from ..storage import fs


ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"
USER_AGENT = "hn-topic-extractor/2.2"

_TAG_RE = re.compile(r"<[^>]+>")


# -----------------------------
# Logging
# -----------------------------

def setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )


# -----------------------------
# Utilities
# -----------------------------

def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = unescape(html_text)
    text = text.replace("<p>", "\n\n").replace("</p>", "")
    text = text.replace("<pre><code>", "\n\n").replace("</code></pre>", "")
    text = _TAG_RE.sub("", text)
    return text.strip()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj: Any) -> None:
    fs.atomic_write_json(path, obj)


def now_ts() -> int:
    return int(time.time())


def year_month_to_epoch_utc(year: int, month: int) -> int:
    dt = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def parse_word_list(csv: str) -> List[str]:
    if not csv.strip():
        return []
    return [w.strip() for w in csv.split(",") if w.strip()]


def parse_include_groups(spec: str) -> List[List[str]]:
    """
    Parse group spec:
      "remote_python;remote_rust;remote_c++"
    -> [["remote","python"], ["remote","rust"], ["remote","c++"]]
    """
    spec = spec.strip()
    if not spec:
        return []
    groups: List[List[str]] = []
    for g in spec.split(";"):
        g = g.strip()
        if not g:
            continue
        words = [w.strip() for w in g.split("_") if w.strip()]
        if words:
            groups.append(words)
    return groups


def matched_words(text: str, words: List[str]) -> List[str]:
    """Return list of words (preserving original list casing) that appear as substrings (case-insensitive)."""
    tl = text.lower()
    out: List[str] = []
    for w in words:
        if w.lower() in tl:
            out.append(w)
    return out


# def contains_all(text: str, words: List[str]) -> bool:
#     tl = text.lower()
#     return all(w.lower() in tl for w in words)

# not match rust with trust.
def contains_all(text: str, words: List[str]) -> bool:
    text = text.lower()
    return all(
        re.search(rf"\b{re.escape(w.lower())}\b", text) is not None
        for w in words
    )



# -----------------------------
# Async HTTP
# -----------------------------

async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    params: Optional[dict] = None,
    timeout_s: int = 20,
    retries: int = 3,
    backoff_s: float = 0.6,
) -> Any:
    last_err: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            last_err = e
            if attempt < retries:
                await asyncio.sleep(backoff_s * attempt)
    raise last_err  # type: ignore[misc]


# -----------------------------
# HN search + items
# -----------------------------

@dataclass(frozen=True)
class StoryHit:
    id: int
    title: str
    created_at: int
    url: Optional[str]
    num_comments: int


async def search_stories(
    session: aiohttp.ClientSession,
    topic: str,
    max_pages: int,
    hits_per_page: int,
    since_epoch: Optional[int],
) -> List[StoryHit]:
    topic_lc = topic.lower().strip()
    out: List[StoryHit] = []

    logging.info("Searching stories: topic=%r pages=%d hits/page=%d", topic, max_pages, hits_per_page)

    for page in range(max_pages):
        logging.info("Search page %d/%d ...", page + 1, max_pages)
        try:
            payload = await fetch_json(
                session,
                ALGOLIA_SEARCH_URL,
                params={
                    "query": topic,
                    "tags": "story",
                    "page": page,
                    "hitsPerPage": hits_per_page,
                },
            )
        except Exception as e:
            logging.warning("Algolia search failed page=%d: %s", page + 1, e)
            continue

        hits = payload.get("hits", []) or []
        logging.info("  Hits: %d", len(hits))

        matched = 0
        for h in hits:
            title = (h.get("title") or "").strip()
            if not title or topic_lc not in title.lower():
                continue

            created_at_i = h.get("created_at_i")
            if not isinstance(created_at_i, int):
                continue
            if since_epoch is not None and created_at_i < since_epoch:
                continue

            object_id = h.get("objectID")
            if not object_id:
                continue

            num_comments = h.get("num_comments") or 0
            if not isinstance(num_comments, int):
                num_comments = 0

            out.append(
                StoryHit(
                    id=int(object_id),
                    title=title,
                    created_at=created_at_i,
                    url=h.get("url"),
                    num_comments=num_comments,
                )
            )
            matched += 1

        logging.info("  Matched on page: %d", matched)

        nb_pages = payload.get("nbPages")
        if isinstance(nb_pages, int) and page >= nb_pages - 1:
            break

    uniq = {x.id: x for x in out}
    final = sorted(uniq.values(), key=lambda x: x.created_at)
    logging.info("Total matching stories (deduped): %d", len(final))
    return final


async def fetch_item(session: aiohttp.ClientSession, item_id: int) -> Optional[dict]:
    try:
        data = await fetch_json(session, HN_ITEM_URL.format(item_id))
    except Exception as e:
        logging.warning("Fetch item failed id=%d: %s", item_id, e)
        return None
    return data if isinstance(data, dict) else None


def top_level_comment_ids(story_item: dict) -> List[int]:
    kids = story_item.get("kids") or []
    out: List[int] = []
    for k in kids:
        if isinstance(k, int):
            out.append(k)
        elif isinstance(k, str) and k.isdigit():
            out.append(int(k))
    return out


# -----------------------------
# Comment filters + group matching
# -----------------------------

def comment_filter_and_match(
    text: str,
    exclude_words: List[str],
    require_words: List[str],
    include_words: List[str],
    include_mode: str,             # "any" | "all"
    include_groups: List[List[str]],
) -> Tuple[bool, Dict[str, Any]]:
    """
    Returns (keep?, meta) where meta includes:
      matched_required_words, matched_include_words, matched_include_group
    """
    t = text.strip()
    if not t:
        return (False, {})

    tl = t.lower()

    # Exclude
    for w in exclude_words:
        if w.lower() in tl:
            return (False, {})

    # Required: ALL must be present
    if require_words and not contains_all(t, require_words):
        # still provide partial matches if you want later
        return (False, {})

    meta: Dict[str, Any] = {
        "matched_required_words": matched_words(t, require_words) if require_words else [],
        "matched_include_words": [],
        "matched_include_group": [],
    }

    # Include-groups mode: must match at least ONE group fully
    if include_groups:
        for grp in include_groups:
            if contains_all(t, grp):
                meta["matched_include_group"] = grp[:]  # record the group words (as provided)
                return (True, meta)
        return (False, meta)

    # Else include-words mode (any/all) if provided
    if include_words:
        inc_matched = matched_words(t, include_words)
        meta["matched_include_words"] = inc_matched

        if include_mode == "all":
            return (len(inc_matched) == len(include_words), meta)
        else:  # any
            return (len(inc_matched) > 0, meta)

    # No include constraint -> pass (since required already enforced)
    return (True, meta)


async def fetch_top_level_comments_filtered(
    session: aiohttp.ClientSession,
    story_id: int,
    comment_ids: List[int],
    exclude_words: List[str],
    require_words: List[str],
    include_words: List[str],
    include_mode: str,
    include_groups: List[List[str]],
    concurrency: int,
) -> Dict[int, Dict[str, Any]]:
    """
    Return {cid: {"time": int, "text": str, "matched_required_words": [...], "matched_include_words": [...], "matched_include_group": [...]}}
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(cid: int) -> Optional[Tuple[int, Dict[str, Any]]]:
        async with sem:
            c = await fetch_item(session, cid)

        if not c:
            return None
        if c.get("type") != "comment":
            return None
        if c.get("parent") != story_id:
            return None
        if c.get("dead") or c.get("deleted"):
            return None

        c_time = c.get("time")
        if not isinstance(c_time, int):
            return None

        text = strip_html(c.get("text") or "")
        keep, meta = comment_filter_and_match(
            text=text,
            exclude_words=exclude_words,
            require_words=require_words,
            include_words=include_words,
            include_mode=include_mode,
            include_groups=include_groups,
        )
        if not keep:
            return None

        out = {"time": c_time, "text": text}
        out.update(meta)
        return (cid, out)

    total = len(comment_ids)
    if total == 0:
        return {}

    logging.info("    Fetching %d new top-level comments (concurrency=%d)...", total, concurrency)

    tasks = [asyncio.create_task(one(cid)) for cid in comment_ids]
    out: Dict[int, Dict[str, Any]] = {}

    done = 0
    for fut in asyncio.as_completed(tasks):
        res = await fut
        done += 1
        if done % 100 == 0 or done == total:
            logging.info("    Progress: %d/%d comment fetches done", done, total)
        if res:
            cid, meta = res
            out[cid] = meta
            logging.debug(
                "      Kept comment id=%d req=%s group=%s include=%s",
                cid,
                meta.get("matched_required_words"),
                meta.get("matched_include_group"),
                meta.get("matched_include_words"),
            )

    return out


# -----------------------------
# Incremental State
# -----------------------------

def make_empty_state(topic: str) -> dict:
    return {"version": 1, "topic": topic, "updated_at": 0, "posts": {}}


def load_state(path: Path, topic: str) -> dict:
    st = load_json(path, default=None)
    if not isinstance(st, dict) or st.get("topic") != topic:
        if path.exists():
            logging.warning("State invalid or topic mismatch; reinitializing: %s", path)
        return make_empty_state(topic)

    st.setdefault("version", 1)
    st.setdefault("updated_at", 0)
    st.setdefault("posts", {})
    if not isinstance(st["posts"], dict):
        st["posts"] = {}
    return st


async def update_state_with_story(
    session: aiohttp.ClientSession,
    state: dict,
    hit: StoryHit,
    min_post_comments: int,
    exclude_words: List[str],
    require_words: List[str],
    include_words: List[str],
    include_mode: str,
    include_groups: List[List[str]],
    comment_concurrency: int,
) -> Tuple[int, int]:
    # Fast pre-filter from Algolia
    if hit.num_comments < min_post_comments:
        logging.info("Skipping (num_comments=%d < %d): %s", hit.num_comments, min_post_comments, hit.title)
        return (0, 0)

    story_item = await fetch_item(session, hit.id)
    if not story_item or story_item.get("type") != "story":
        return (0, 0)

    posts: dict = state["posts"]
    pid = str(hit.id)
    is_new = pid not in posts
    logging.info("%s post: %s", "New" if is_new else "Updating", hit.title)

    post_bucket = posts.get(pid)
    if not isinstance(post_bucket, dict):
        post_bucket = {"title": hit.title, "url": hit.url, "time": hit.created_at, "comments": {}}
        posts[pid] = post_bucket
    else:
        post_bucket["title"] = hit.title
        post_bucket["url"] = hit.url
        post_bucket["time"] = hit.created_at
        if not isinstance(post_bucket.get("comments"), dict):
            post_bucket["comments"] = {}

    existing_comments: dict = post_bucket["comments"]
    existing_ids = {int(k) for k in existing_comments.keys() if str(k).isdigit()}

    kids = top_level_comment_ids(story_item)

    # Accurate filter based on actual top-level kids count
    if len(kids) < min_post_comments:
        logging.info("Skipping (top-level kids=%d < %d): %s", len(kids), min_post_comments, hit.title)
        return (0, len(kids))

    new_ids = [cid for cid in kids if cid not in existing_ids]
    logging.info("  Top-level comments: %d total, %d new", len(kids), len(new_ids))

    if not new_ids:
        return (0, len(kids))

    new_comments = await fetch_top_level_comments_filtered(
        session=session,
        story_id=hit.id,
        comment_ids=new_ids,
        exclude_words=exclude_words,
        require_words=require_words,
        include_words=include_words,
        include_mode=include_mode,
        include_groups=include_groups,
        concurrency=comment_concurrency,
    )

    added = 0
    for cid, meta in new_comments.items():
        existing_comments[str(cid)] = meta
        added += 1

    logging.info("  Added %d new comments after filters", added)
    return (added, len(kids))


def state_to_output(state: dict) -> List[dict]:
    """
    Output list:
      { "post": "title", "comments": [ {text,time,matched_*}, ... ] }
    Comments sorted by time DESC (latest first). Posts sorted by story time ASC.
    """
    posts: dict = state.get("posts", {}) or {}
    out: List[Tuple[int, dict]] = []

    for _, p in posts.items():
        if not isinstance(p, dict):
            continue

        title = p.get("title") or ""
        ptime = p.get("time") or 0
        comments = p.get("comments") or {}
        if not isinstance(comments, dict):
            comments = {}

        sorted_comments = sorted(
            (
                {
                    "time": int(meta.get("time", 0)),
                    "text": meta.get("text", ""),
                    "matched_required_words": meta.get("matched_required_words", []),
                    "matched_include_words": meta.get("matched_include_words", []),
                    "matched_include_group": meta.get("matched_include_group", []),
                }
                for meta in comments.values()
                if isinstance(meta, dict) and meta.get("text")
            ),
            key=lambda x: x["time"],
            reverse=True,
        )

        out.append(
            (
                int(ptime) if isinstance(ptime, int) else 0,
                {"post": title, "comments": sorted_comments},
            )
        )

    out.sort(key=lambda x: x[0])
    return [item for _, item in out]


# -----------------------------
# Orchestration (async)
# -----------------------------

async def run_async(
    topic: str,
    out_path: Path,
    state_path: Path,
    max_pages: int,
    hits_per_page: int,
    since_year: Optional[int],
    since_month: Optional[int],
    min_post_comments: int,
    exclude_words: List[str],
    require_words: List[str],
    include_words: List[str],
    include_mode: str,
    include_groups: List[List[str]],
    story_concurrency: int,
    comment_concurrency: int,
) -> None:
    state = load_state(state_path, topic)
    logging.info("Loaded state: %d posts tracked (%s)", len(state["posts"]), state_path)

    since_epoch: Optional[int] = None
    if since_year is not None and since_month is not None:
        since_epoch = year_month_to_epoch_utc(since_year, since_month)
        logging.info("Filtering posts since: %04d-%02d (epoch=%d)", since_year, since_month, since_epoch)

    if include_groups:
        logging.info("Include groups enabled: %s", include_groups)
    if require_words:
        logging.info("Require words: %s", require_words)

    connector = aiohttp.TCPConnector(limit=0)
    headers = {"User-Agent": USER_AGENT}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        hits = await search_stories(session, topic, max_pages, hits_per_page, since_epoch)

        if not hits:
            logging.warning("No matching stories found.")
            state["updated_at"] = now_ts()
            save_json(state_path, state)
            save_json(out_path, state_to_output(state))
            return

        sem_story = asyncio.Semaphore(story_concurrency)
        total_added = 0
        total_kids_seen = 0

        async def process_one(idx: int, hit: StoryHit) -> Tuple[int, int]:
            async with sem_story:
                logging.info("Processing story %d/%d id=%d", idx, len(hits), hit.id)
                return await update_state_with_story(
                    session=session,
                    state=state,
                    hit=hit,
                    min_post_comments=min_post_comments,
                    exclude_words=exclude_words,
                    require_words=require_words,
                    include_words=include_words,
                    include_mode=include_mode,
                    include_groups=include_groups,
                    comment_concurrency=comment_concurrency,
                )

        tasks = [asyncio.create_task(process_one(i, hit)) for i, hit in enumerate(hits, start=1)]

        completed = 0
        for fut in asyncio.as_completed(tasks):
            added, kids_seen = await fut
            total_added += added
            total_kids_seen += kids_seen
            completed += 1
            if completed % 5 == 0 or completed == len(tasks):
                logging.info("Stories progress: %d/%d done", completed, len(tasks))

    state["updated_at"] = now_ts()

    logging.info("Writing state -> %s", state_path)
    save_json(state_path, state)

    output = state_to_output(state)
    logging.info("Writing output -> %s", out_path)
    save_json(out_path, output)

    logging.info(
        "Done. posts tracked=%d | top-level seen=%d | new comments added=%d",
        len(state.get("posts", {})),
        total_kids_seen,
        total_added,
    )


def run(**kwargs: Any) -> None:
    asyncio.run(run_async(**kwargs))


# -----------------------------
# CLI
# -----------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="HN topic extractor (async, incremental, filters, include-groups).")

    p.add_argument("--topic", required=True, help="String to match in post titles (case-insensitive).")
    p.add_argument("--out", default="hn_topic_output.json", help="Output JSON path.")
    p.add_argument("--state", default="hn_topic_state.json", help="State JSON path (incremental updates).")

    p.add_argument("--pages", type=int, default=5, help="Algolia pages to scan.")
    p.add_argument("--hits", type=int, default=100, help="Hits per Algolia page (max ~1000).")

    p.add_argument("--since-year", type=int, default=None, help="Only include posts after this year (needs --since-month).")
    p.add_argument("--since-month", type=int, default=None, help="Only include posts after this month 1-12 (needs --since-year).")

    p.add_argument("--min-post-comments", type=int, default=0, help="Only process posts with >= this many TOP-LEVEL comments.")

    p.add_argument("--exclude-words", default="", help="Comma-separated words: drop comment if contains ANY of these.")
    p.add_argument("--require-words", default="", help="Comma-separated REQUIRED words: comment must contain ALL of them.")

    # Basic include list mode (kept for backwards compatibility)
    p.add_argument("--include-words", default="", help="Comma-separated words: include filter (ignored if --include-groups is set).")
    p.add_argument(
        "--include-mode",
        choices=["any", "all"],
        default="any",
        help="Include-words mode: any=match at least one; all=must match all. Ignored if --include-groups is set.",
    )

    # NEW: include groups
    p.add_argument(
        "--include-groups",
        default="",
        help='Group spec: "remote+python;remote+rust;remote+c++" (match ANY group; AND within group).',
    )

    p.add_argument("--story-concurrency", type=int, default=3, help="How many stories to process in parallel.")
    p.add_argument("--comment-concurrency", type=int, default=60, help="How many comment fetches in parallel per story.")

    p.add_argument("--debug", action="store_true", help="Enable debug logs.")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.debug)

    if (args.since_year is None) ^ (args.since_month is None):
        raise SystemExit("Error: use --since-year AND --since-month together (or neither).")
    if args.since_month is not None and not (1 <= args.since_month <= 12):
        raise SystemExit("Error: --since-month must be 1..12")

    include_groups = parse_include_groups(args.include_groups)

    run(
        topic=args.topic,
        out_path=Path(args.out),
        state_path=Path(args.state),
        max_pages=max(1, args.pages),
        hits_per_page=max(1, min(args.hits, 1000)),
        since_year=args.since_year,
        since_month=args.since_month,
        min_post_comments=max(0, args.min_post_comments),
        exclude_words=parse_word_list(args.exclude_words),
        require_words=parse_word_list(args.require_words),
        include_words=parse_word_list(args.include_words),
        include_mode=args.include_mode,
        include_groups=include_groups,
        story_concurrency=max(1, args.story_concurrency),
        comment_concurrency=max(1, args.comment_concurrency),
    )


if __name__ == "__main__":
    main()


## run as:
#  uv run python .\hn_topic_extractor.py `
#   --topic "who is hiring" `
#   --require-words "remote" `
#   --include-groups "remote_python;remote_rust;remote_c++" `
#   --since-year 2025 --since-month 10 `
#   --min-post-comments 200 `
#   --exclude-words "visa,relocation" `
#   --story-concurrency 5 --comment-concurrency 80 `
#   --out out.json --state state.json

# uv run python .\hn_topic_extractor.py --topic "who is hiring" --require-words "remote" --include-groups "remote_python;remote_rust;remote_c++" --since-year 2022 --since-month 10 --min-post-comments 200 --exclude-words "visa,relocation" --story-concurrency 5 --comment-concurrency 80 --out out.json --state state.json
# uv run python .\hn_topic_extractor.py --topic "what are you working on" --since-year 2019 --since-month 01 --min-post-comments 100 --exclude-words "visa,relocation" --story-concurrency 5 --comment-concurrency 80 --out working_on_2019.json --state working_on_2019.json
