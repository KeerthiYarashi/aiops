"""Detect sustained anomalies and rank their likely sources."""

from math import isfinite
from statistics import StatisticsError, correlation, median


MEMORY = "memory"
AVAILABILITY = "availability"
DEPENDENCIES = {
    "redis_latency": {"latency", "traffic"},
    "redis_errors": {"redis_latency", "latency", "traffic"},
    "catalog_latency": {"latency", "traffic"},
    "catalog_errors": {"catalog_latency", "http_errors", "traffic"},
    "cpu": {"latency", "traffic"},
    MEMORY: {"latency", "http_errors", "traffic"},
}
MIN_SCALE = {
    "traffic": 0.3, "latency": 0.004, "http_errors": 0.05,
    "redis_latency": 0.002, "redis_errors": 0.05,
    "catalog_latency": 0.002, "catalog_errors": 0.05,
    "cpu": 0.02, MEMORY: 1024 * 1024,
}


def detect(series: list[float], metric: str) -> int | None:
    """Return the onset of the first run of three anomalous samples."""
    reference: list[float] = []
    streak = 0
    cumulative = 0.0
    for index, value in enumerate(series):
        if not isfinite(value):
            streak = 0
            cumulative = 0
            continue
        if metric == AVAILABILITY:
            anomalous = value == 0
        elif len(reference) >= 6:
            center = median(reference)
            mad = median(abs(sample - center) for sample in reference)
            scale = max(mad / 0.6745, MIN_SCALE[metric])
            deviation = (value - center) / scale
            if metric == MEMORY:
                cumulative = max(0, cumulative + deviation - 0.5)
                anomalous = cumulative > 6
            else:
                anomalous = abs(deviation) > 3.5
        else:
            anomalous = False
        streak = streak + 1 if anomalous else 0
        if streak == 3:
            return index - 2
        if not anomalous:
            reference = (reference + [value])[-12:]
    return None


def alignment(source: list[float], symptom: list[float]) -> float:
    best = 0.0
    for lag in range(5):
        pairs = [(a, b) for a, b in zip(source, symptom[lag:]) if isfinite(a) and isfinite(b)]
        if len(pairs) >= 4:
            try:
                best = max(best, correlation(*zip(*pairs)))
            except StatisticsError:
                continue
    return best


def rank_signals(series: dict[str, list[float]]) -> dict:
    onsets = {}
    for name, values in series.items():
        onset = detect(values, name)
        if onset is not None:
            onsets[name] = onset
    if not onsets:
        return {"earliest_signal": None, "candidates": []}
    earliest = min(onsets, key=onsets.get)
    if AVAILABILITY in onsets:
        return {"earliest_signal": earliest, "candidates": [
            {"metric": AVAILABILITY, "onset_sample": onsets[AVAILABILITY], "score": 1.0},
        ]}
    first, last = min(onsets.values()), max(onsets.values())
    candidates = []
    for name, onset in onsets.items():
        others = onsets.keys() - {name}
        explains = others & DEPENDENCIES.get(name, set())
        prior = len(explains) / max(1, len(others))
        source = series[name]
        if name == MEMORY:
            source = [float("nan")] + [b - a for a, b in zip(source, source[1:])]
        lag_score = max((alignment(source, series[symptom]) for symptom in explains), default=0.0)
        timing = 1 - (onset - first) / max(1, last - first)
        score = 1.0 if not others else 0.5 * prior + 0.35 * lag_score + 0.15 * timing
        candidates.append({"metric": name, "onset_sample": onset, "score": round(score, 3),
                           "dependency_prior": prior, "lag_correlation": lag_score, "timing": timing})
    return {"earliest_signal": earliest, "candidates": sorted(
        candidates, key=lambda candidate: (-candidate["score"], candidate["onset_sample"])
    )}
