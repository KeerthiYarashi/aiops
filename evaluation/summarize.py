"""Compare detected root causes across saved fault experiments."""

import json
from pathlib import Path

from analysis.forecast_detection import detect_forecast
from analysis.statistical_detection import detect, rank_signals


def main() -> None:
    reports = []
    for path in sorted(Path("evaluation").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if "expected_metric" in report:
            reports.append(report)
    detected_runs = expected_signal_detected = earliest_correct = ranked_correct = 0
    forecast_detected = forecast_expected = 0
    healthy_alerts = {"rolling_mad": 0, "forecast_residual": 0}
    for report in reports:
        expected = report["expected_metric"]
        series = {
            name: [float(value) if value is not None else float("nan") for value in samples]
            for name, samples in report["samples"].items()
        }
        replay = rank_signals(series)
        earliest = replay["earliest_signal"]
        candidates = replay["candidates"]
        top = candidates[0]["metric"] if candidates else None
        detected_runs += earliest is not None
        expected_signal_detected += (
            expected in series and detect(series[expected], expected) is not None
        )
        forecast_onsets = {name for name, values in series.items()
                           if detect_forecast(values, name) is not None}
        forecast_detected += bool(forecast_onsets)
        forecast_expected += expected in forecast_onsets
        baseline_samples = sum(
            report["baseline_start"] + index * report["step_seconds"] < report["injected_at"]
            for index in range(max(map(len, series.values()), default=0))
        )
        for name, detector in (("rolling_mad", detect), ("forecast_residual", detect_forecast)):
            healthy_alerts[name] += any(
                detector(values[:baseline_samples], metric) is not None
                for metric, values in series.items()
            )
        earliest_correct += earliest == expected
        ranked_correct += top == expected
        delay = report.get("detection_delay_seconds")
        suffix = f"; detection delay={delay}s" if delay is not None else ""
        print(f"{report['fault']}: expected={expected}; earliest={earliest}; ranked={top}{suffix}")
    print(f"Detection over {len(reports)} replayed runs: any_signal={detected_runs}, "
          f"expected_signal={expected_signal_detected}")
    print(f"Forecast residual detection: any_signal={forecast_detected}, "
          f"expected_signal={forecast_expected}")
    print(f"Baseline windows with an alert: {healthy_alerts}")
    print(f"Correct over {len(reports)} replayed runs: earliest={earliest_correct}, ranked={ranked_correct}")
    for path in sorted(Path("evaluation").glob("*-recovery.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            action = json.loads(line)
            print(f"{path.name}: execution={action['execution']}; "
                  f"command-only recovery={action['command_only_recovery']}; "
                  f"probe verdict={action['verification']['verdict']}")


if __name__ == "__main__":
    main()
