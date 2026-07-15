import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from jobscraper.scraping import core
from jobscraper.storage import db


async def _run_capped_requests() -> dict[str, int]:
    class FakeResponse:
        def __init__(self, semaphore: asyncio.Semaphore) -> None:
            self.semaphore = semaphore

        async def __aenter__(self):
            await self.semaphore.acquire()
            await asyncio.sleep(0.01)
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.semaphore.release()

        def raise_for_status(self) -> None:
            return None

        async def json(self, content_type=None):
            return {"ok": True}

    class FakeSession:
        def __init__(self, cap: int) -> None:
            self.semaphore = asyncio.Semaphore(cap)

        def get(self, *_args: object, **_kwargs: object) -> FakeResponse:
            return FakeResponse(self.semaphore)

    tracker = {"current": 0, "max": 0}
    session = FakeSession(cap=8)
    core.attach_http_tracker(session, tracker)
    results = await asyncio.gather(*(core.fetch_json(session, "https://example.test/", retries=1) for _ in range(100)))
    assert len(results) == 100
    assert all(result == {"ok": True} for result in results)
    return tracker


def test_global_http_cap_tracks_max_in_flight_requests() -> None:
    tracker = asyncio.run(_run_capped_requests())

    assert tracker["max"] <= 8
    assert tracker["current"] == 0


def test_jobicy_adapter_normalizes_public_api_payload(monkeypatch) -> None:
    async def fake_fetch_json(_session, _url, **_kwargs):
        return {
            "jobs": [
                {
                    "id": 123,
                    "jobTitle": "Backend Engineer",
                    "companyName": "Example Co",
                    "jobGeo": "Remote",
                    "jobType": "Full-time",
                    "jobIndustry": ["Software"],
                    "url": "https://jobicy.com/jobs/123",
                    "pubDate": "2026-01-01",
                    "jobDescription": "<p>Python systems work</p>",
                }
            ]
        }

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)
    source = {"company": "Jobicy", "ats": "jobicy_api", "url": "https://jobicy.com/api/v2/remote-jobs"}

    jobs = asyncio.run(core.fetch_jobicy_api(None, source))

    assert jobs[0]["source_job_id"] == "123"
    assert jobs[0]["company"] == "Example Co"
    assert jobs[0]["title"] == "Backend Engineer"
    assert jobs[0]["location"] == "Remote"
    assert "Python systems work" in jobs[0]["text"]


def test_themuse_adapter_normalizes_public_api_payload(monkeypatch) -> None:
    async def fake_fetch_json(_session, _url, **_kwargs):
        return {
            "results": [
                {
                    "id": 456,
                    "name": "Platform Engineer",
                    "company": {"name": "Muse Co"},
                    "locations": [{"name": "New York, NY"}],
                    "categories": [{"name": "Engineering"}],
                    "levels": [{"name": "Mid Level"}],
                    "refs": {"landing_page": "https://www.themuse.com/jobs/museco/platform-engineer"},
                    "publication_date": "2026-01-02T00:00:00Z",
                    "contents": "<p>Go platform work</p>",
                }
            ]
        }

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)
    source = {"company": "The Muse", "ats": "themuse_api", "url": "https://www.themuse.com/api/public/jobs?page=1"}

    jobs = asyncio.run(core.fetch_themuse_api(None, source))

    assert jobs[0]["source_job_id"] == "456"
    assert jobs[0]["company"] == "Muse Co"
    assert jobs[0]["title"] == "Platform Engineer"
    assert jobs[0]["department"] == "Engineering"
    assert "Go platform work" in jobs[0]["text"]


