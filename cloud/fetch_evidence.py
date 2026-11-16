"""Export CloudWatch application logs for one recorded experiment."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("instance_id")
    args = parser.parse_args()

    import boto3

    report = json.loads(args.report.read_text(encoding="utf-8"))
    client = boto3.client("logs")
    pages = client.get_paginator("filter_log_events").paginate(
        logGroupName="/verify-ops/application",
        logStreamNames=[args.instance_id],
        startTime=int(report["injected_at"] * 1000),
        endTime=int(report["cleared_at"] * 1000) + 1,
    )
    path = args.report.with_suffix(".cloudwatch.jsonl")
    count = 0
    with path.open("w", encoding="utf-8") as output:
        for page in pages:
            for event in page["events"]:
                try:
                    entry = json.loads(event["message"])
                except ValueError:
                    continue
                output.write(json.dumps(entry) + "\n")
                count += 1
    print(f"Exported {count} events to {path}")


if __name__ == "__main__":
    main()
