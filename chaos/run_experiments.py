"""Capture healthy and faulted Prometheus samples and evaluate one scenario."""

import argparse
import json
import subprocess
from pathlib import Path
from time import monotonic, sleep, time
from uuid import uuid4

from analysis.incident_detector import prometheus_request
from analysis.statistical_detection import rank_signals
from chaos.run_catalog_demo import expect


QUERIES = {
    "traffic": 'sum(rate(http_request_total{job="verify-ops",endpoint="/items/{item_id}"}[20s]))',
    "latency": 'histogram_quantile(0.95,sum by(le)(rate(http_request_duration_seconds_bucket{job="verify-ops",endpoint="/items/{item_id}"}[20s])))',
    "http_errors": 'sum(rate(http_request_total{job="verify-ops",http_status=~"5.."}[20s])) or vector(0)',
    "redis_latency": 'sum(rate(redis_request_duration_seconds_sum{job="verify-ops"}[20s])) / sum(rate(redis_request_duration_seconds_count{job="verify-ops"}[20s]))',
    "redis_errors": 'sum(rate(cache_request_total{job="verify-ops",outcome="error"}[20s])) or vector(0)',
    "catalog_latency": 'sum(rate(downstream_call_duration_seconds_sum{job="verify-ops"}[20s])) / sum(rate(downstream_call_duration_seconds_count{job="verify-ops"}[20s]))',
    "catalog_errors": 'sum(rate(downstream_calls_total{job="verify-ops",outcome="error"}[20s])) or vector(0)',
    "cpu": 'sum(rate(process_cpu_seconds_total{job="verify-ops"}[20s]))',
    "memory": 'sum(process_resident_memory_bytes{job="verify-ops"})',
    "availability": 'min(up{job="verify-ops"})',
}
HTTP_FAULTS = {
    "redis-latency": ("/chaos/redis-delay", {"delay_ms": 400}, {}),
    "catalog-latency": ("/chaos/catalog", {"delay_ms": 500}, {}),
    "catalog-failure": ("/chaos/catalog", {"failure_rate": 1}, {}),
    "cpu-spike": ("/chaos/cpu", {"seconds": 35}, {}),
    "memory-growth": ("/chaos/memory", {}, {"megabytes": 0}),
}
STOP_FAULTS = {"redis-outage": "redis", "app-outage": "api"}
EXPECTED = {
    "redis-latency": "redis_latency", "redis-outage": "redis_errors",
    "catalog-latency": "catalog_latency", "catalog-failure": "catalog_errors",
    "app-outage": "availability", "cpu-spike": "cpu", "memory-growth": "memory",
}


def capture(start: int, end: int) -> dict:
    measurements = {}
    for name, expression in QUERIES.items():
        result = prometheus_request("query_range", query=expression, start=start, end=end, step=5)
        samples = {int(stamp): value for stamp, value in result[0]["values"]} if result else {}
        measurements[name] = [samples.get(stamp) for stamp in range(start, end + 1, 5)]
    return measurements


def compose(action: str, service: str) -> None:
    subprocess.run(["docker", "compose", action, service], check=True, timeout=30)


def hold_fault(fault: str) -> None:
    service = STOP_FAULTS.get(fault)
    if service:
        try:
            compose("stop", service)
            sleep(30)
        finally:
            compose("start", service)
        return
    path, settings, reset = HTTP_FAULTS[fault]
    try:
        if fault == "memory-growth":
            for size in range(8, 49, 8):
                expect(path, 200, "PUT", {"megabytes": size})
                sleep(5)
        else:
            expect(path, 200, "PUT", settings)
            sleep(30)
    finally:
        expect(path, 200, "PUT", reset)


def wait_for_ready() -> None:
    deadline = monotonic() + 30
    while True:
        try:
            expect("/ready", 200)
            return
        except (OSError, RuntimeError) as error:
            if monotonic() >= deadline:
                raise RuntimeError(f"Verify Ops did not become ready within 30 seconds: {error}") from error
            sleep(1)


def run_experiment(fault: str) -> bool:
    wait_for_ready()
    started = int(time())
    print("Recording a 40-second healthy baseline. Keep Locust running.", flush=True)
    sleep(40)
    baseline_end = int(time())
    baseline = capture(started, baseline_end)
    if sum(value is not None and float(value) > 0 for value in baseline["traffic"]) < 6:
        raise RuntimeError("Baseline needs at least six item traffic samples. Start the traffic profile and retry.")
    injected_at = time()
    print(f"Injecting {fault} for about 30 seconds.", flush=True)
    hold_fault(fault)
    cleared_at = time()
    wait_for_ready()
    sleep(5)
    samples = capture(started, int(cleared_at))
    series = {name: [float(value) if value is not None else float("nan") for value in values]
              for name, values in samples.items()}
    result = rank_signals(series)
    candidates = result["candidates"]
    ranked = candidates[0]["metric"] if candidates else None
    alert_times = [started + (candidate["onset_sample"] + 2) * 5 for candidate in candidates]
    report = {
        "fault": fault, "expected_metric": EXPECTED[fault],
        "baseline_start": started, "injected_at": injected_at, "cleared_at": cleared_at,
        "step_seconds": 5, "samples": samples, **result,
        "detection_delay_seconds": round(min(alert_times) - injected_at, 2) if alert_times else None,
    }
    output = Path("evaluation") / f"{fault}-{uuid4().hex[:8]}.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Expected: {EXPECTED[fault]}. Earliest signal: {result['earliest_signal']}. "
          f"Top-ranked: {ranked or 'none'}. Report: {output}", flush=True)
    return ranked == EXPECTED[fault]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fault", choices=["all", *sorted(EXPECTED)])
    args = parser.parse_args()
    if args.fault != "all":
        run_experiment(args.fault)
        return
    correct = 0
    for fault in EXPECTED:
        correct += run_experiment(fault)
        sleep(20)  # Let the Prometheus rate windows clear before the next baseline.
    print(f"Top-ranked cause matched {correct}/{len(EXPECTED)} injected faults.")


if __name__ == "__main__":
    main()