def test_workingnomads_adapter_normalizes_public_api_payload(monkeypatch) -> None:
    async def fake_fetch_json(_session, _url, **_kwargs):
        return [
            {
                "id": "wn-789",
                "title": "Remote Rust Engineer",
                "company_name": "Nomad Co",
                "location": "Remote",
                "category": "Development",
                "job_type": "Full-time",
                "url": "https://www.workingnomads.com/jobs/wn-789",
                "pub_date": "2026-01-03",
                "description": "<p>Rust backend role</p>",
            }
        ]

    monkeypatch.setattr(core, "fetch_json", fake_fetch_json)
    source = {
        "company": "Working Nomads",
        "ats": "workingnomads_api",
        "url": "https://www.workingnomads.com/api/exposed_jobs/",
    }

    jobs = asyncio.run(core.fetch_workingnomads_api(None, source))

    assert jobs[0]["source_job_id"] == "wn-789"
    assert jobs[0]["company"] == "Nomad Co"
    assert jobs[0]["title"] == "Remote Rust Engineer"
    assert jobs[0]["department"] == "Development"
    assert "Rust backend role" in jobs[0]["text"]


def test_hiringcafe_adapter_normalizes_ssr_payload(monkeypatch) -> None:
    payload = {
        "props": {
            "pageProps": {
                "ssrHits": [
                    {
                        "id": "workday___example___123",
                        "objectID": "workday___example___123",
                        "source": "workday",
                        "source_and_board_token": "workday_example",
                        "apply_url": "https://example.com/jobs/123",
                        "job_information": {"title": "Platform Engineer"},
                        "v5_processed_job_data": {
                            "company_name": "Example Labs",
                            "core_job_title": "Platform Engineer",
                            "formatted_workplace_location": "Remote, US",
                            "commitment": ["Full Time"],
                            "job_category": "Engineering",
                            "seniority_level": "Senior Level",
                            "requirements_summary": "Python and distributed systems",
                            "technical_tools": ["Python", "Kubernetes"],
                            "estimated_publish_date_millis": 1767225600000,
                        },
                        "enriched_company_data": {"name": "Example Labs"},
                    }
                ]
            }
        }
    }

    async def fake_fetch_text(_session, _url, **_kwargs):
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    monkeypatch.setattr(core, "fetch_text", fake_fetch_text)
    source = {"company": "HiringCafe", "ats": "hiringcafe_search", "url": "https://hiring.cafe/"}

    jobs = asyncio.run(core.fetch_hiringcafe_search(None, source))

    assert jobs[0]["source_job_id"] == "workday___example___123"
    assert jobs[0]["company"] == "Example Labs"
    assert jobs[0]["title"] == "Platform Engineer"
    assert jobs[0]["location"] == "Remote, US"
    assert jobs[0]["department"] == "Engineering"
    assert jobs[0]["employment_type"] == "Full Time"
    assert jobs[0]["apply_url"] == "https://example.com/jobs/123"
    assert jobs[0]["published_at"] == "2026-01-01T00:00:00+00:00"
    assert "Python and distributed systems" in jobs[0]["text"]


def test_paloalto_adapter_normalizes_paginated_public_html(monkeypatch) -> None:
    first_page = """
    <section id="search-results" data-total-pages="2" data-current-page="1" data-records-per-page="15">
      <ul>
        <li class="section29__search-results-li">
          <a class="section29__search-results-link" href="/en/job/hyderabad/senior-staff-product-owner/47263/97123951120" data-job-id="97123951120">
            <h2 class="section29__search-results-job-title">Senior Staff Product Owner</h2>
            <div class="section29__result-info-container">
              <span class="section29__result-location">Hyderabad, Telangana, India</span>
              <span class="section29__result-category"><span>Product Management</span></span>
            </div>
          </a>
        </li>
      </ul>
    </section>
    """
    second_page = """
    <section id="search-results" data-total-pages="2" data-current-page="2" data-records-per-page="15">
      <ul>
        <li class="section29__search-results-li">
          <a class="section29__search-results-link" href="/en/job/santa-clara/sr-staff-machine-learning-engineer/47263/97240719264" data-job-id="97240719264">
            <h2 class="section29__search-results-job-title">Sr Staff Machine Learning Engineer</h2>
            <div class="section29__result-info-container">
              <span class="section29__result-location">Santa Clara, California, United States</span>
              <span class="section29__result-category"><span>Product Engineering</span></span>
            </div>
          </a>
        </li>
      </ul>
    </section>
    """

    async def fake_fetch_text(_session, url, **_kwargs):
        return second_page if "p=2" in url else first_page

    monkeypatch.setattr(core, "fetch_text", fake_fetch_text)
    source = {"company": "Palo Alto Networks", "ats": "paloalto_search", "url": "https://jobs.paloaltonetworks.com/en/search-jobs"}

    jobs = asyncio.run(core.fetch_paloalto_search(None, source))

    assert len(jobs) == 2
    assert jobs[0]["source_job_id"] == "97123951120"
    assert jobs[0]["title"] == "Senior Staff Product Owner"
    assert jobs[0]["location"] == "Hyderabad, Telangana, India"
    assert jobs[0]["department"] == "Product Management"
    assert jobs[1]["source_job_id"] == "97240719264"
    assert jobs[1]["job_url"] == "https://jobs.paloaltonetworks.com/en/job/santa-clara/sr-staff-machine-learning-engineer/47263/97240719264"


