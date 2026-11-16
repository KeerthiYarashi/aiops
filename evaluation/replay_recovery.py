"""Replay recovery evidence and compare it with command success alone."""

from verification.recovery import recovery_verdict
from time import perf_counter


CASES = (
    ("working application", 0.0, (201, 200, True, 200, True), "recovered"),
    ("catalog failure", 0.8, (201, 503, False, 200, True), "still degraded"),
    ("Redis fallback", 0.0, (201, 200, True, 503, True), "still degraded"),
    ("missing metrics", None, (201, 200, True, 200, False), "insufficient evidence"),
    ("wrong item returned", 0.0, (201, 200, False, 200, True), "still degraded"),
)


def main() -> None:
    false_command, false_probe = 0, 0
    for name, error_rate, evidence, expected in CASES:
        started = perf_counter()
        baseline = ("insufficient evidence" if error_rate is None else
                    "recovered" if error_rate == 0 else "still degraded")
        baseline_ms = (perf_counter() - started) * 1000
        started = perf_counter()
        verdict = recovery_verdict(*evidence)
        verification_ms = (perf_counter() - started) * 1000
        if verdict != expected:
            raise RuntimeError(f"{name}: expected {expected}, got {verdict}")
        false_command += baseline == "recovered" and expected != "recovered"
        false_probe += verdict == "recovered" and expected != "recovered"
        print(f"{name}: error-rate baseline={baseline} ({baseline_ms:.3f} ms), "
              f"verified={verdict} ({verification_ms:.3f} ms)")
    print(f"False recovery declarations: error rate only={false_command}, verified={false_probe}")


if __name__ == "__main__":
    main()
