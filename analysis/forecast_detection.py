"""Experimental detector using residuals from a one-step forecast."""

from math import isfinite
from statistics import median

from analysis.statistical_detection import AVAILABILITY, MEMORY, MIN_SCALE, detect


def detect_forecast(series: list[float], metric: str) -> int | None:
    if metric in {AVAILABILITY, MEMORY}:
        return detect(series, metric)

    forecast = None
    residuals: list[float] = []
    streak = 0
    for index, value in enumerate(series):
        if not isfinite(value):
            streak = 0
            continue
        if forecast is None:
            forecast = value
            continue
        error = value - forecast
        anomalous = False
        if len(residuals) >= 6:
            center = median(residuals)
            spread = median(abs(sample - center) for sample in residuals)
            scale = max(spread / 0.6745, MIN_SCALE[metric])
            anomalous = abs(error - center) > 3.5 * scale
        if anomalous:
            streak += 1
            if streak == 3:
                return index - 2
        else:
            streak = 0
            forecast = 0.3 * value + 0.7 * forecast
            residuals = (residuals + [error])[-12:]
    return None
