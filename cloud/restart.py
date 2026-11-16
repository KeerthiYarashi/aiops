"""Request an explicitly approved Verify Ops API restart on one managed EC2 instance."""

import argparse
import json
from time import monotonic, sleep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()
    if not args.approve:
        print("Dry run. Target:", args.instance_id, "Action: restart Verify Ops API")
        return

    import boto3

    client = boto3.client("ssm")
    command = client.send_command(
        InstanceIds=[args.instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": ["cd /opt/verify-ops && docker compose restart api"]},
    )["Command"]["CommandId"]
    print(f"SSM command ID: {command}", flush=True)
    deadline = monotonic() + 90
    while monotonic() < deadline:
        try:
            execution = client.get_command_invocation(CommandId=command, InstanceId=args.instance_id)
        except client.exceptions.InvocationDoesNotExist:
            sleep(2)
            continue
        status = execution["Status"]
        if status not in {"Pending", "InProgress", "Delayed", "Cancelling"}:
            result = {
                "command_id": command, "status": status,
                "command_only_recovery": status == "Success",
            }
            if status == "Success":
                from chaos.run_experiments import wait_for_ready
                from verification.recovery import verify_recovery

                try:
                    wait_for_ready()
                except RuntimeError:
                    pass
                result["verification"] = verify_recovery()
            print(json.dumps(result, indent=2))
            recovered = result.get("verification", {}).get("verdict") == "recovered"
            raise SystemExit(0 if recovered else 1)
        sleep(2)
    raise SystemExit(f"Timed out waiting for command {command}. Check that command before retrying.")


if __name__ == "__main__":
    main()