def test_twosigma_adapter_normalizes_paginated_openroles_html(monkeypatch) -> None:
    first_page = """
    <article class="article article--result" id="article--1">
      <div class="article__header">
        <div class="article__header__text">
          <h3 class="article__header__text__title title title--065">
            <a class="link" href="https://careers.twosigma.com/careers/JobDetail/New-York-New-York-United-States-AI-Research-Scientist-Campus-Full-Time/13671">
              AI Research Scientist - Campus Full-Time
            </a>
          </h3>
          <div class="article__header__content">
            <div class="article__header__content__text">
              <span class="paragraph_inner-span">United States - NY New York</span>
              <div class="article__header__content__sub-text">
                <span class="paragraph_inner-span">Quantitative Research</span>
                <span class="paragraph_inner-span">Early Careers</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </article>
    <div class="list-controls__pagination">
      <nav aria-label="Pagination Navigation">
        <a class="list-controls__pagination__item paginationNextLink" href="https://careers.twosigma.com/careers/OpenRoles/?jobRecordsPerPage=10&amp;jobOffset=10">Next &gt;&gt;</a>
      </nav>
    </div>
    """
    second_page = """
    <article class="article article--result" id="article--2">
      <div class="article__header">
        <div class="article__header__text">
          <h3 class="article__header__text__title title title--065">
            <a class="link" href="https://careers.twosigma.com/careers/JobDetail/London-United-Kingdom-of-Great-Britain-and-Northern-Ireland-Client-Services-Associate/13936">
              Client Services Associate
            </a>
          </h3>
          <div class="article__header__content">
            <div class="article__header__content__text">
              <span class="paragraph_inner-span">United Kingdom - London</span>
              <div class="article__header__content__sub-text">
                <span class="paragraph_inner-span">Client Services</span>
                <span class="paragraph_inner-span">Experienced Professional</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </article>
    """

    async def fake_fetch_text(_session, url, **_kwargs):
        return second_page if "jobOffset=10" in url else first_page

    monkeypatch.setattr(core, "fetch_text", fake_fetch_text)
    source = {"company": "Two Sigma", "ats": "twosigma_search", "url": "https://careers.twosigma.com/careers/OpenRoles"}

    jobs = asyncio.run(core.fetch_twosigma_search(None, source))

    assert len(jobs) == 2
    assert jobs[0]["source_job_id"] == "13671"
    assert jobs[0]["title"] == "AI Research Scientist - Campus Full-Time"
    assert jobs[0]["location"] == "United States - NY New York"
    assert jobs[0]["department"] == "Quantitative Research, Early Careers"
    assert jobs[1]["source_job_id"] == "13936"
    assert jobs[1]["job_url"] == "https://careers.twosigma.com/careers/JobDetail/London-United-Kingdom-of-Great-Britain-and-Northern-Ireland-Client-Services-Associate/13936"


