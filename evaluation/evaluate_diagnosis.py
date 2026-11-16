"""Compare statistical ranking and model-assisted diagnosis on saved experiments."""

import argparse
import json
from pathlib import Path

from agent.diagnose import graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("evaluation"))
    args = parser.parse_args()
    results = []
    for path in sorted(args.directory.glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        if "expected_metric" not in report:
            continue
        candidates = report["candidates"]
        expected = report["expected_metric"]
        statistical_root = candidates[0]["metric"] if candidates else None
        # Ground truth is used for scoring only.
        diagnosis = graph.invoke({
            "report": {key: value for key, value in report.items()
                       if key not in {"fault", "expected_metric"}},
        })["result"]
        result = {
            "report": path.name,
            "expected": expected,
            "statistical": statistical_root,
            "diagnosed": diagnosis["root"],
            "source": diagnosis["source"],
        }
        if "model_error" in diagnosis:
            result["model_error"] = diagnosis["model_error"]
        results.append(result)
        print(json.dumps(result), flush=True)
    if not results:
        parser.error("No incident reports found. Run a fault experiment first.")
    model_results = [result for result in results if result["source"] in {"ollama", "bedrock"}]
    print(json.dumps({
        "reports": len(results),
        "statistical_correct": sum(r["statistical"] == r["expected"] for r in results),
        "diagnosis_correct": sum(r["diagnosed"] == r["expected"] for r in results),
        "model_responses": len(model_results),
        "model_correct": sum(r["diagnosed"] == r["expected"] for r in model_results),
        "statistical_correct_on_model_subset": sum(
            r["statistical"] == r["expected"] for r in model_results
        ),
    }))


if __name__ == "__main__":
    main()
