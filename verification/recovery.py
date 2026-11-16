"""Check recovery with an item write and read, plus Prometheus availability."""

from time import monotonic, sleep, time

from analysis.incident_detector import query
from chaos.run_catalog_demo import api


def recovery_verdict(write_status: int, read_status: int, matches: bool,
                     readiness_status: int, metrics_available: bool) -> str:
    if write_status != 201 or read_status != 200 or not matches or readiness_status != 200:
        return "still degraded"
    return "recovered" if metrics_available else "insufficient evidence"


def verify_recovery() -> dict:
    try:
        write_status, item = api("/items", "POST", {"name": "Verify Ops recovery probe"})
        if write_status != 201:
            return {"verdict": "still degraded", "write_status": write_status}
        read_status, returned = api(f"/items/{item['id']}")
        readiness_status, _ = api("/ready")
    except (OSError, ValueError, KeyError) as error:
        return {"verdict": "insufficient evidence", "probe_error": type(error).__name__}
    statuses = {"write_status": write_status, "read_status": read_status, "readiness_status": readiness_status}
    verdict = recovery_verdict(write_status, read_status, item == returned, readiness_status, False)
    if verdict == "still degraded":
        return {"verdict": verdict, **statuses}
    probe_completed = time()
    deadline = monotonic() + 15
    metrics_error = "No fresh scrape after the item probe"
    while monotonic() < deadline:
        try:
            stamps = query('timestamp(up{job="verify-ops"} == 1)')
            if len(stamps) == 1 and stamps[0] >= probe_completed:
                return {"verdict": "recovered", **statuses}
        except (OSError, ValueError, RuntimeError) as error:
            metrics_error = type(error).__name__
        sleep(1)
    return {"verdict": verdict, **statuses, "metrics_error": metrics_error}