def test_aijobsnet_adapter_normalizes_public_html_list(monkeypatch) -> None:
    html_text = """
    <ul id="job_list">
      <li class="d-flex justify-content-between position-relative pb-2 py-2 mb-1">
        <div>
          <div>
            <a class="font-monospace fw-bold stretched-link" href="/job/platform-engineer-123/" target="_blank">
              <span class="fw-light text-bg-primary px-1 rounded d-none d-sm-inline">Featured</span>
              Platform Engineer
            </a>
            <span class="text-bg-success px-1 rounded">USD 120K-150K</span>
          </div>
          <div>
            <span>Python</span> | <span>Distributed Systems</span>
          </div>
          <div>
            <span class="text-success">Remote work</span> | <span class="text-success">Flexible hours</span>
          </div>
        </div>
        <div class="text-end">
          <div>
            <span class="text-bg-warning px-1 rounded">Senior-level</span>
            <span class="text-bg-secondary px-1 rounded">Full Time</span>
          </div>
          <div>
            Remote
            <span class="text-bg-success px-1 rounded">R</span>
          </div>
          <div class="text-muted">6d ago</div>
        </div>
      </li>
    </ul>
    """

    async def fake_fetch_text(_session, _url, **_kwargs):
        return html_text

    monkeypatch.setattr(core, "fetch_text", fake_fetch_text)
    source = {"company": "AIJobs.net", "ats": "aijobsnet_search", "url": "https://aijobs.net/"}

    jobs = asyncio.run(core.fetch_aijobsnet_search(None, source))

    assert jobs[0]["source_job_id"] == "123"
    assert jobs[0]["company"] == "AIJobs.net"
    assert jobs[0]["title"] == "Platform Engineer"
    assert jobs[0]["location"] == "Remote"
    assert jobs[0]["department"] == "Python, Distributed Systems"
    assert jobs[0]["employment_type"] == "Full Time"
    assert jobs[0]["job_url"] == "https://aijobs.net/job/platform-engineer-123/"
    assert "USD 120K-150K" in jobs[0]["text"]
    assert "Flexible hours" in jobs[0]["text"]


def test_batch_c_portal_wrappers_delegate_to_generic_public_board_search(monkeypatch) -> None:
    seen: list[str] = []

    async def fake_fetch_public_board_search(_session, _source, *, portal):
        seen.append(portal)
        return [{"title": portal}]

    monkeypatch.setattr(core, "fetch_public_board_search", fake_fetch_public_board_search)

    echo_jobs = asyncio.run(core.fetch_echojobs_search(None, {"company": "EchoJobs", "ats": "echojobs_search"}))
    datayoshi_jobs = asyncio.run(core.fetch_datayoshi_search(None, {"company": "DataYoshi", "ats": "datayoshi_search"}))

    assert echo_jobs == [{"title": "generic"}]
    assert datayoshi_jobs == [{"title": "generic"}]
    assert seen == ["generic", "generic"]


