"""Run one local catalog incident from baseline through recovery."""

import json
import os
from time import monotonic, sleep
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from analysis.incident_detector import rank_new_errors, snapshot


def api(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode()
    port = os.getenv("VERIFY_OPS_PORT", "8002") or "8002"
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def expect(path: str, status: int, method: str = "GET", payload: dict | None = None) -> dict:
    actual, body = api(path, method, payload)
    if actual != status:
        raise RuntimeError(f"{method} {path}: expected HTTP {status}, got {actual}: {body}")
    return body


def main() -> None:
    expect("/chaos/catalog", 200, "PUT", {})
    item = expect("/items", 201, "POST", {"name": "Catalog incident demo"})
    path = f"/items/{item['id']}"
    expect(path, 200)
    before = snapshot()
    print("Baseline: item GET 200, Prometheus target UP.", flush=True)

    try:
        expect("/chaos/catalog", 200, "PUT", {"failure_rate": 1})
        for _ in range(3):
            expect(path, 503)
        print("Injected catalog failure: three item requests returned 503.", flush=True)

        deadline = monotonic() + 20
        while True:
            candidates = rank_new_errors(before, snapshot())
            if candidates:
                break
            if monotonic() >= deadline:
                raise RuntimeError("Prometheus did not show a new dependency error")
            sleep(1)
        if candidates[0][0] != "catalog-service":
            raise RuntimeError(f"Expected catalog-service as top candidate, got {candidates}")
        print(f"Incident detected from Prometheus: {candidates}", flush=True)
    finally:
        expect("/chaos/catalog", 200, "PUT", {})

    if expect(path, 200) != item:
        raise RuntimeError("The recovery read did not match the original item")
    print("Recovery: item GET 200 and returned the original item.", flush=True)


if __name__ == "__main__":
    main()
