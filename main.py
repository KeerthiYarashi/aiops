import asyncio
import json
import logging
import os
import re
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from random import random
from threading import Event, Lock, Thread
from time import monotonic, perf_counter, sleep
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, Field, field_validator
from redis import Redis
from redis.exceptions import RedisError

from analysis.incident_detector import LiveDetector

detector = LiveDetector()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(detector.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Verify Ops Demo Service", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def operator_dashboard():
    return FileResponse(Path(__file__).parent / "dashboard" / "index.html")


@app.get("/incidents")
async def list_incidents():
    return {"detector_status": detector.status, "incidents": list(detector.incidents)}


@app.get("/evaluations")
def list_evaluations():
    directory = Path(os.getenv("VERIFY_OPS_EVALUATION_DIR", "evaluation"))
    results = []
    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "expected_metric" not in report:
            continue
        candidates = report["candidates"]
        actions_path = path.with_name(f"{path.stem}-recovery.jsonl")
        actions = []
        try:
            lines = actions_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                actions.append(json.loads(line))
            except ValueError:
                continue
        results.append({
            "report": path.name, "fault": report["fault"],
            "expected": report["expected_metric"], "earliest": report["earliest_signal"],
            "top_ranked": candidates[0]["metric"] if candidates else None,
            "actions": actions,
        })
    return {"evaluations": results}


logger = logging.getLogger("verify_ops")
logger.setLevel(logging.INFO)
log_file = os.getenv("LOG_FILE")
if log_file:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger.addHandler(logging.FileHandler(log_path, encoding="utf-8"))
    logger.propagate = False
request_count = Counter("http_request_total", "HTTP responses", ["method", "endpoint", "http_status"])
request_latency = Histogram("http_request_duration_seconds", "HTTP latency", ["method", "endpoint"])
redis_latency = Histogram("redis_request_duration_seconds", "Redis latency", ["operation"])
cache_count = Counter("cache_request_total", "Redis cache outcomes", ["outcome"])
catalog_latency = Histogram("downstream_call_duration_seconds", "Downstream latency", ["dependency"])
catalog_count = Counter("downstream_calls_total", "Downstream call outcomes", ["dependency", "outcome"])
redis_client = Redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    socket_connect_timeout=1,
    socket_timeout=1,
)


app.mount("/metrics", make_asgi_app())


def log_event(event: str, **details) -> None:
    logger.info(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }))


@app.middleware("http")
async def observe_request(request: Request, call_next):
    if request.url.path.rstrip("/") == "/metrics":
        return await call_next(request)
    start = perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        endpoint = getattr(request.scope.get("route"), "path", "unmatched")
        seconds = perf_counter() - start
        request_count.labels(request.method, endpoint, str(status)).inc()
        request_latency.labels(request.method, endpoint).observe(seconds)
        log_event("http_request", method=request.method, endpoint=endpoint,
                  status=status, duration_ms=round(seconds * 1000, 2))


class ItemCreate(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("Item name must not be blank")
        return name


class Item(ItemCreate):
    id: str


items: dict[str, Item] = {}


class CatalogFault(BaseModel):
    delay_ms: int = Field(default=0, ge=0, le=5000)
    failure_rate: float = Field(default=0, ge=0, le=1)


catalog_fault = CatalogFault()
redis_delay_ms = 0
memory_blocks: deque[bytearray] = deque()
memory_lock = Lock()
cpu_stop = Event()
cpu_lock = Lock()
cpu_worker: Thread | None = None


class RedisDelay(BaseModel):
    delay_ms: int = Field(default=0, ge=0, le=5000)


class MemoryGrowth(BaseModel):
    megabytes: int = Field(ge=0, le=64)


class CpuLoad(BaseModel):
    seconds: int = Field(default=0, ge=0, le=120)


@app.put("/chaos/redis-delay")
def configure_redis_delay(settings: RedisDelay) -> RedisDelay:
    global redis_delay_ms
    redis_delay_ms = settings.delay_ms
    return settings


@app.put("/chaos/memory")
def configure_memory(settings: MemoryGrowth) -> dict:
    with memory_lock:
        while len(memory_blocks) > settings.megabytes:
            memory_blocks.pop()
        while len(memory_blocks) < settings.megabytes:
            memory_blocks.append(bytearray(b"x" * (1024 * 1024)))
    return {"megabytes": settings.megabytes}


def burn_cpu(seconds: int) -> None:
    deadline = monotonic() + seconds
    while monotonic() < deadline and not cpu_stop.is_set():
        pass


@app.put("/chaos/cpu")
def configure_cpu(settings: CpuLoad) -> CpuLoad:
    global cpu_worker
    with cpu_lock:
        cpu_stop.set()
        if cpu_worker is not None:
            cpu_worker.join()
        if settings.seconds:
            cpu_stop.clear()
            cpu_worker = Thread(target=burn_cpu, args=(settings.seconds,), daemon=True)
            cpu_worker.start()
    return settings


@app.put("/chaos/catalog")
def configure_catalog_fault(settings: CatalogFault) -> CatalogFault:
    global catalog_fault
    catalog_fault = settings
    return settings


def check_catalog() -> None:
    settings = catalog_fault
    with catalog_latency.labels("catalog-service").time():
        if settings.failure_rate > 0 and random() < settings.failure_rate:
            catalog_count.labels("catalog-service", "error").inc()
            log_event("catalog_call", outcome="error")
            raise HTTPException(status_code=503, detail="Catalog service unavailable")
        sleep((5 + settings.delay_ms) / 1000)
    catalog_count.labels("catalog-service", "success").inc()
    log_event("catalog_call", outcome="success")


def cache_item(item: Item) -> None:
    try:
        with redis_latency.labels("set").time():
            sleep(redis_delay_ms / 1000)
            redis_client.set(f"item:{item.id}", item.model_dump_json())
    except RedisError:
        cache_count.labels("error").inc()
        log_event("redis_cache", operation="set", outcome="error", item_id=item.id)


@app.post("/items", status_code=201)
def create_item(request: ItemCreate) -> Item:
    item = Item(id=uuid4().hex, name=request.name)
    items[item.id] = item
    cache_item(item)
    return item


@app.get(
    "/items/{item_id}",
    response_model=Item,
    responses={
        304: {"description": "Not Modified"},
        404: {"description": "Item not found"},
        503: {"description": "Catalog service unavailable"},
    },
)
def get_item(
    item_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None),
) -> Item | Response:
    try:
        with redis_latency.labels("get").time():
            sleep(redis_delay_ms / 1000)
            stored_item = redis_client.get(f"item:{item_id}")
    except RedisError:
        cache_count.labels("error").inc()
        log_event("redis_cache", operation="get", outcome="error", item_id=item_id)
        stored_item = None
    else:
        outcome = "miss" if stored_item is None else "hit"
        cache_count.labels(outcome).inc()
        log_event("redis_cache", operation="get", outcome=outcome, item_id=item_id)
    if stored_item is None:
        item = items.get(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")
        cache_item(item)
    else:
        item = Item.model_validate_json(stored_item)
    check_catalog()
    version = f'"{sha256(item.model_dump_json().encode()).hexdigest()}"'
    etag = f"W/{version}"
    if if_none_match is not None:
        cached_versions = re.findall(r'"[^"]*"', if_none_match)
        if if_none_match.strip() == "*" or version in cached_versions:
            return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return item


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    try:
        redis_client.ping()
    except RedisError as error:
        raise HTTPException(status_code=503, detail="Redis unavailable") from error
    return {"status": "ready"}