def test_hackernews_adapter_ingests_all_top_level_comments(monkeypatch) -> None:
    story = SimpleNamespace(
        id=777,
        created_at="2026-07-01T00:00:00Z",
        title="Ask HN: Who is hiring?",
        url="https://news.ycombinator.com/item?id=777",
    )

    async def fake_search_stories(_session, **_kwargs):
        return [story]

    async def fake_fetch_item(_session, item_id):
        if item_id == 777:
            return {"id": 777, "type": "story", "kids": [1, 2, 3, 4]}
        if item_id == 1:
            return {
                "id": 1,
                "type": "comment",
                "by": "alice",
                "text": "Remote AI engineer building RAG systems in Java. Apply at https://example.com/jobs/1",
                "time": 1760000000,
            }
        if item_id == 2:
            return {
                "id": 2,
                "type": "comment",
                "by": "bob",
                "text": "This is a long but weakly structured hiring note with no external apply link and no pipe header.",
                "time": 1760000001,
            }
        if item_id == 3:
            return {
                "id": 3,
                "type": "comment",
                "by": "ghost",
                "text": "deleted comment that should never be ingested",
                "time": 1760000002,
                "deleted": True,
            }
        if item_id == 4:
            return {
                "id": 4,
                "type": "comment",
                "by": "tiny",
                "text": "too short",
                "time": 1760000003,
            }
        raise AssertionError(f"Unexpected item id: {item_id}")

    async def fake_upgrade(comment_text, apply_url, parsed, *, engine):
        assert engine == "local"
        assert comment_text
        assert apply_url
        return parsed, {"parser_engine": "local", "parser_confidence": "test", "parser_reason": "forced keep"}, False

    monkeypatch.setattr(core.hn_topic, "search_stories", fake_search_stories)
    monkeypatch.setattr(core.hn_topic, "fetch_item", fake_fetch_item)
    monkeypatch.setattr(core.hn_topic, "top_level_comment_ids", lambda _story_item: [1, 2, 3, 4])
    monkeypatch.setattr(core, "maybe_upgrade_hackernews_parse", fake_upgrade)

    jobs = asyncio.run(
        core.fetch_hackernews_hiring(
            None,
            {
                "company": "Hacker News Who Is Hiring",
                "ats": "hackernews_hiring",
                "token": "who is hiring",
                "_hn_parser_engine": "local",
            },
        )
    )

    assert [job["source_job_id"] for job in jobs] == ["1", "2"]
    assert jobs[0]["apply_url"] == "https://example.com/jobs/1"
    assert jobs[1]["apply_url"] == "https://news.ycombinator.com/item?id=2"
    assert jobs[1]["raw"]["parser_reason"] == "forced keep"


def test_filter_and_match_detects_new_ai_interest_terms() -> None:
    match = core.filter_and_match(
        (
            "Remote AI engineer and forward deployed engineer role building "
            "retrieval-augmented generation systems in Java on Linux."
        ),
        core.ScrapeOptions(),
        location="Remote",
    )

    assert match["passes_filter"] is True
    assert set(match["interest_tags"]) >= {
        "AI",
        "AI Engineer",
        "Forward Deployed Engineer",
        "Java",
        "Linux",
        "RAG",
    }


def test_detect_stack_maps_founder_variants_to_founding_engineer() -> None:
    stack = core.detect_stack(
        "Remote foundation engineer and co-founder role for the first engineer on an AI startup."
    )

    assert "AI" in stack["domains"]
    assert "Founding Engineer" in stack["groups"]


def test_detect_stack_keeps_systems_matching_conservative() -> None:
    matched = core.detect_stack("Low-level systems programming on Linux kernel networking code.")
    not_matched = core.detect_stack("Own internal system design reviews for the finance team.")

    assert "Systems" in matched["domains"]
    assert "Kernel" in matched["domains"]
    assert "Systems" not in not_matched["domains"]


def test_scrape_failure_updates_source_health(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "jobs.sqlite"
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(
        json.dumps(
            [
                {
                    "company": "Failing Source",
                    "ats": "remotive_api",
                    "url": "https://example.test/jobs",
                    "entry_url": "https://example.test/jobs",
                    "enabled": True,
                    "portal": "remotive",
                    "entry_kind": "public_api",
                    "auth_mode": "public",
                }
            ]
        ),
        encoding="utf-8",
    )
    db.import_sources_report(db_path, sources_path, create_backup=False)

    async def fail_adapter(_session, _source):
        raise RuntimeError("simulated network failure")

    monkeypatch.setitem(core.ADAPTERS, "remotive_api", fail_adapter)
    lines: list[str] = []

    results = core.scrape_all(
        db_path=db_path,
        sources_path=sources_path,
        options=core.ScrapeOptions(concurrency=1, http_concurrency=1),
        progress=lines.append,
    )

    source = db.list_sources(db_path)[0]
    assert len(results) == 1
    assert results[0].error
    assert source["last_status"] == "error"
    assert source["failure_count"] == 1
    assert "ERROR Failing Source" in "\n".join(lines)
