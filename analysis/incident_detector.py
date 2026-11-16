"""Read dependency counters from Prometheus and rank new errors."""

import asyncio
import json
import os
from collections import deque
from datetime import datetime, timezone
from time import time
from urllib.parse import urlencode
from urllib.request import urlopen


SIGNALS = {
    "catalog-service": 'downstream_calls_total{job="verify-ops",outcome="error"}',
    "redis": 'cache_request_total{job="verify-ops",outcome="error"}',
}


def query(expression: str) -> list[float]:
    results = prometheus_request("query", query=expression)
    return [float(row["value"][1]) for row in results]


def prometheus_request(endpoint: str, **parameters) -> list[dict]:
    params = urlencode(parameters)
    base_url = os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/")
    with urlopen(f"{base_url}/api/v1/{endpoint}?{params}", timeout=5) as response:
        result = json.load(response)
    if result["status"] != "success":
        raise RuntimeError("Prometheus query failed")
    return result["data"]["result"]


def snapshot() -> dict[str, float]:
    if query('up{job="verify-ops"}') != [1.0]:
        raise RuntimeError("Prometheus cannot scrape Verify Ops")
    return {name: sum(query(expression)) for name, expression in SIGNALS.items()}


def rank_new_errors(before: dict[str, float], after: dict[str, float]) -> list[tuple[str, float]]:
    if any(after[name] < count for name, count in before.items()):
        raise RuntimeError("A counter reset during the experiment. Retry with a fresh baseline.")
    candidates = [(name, after[name] - count) for name, count in before.items() if after[name] > count]
    return sorted(candidates, key=lambda candidate: candidate[1], reverse=True)


class LiveDetector:
    def __init__(self) -> None:
        self.incidents = deque(maxlen=100)
        self.status = "starting"

    async def run(self) -> None:
        previous = None
        started_at = time()
        while True:
            try:
                if previous is None:
                    scrapes = await asyncio.to_thread(query, 'timestamp(up{job="verify-ops"})')
                    if not scrapes or scrapes[0] < started_at:
                        self.status = "waiting_for_baseline"
                        await asyncio.sleep(5)
                        continue
                current = await asyncio.to_thread(snapshot)
                if previous is not None:
                    candidates = rank_new_errors(previous, current)
                    if candidates:
                        self.incidents.appendleft({
                            "detected_at": datetime.now(timezone.utc).isoformat(),
                            "candidates": [
                                {"dependency": name, "new_errors": count}
                                for name, count in candidates
                            ],
                        })
                previous = current
                self.status = "watching"
            except (OSError, ValueError, RuntimeError):
                previous = None
                self.status = "waiting_for_baseline"
            await asyncio.sleep(5)
