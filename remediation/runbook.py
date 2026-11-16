"""Diagnose an experiment, record an operator decision and verify recovery."""

import argparse
import getpass
from hashlib import sha256
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from time import time
from uuid import uuid4

from agent.diagnose import graph
from chaos.run_experiments import wait_for_ready
from verification.recovery import verify_recovery


ACTIONS = {
    "restart": ["restart", "api"],
    "flush-cache": [
        "exec", "-T", "api", "python", "-c",
        "import os, redis\n"
        "client = redis.Redis.from_url(os.environ['REDIS_URL'])\n"
        "for key in client.scan_iter(match='item:*'):\n"
        "    client.delete(key)",
    ],
}


def execute(action: str) -> None:
    subprocess.run(["docker", "compose", *ACTIONS[action]], check=True, timeout=30)


def target_version(action: str) -> str:
    service = "api" if action == "restart" else "redis"
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", service], text=True, timeout=10,
    ).strip()
    if not container or "\n" in container:
        raise RuntimeError(f"Expected one running {service} container")
    return container


def approve_plan(path: Path, binding: dict) -> None:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if any(plan.get(key) != value for key, value in binding.items()):
        raise RuntimeError("Approval target, incident, or action changed")
    if time() >= plan["expires_at"]:
        raise RuntimeError("Approval expired. Create a new preview.")
    with path.with_suffix(".used").open("x", encoding="utf-8") as marker:
        marker.write(datetime.now(timezone.utc).isoformat())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--action", choices=ACTIONS)
    decision = parser.add_mutually_exclusive_group()
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--reject", action="store_true")
    parser.add_argument("--plan", type=Path, help="Preview file for an approved action")
    args = parser.parse_args()
    if (args.approve or args.reject) and not args.action:
        parser.error("choose an action before approving or rejecting")
    if args.approve and not args.plan:
        parser.error("--approve requires the --plan file printed by the preview")

    report_bytes = args.report.read_bytes()
    report = json.loads(report_bytes)
    diagnosis = graph.invoke({"report": report})
    root = diagnosis["result"]["root"]
    signals = {candidate["metric"] for candidate in report["candidates"]}
    restart_signals = {"availability", "http_errors", "cpu", "memory"}
    recommendation = None
    if root in restart_signals and root in signals:
        recommendation = "restart"
    outcome = {"time": datetime.now(timezone.utc).isoformat(), "diagnosis": diagnosis["result"],
               "action": args.action, "operator": getpass.getuser(),
               "recommended_action": recommendation,
               "execution": "dry-run" if args.action else "skipped"}
    if args.reject:
        outcome["execution"] = "rejected"
    elif args.action:
        try:
            binding = {"report_sha256": sha256(report_bytes).hexdigest(), "action": args.action,
                       "target_version": target_version(args.action)}
            outcome["target_version"] = binding["target_version"]
            if args.action == "flush-cache" and "redis_errors" not in signals:
                raise RuntimeError("Cache flush requires Redis error evidence")
            if args.action == "restart" and not signals & restart_signals:
                raise RuntimeError("Restart requires availability, error, CPU, or memory evidence")
            if args.approve:
                approve_plan(args.plan, binding)
                execute(args.action)
                outcome["execution"] = "completed"
            else:
                plan_path = args.report.with_name(f"{args.report.stem}-{uuid4().hex[:8]}.approval")
                plan_path.write_text(json.dumps({**binding, "expires_at": time() + 600}), encoding="utf-8")
                outcome["plan"] = str(plan_path)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError, TypeError) as error:
            outcome.update(execution="failed", execution_error=str(error))
        if outcome["execution"] == "completed" and args.action == "restart":
            try:
                wait_for_ready()
            except RuntimeError as error:
                outcome["readiness_error"] = str(error)
    outcome["verification"] = verify_recovery()
    outcome["command_only_recovery"] = outcome["execution"] == "completed"
    record = args.report.with_name(f"{args.report.stem}-recovery.jsonl")
    with record.open("a", encoding="utf-8") as output:
        output.write(json.dumps(outcome) + "\n")
    print(json.dumps(outcome, indent=2))
    if outcome["execution"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
