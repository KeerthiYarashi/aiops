"""Check AWS identity, target registration, and log access before a live demo."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance_id")
    parser.add_argument("--profile")
    parser.add_argument("--region", required=True)
    args = parser.parse_args()

    import boto3

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    session.client("sts").get_caller_identity()
    print("AWS authentication passed.")
    response = session.client("ssm").describe_instance_information(
        Filters=[{"Key": "InstanceIds", "Values": [args.instance_id]}],
    )
    instances = response["InstanceInformationList"]
    if not instances or instances[0]["PingStatus"] != "Online":
        parser.exit(1, "The selected EC2 instance is not online in Systems Manager.\n")
    print("Systems Manager target is online.")
    response = session.client("logs").describe_log_streams(
        logGroupName="/verify-ops/application",
        logStreamNamePrefix=args.instance_id,
    )
    if not any(stream["logStreamName"] == args.instance_id for stream in response["logStreams"]):
        parser.exit(1, "The expected CloudWatch log stream is missing.\n")
    print("CloudWatch log stream is available.")


if __name__ == "__main__":
    main()
