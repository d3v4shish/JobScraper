#!/usr/bin/env python3
"""
Local topic roadmap generation for selected job sets.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Sequence


TOPIC_GUIDE: Dict[str, Dict[str, List[str]]] = {
    "Linux": {
        "basics": ["processes, threads, files, signals, virtual memory", "shell tooling, permissions, systemd basics"],
        "intermediate": ["syscalls, page cache, cgroups, namespaces, epoll/io_uring"],
        "advanced": ["scheduler behavior, NUMA, perf/ftrace/eBPF, kernel networking path"],
        "expert": ["debug production latency from kernel counters and traces", "design isolation for noisy multi-tenant workloads"],
        "champion": ["set Linux performance/debugging standards across teams"],
    },
    "Kernel": {
        "basics": ["kernel/user mode, interrupts, syscalls, memory protection"],
        "intermediate": ["locking, wait queues, file descriptors, device model"],
        "advanced": ["RCU, lock contention, allocator behavior, scheduler tradeoffs"],
        "expert": ["root-cause kernel regressions with traces and minimal repros"],
        "champion": ["own safe kernel-facing architecture and rollback strategy"],
    },
    "Networking": {
        "basics": ["TCP/IP, DNS, TLS, HTTP, routing, load balancing"],
        "intermediate": ["timeouts, retries, backpressure, proxies, firewalls, BGP/VPN concepts"],
        "advanced": ["tail latency, packet loss, congestion control, zero-copy IO, kernel bypass basics"],
        "expert": ["debug cross-region production incidents from packet to application traces"],
        "champion": ["define network reliability and observability standards"],
    },
    "Backend": {
        "basics": ["APIs, request lifecycle, auth, persistence, queues"],
        "intermediate": ["idempotency, rate limits, retries, transactions, cache invalidation"],
        "advanced": ["multi-tenant scaling, consistency boundaries, schema evolution, async workflows"],
        "expert": ["design resilient services under partial failure and traffic spikes"],
        "champion": ["set API, reliability, and operability architecture across services"],
    },
    "Distributed Systems": {
        "basics": ["latency, availability, replication, partitioning, consensus vocabulary"],
        "intermediate": ["leader election, quorum reads/writes, leases, clocks, retries"],
        "advanced": ["CAP/PACELC tradeoffs, split brain, exactly-once illusions, conflict resolution"],
        "expert": ["prove failure behavior with chaos tests and formal invariants"],
        "champion": ["choose consistency models and migration plans for company-scale systems"],
    },
    "Infrastructure": {
        "basics": ["compute, storage, networking, deploys, secrets, config"],
        "intermediate": ["containers, CI/CD, autoscaling, capacity, incident response"],
        "advanced": ["multi-region reliability, cost controls, blast-radius reduction"],
        "expert": ["design operable platforms with SLOs, golden paths, and guardrails"],
        "champion": ["drive platform strategy and developer experience standards"],
    },
    "Storage": {
        "basics": ["files, blocks, objects, indexes, durability, replication"],
        "intermediate": ["LSM/B-tree tradeoffs, compaction, caching, checksums, WAL"],
        "advanced": ["distributed storage consistency, hot partitions, recovery, erasure coding"],
        "expert": ["debug data loss/corruption risk and performance under failure"],
        "champion": ["own storage architecture decisions and long-term evolution"],
    },
    "Kubernetes": {
        "basics": ["pods, deployments, services, ingress, configmaps, secrets"],
        "intermediate": ["scheduling, probes, resource requests/limits, networking, storage classes"],
        "advanced": ["operators, admission control, autoscaling, multi-cluster patterns"],
        "expert": ["debug production cluster failures from control plane to workload"],
        "champion": ["set platform policy and cluster reliability standards"],
    },
    "Security": {
        "basics": ["authn/authz, secrets, TLS, least privilege, threat modeling"],
        "intermediate": ["supply chain, dependency risk, sandboxing, audit trails"],
        "advanced": ["zero trust, isolation, vulnerability response, secure-by-default APIs"],
        "expert": ["design controls that survive compromised dependencies and operators"],
        "champion": ["define security architecture and review standards"],
    },
    "Go": {
        "basics": ["types, interfaces, errors, packages, testing"],
        "intermediate": ["goroutines, channels, context cancellation, HTTP services"],
        "advanced": ["scheduler, GC, pprof, memory layout, sync/atomic, race detector"],
        "expert": ["debug leaks, tail latency, and contention in production Go services"],
        "champion": ["set Go service architecture and performance review standards"],
    },
    "Python": {
        "basics": ["data model, exceptions, typing, packaging, tests"],
        "intermediate": ["asyncio, multiprocessing, FastAPI/Django patterns, SQL access"],
        "advanced": ["GIL implications, profiling, C extensions, memory/perf tuning"],
        "expert": ["design robust Python services around IO, isolation, and observability"],
        "champion": ["define Python architecture, packaging, and reliability conventions"],
    },
    "Rust": {
        "basics": ["ownership, borrowing, lifetimes, enums, traits"],
        "intermediate": ["error handling, async Rust, channels, crates, testing"],
        "advanced": ["unsafe boundaries, pinning, Send/Sync, zero-copy, FFI"],
        "expert": ["design safe high-performance systems with explicit invariants"],
        "champion": ["own Rust safety/performance standards and API design"],
    },
    "C++": {
        "basics": ["RAII, value/reference semantics, STL, compilation model"],
        "intermediate": ["move semantics, templates, memory ownership, concurrency"],
        "advanced": ["lock-free patterns, cache locality, allocators, ABI, sanitizers"],
        "expert": ["debug low-latency production behavior from CPU/cache to code path"],
        "champion": ["set C++ performance, safety, and review standards"],
    },
}

ALIASES = {
    "Postgres": "Storage",
    "MySQL": "Storage",
    "Redis": "Storage",
    "Kafka": "Distributed Systems",
    "Docker": "Infrastructure",
    "Terraform": "Infrastructure",
    "AWS": "Infrastructure",
    "GCP": "Infrastructure",
    "Azure": "Infrastructure",
    "eBPF": "Linux",
    "TCP/IP": "Networking",
    "DNS": "Networking",
    "VPN": "Networking",
    "WireGuard": "Networking",
    "BGP": "Networking",
    "Firewall": "Security",
    "Proxy": "Networking",
}


def _split_stack(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _job_topics(job: Dict[str, Any]) -> List[str]:
    topics: List[str] = []
    for item in _split_stack(job.get("detected_stack")) + _split_stack(job.get("interest_tags")):
        canonical = ALIASES.get(item, item)
        if canonical in TOPIC_GUIDE and canonical not in topics:
            topics.append(canonical)
    text = " ".join(
        str(job.get(key) or "")
        for key in ("title", "department", "location", "text")
    ).lower()
    keyword_topics = {
        "Storage": ("storage", "database", "postgres", "object store", "filesystem", "file system"),
        "Security": ("security", "auth", "zero trust", "supply chain"),
        "Distributed Systems": ("distributed", "replication", "consensus", "kafka", "streaming"),
        "Networking": ("networking", "tcp", "dns", "proxy", "firewall", "vpn", "bgp"),
        "Linux": ("linux", "kernel", "ebpf", "system call", "syscall"),
        "Backend": ("backend", "api", "microservice", "service"),
    }
    for topic, needles in keyword_topics.items():
        if topic not in topics and any(needle in text for needle in needles):
            topics.append(topic)
    return topics


def _top(counter: Counter[str], limit: int = 12) -> List[Dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counter.most_common(limit)]


def generate_topic_roadmap(
    jobs: Sequence[Dict[str, Any]],
    *,
    scope: str,
    selected_companies: Sequence[str] | None = None,
) -> Dict[str, Any]:
    topic_counter: Counter[str] = Counter()
    company_counter: Counter[str] = Counter()
    company_topics: Dict[str, Counter[str]] = defaultdict(Counter)
    language_counter: Counter[str] = Counter()
    tool_counter: Counter[str] = Counter()

    language_names = {"Go", "Python", "Rust", "C++", "Java", "JavaScript", "TypeScript", "C#", "Kotlin", "Ruby", "Swift"}
    for job in jobs:
        company = str(job.get("company") or "Unknown").strip() or "Unknown"
        company_counter[company] += 1
        stack_items = _split_stack(job.get("detected_stack"))
        for item in stack_items:
            if item in language_names:
                language_counter[item] += 1
            elif item not in TOPIC_GUIDE:
                tool_counter[item] += 1
        for topic in _job_topics(job):
            topic_counter[topic] += 1
            company_topics[company][topic] += 1

    if not topic_counter:
        for fallback in ("Backend", "Distributed Systems", "Infrastructure", "Go"):
            topic_counter[fallback] += 1

    levels = {level: [] for level in ("basics", "intermediate", "advanced", "expert", "champion")}
    for topic, _count in topic_counter.most_common(10):
        guide = TOPIC_GUIDE.get(topic, TOPIC_GUIDE["Backend"])
        for level in levels:
            for item in guide.get(level, []):
                levels[level].append({"topic": topic, "item": item})

    checklist: List[Dict[str, str]] = []
    level_names = {
        "basics": "Basics",
        "intermediate": "Intermediate",
        "advanced": "Advanced",
        "expert": "Expert",
        "champion": "Domain Champion",
    }
    for level, items in levels.items():
        for item in items[:10]:
            checklist.append(
                {
                    "level": level_names[level],
                    "topic": item["topic"],
                    "task": f"Explain and apply: {item['item']}",
                }
            )

    return {
        "scope": scope,
        "job_count": len(jobs),
        "selected_companies": sorted(str(company) for company in (selected_companies or []) if str(company).strip()),
        "overview": {
            "topics": _top(topic_counter),
            "languages": _top(language_counter),
            "tools": _top(tool_counter),
            "companies": _top(company_counter),
        },
        "levels": levels,
        "by_company": [
            {
                "company": company,
                "job_count": company_counter[company],
                "topics": _top(counter, 8),
            }
            for company, counter in sorted(company_topics.items(), key=lambda item: (-sum(item[1].values()), item[0].lower()))
        ],
        "checklist": checklist,
        "signals": {
            "dominant_topics": [item["name"] for item in _top(topic_counter, 8)],
            "dominant_languages": [item["name"] for item in _top(language_counter, 8)],
            "dominant_tools": [item["name"] for item in _top(tool_counter, 8)],
        },
    }
