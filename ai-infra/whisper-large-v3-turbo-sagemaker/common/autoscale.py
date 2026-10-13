"""
Attach Application Auto Scaling to a Whisper SageMaker real-time endpoint.

Registers the endpoint's production variant as a scalable target and applies a
target-tracking policy on SageMakerVariantInvocationsPerInstance. Optionally adds a
SCHEDULED scaling action to pre-warm capacity before known peaks -- important because
a new instance takes ~6-12 min to provision + pull the image + start, which is too
slow to absorb a sudden surge reactively.

Because cold start is slow, we scale OUT eagerly (short cooldown) and scale IN slowly
(long cooldown) to avoid thrashing. The model is baked into the image/artifact, so
there is no Hugging Face download at startup.

Examples:
    # Target-tracking autoscaling (1..4 instances, ~250 invocations/instance/min)
    python common/autoscale.py --endpoint-name whisper-vllm --region ap-south-1 \
        --min-capacity 1 --max-capacity 4 --target-invocations-per-instance 250

    # Add scheduled actions to pre-warm for a daily peak (UTC cron), then scale down.
    # Pre-warm to 4 instances at 08:30, drop the floor back to 1 at 20:00.
    python common/autoscale.py --endpoint-name whisper-vllm --region ap-south-1 \
        --schedule-name prewarm --schedule "cron(30 8 * * ? *)" \
        --schedule-min 4 --schedule-max 8 --timezone Asia/Kolkata

    python common/autoscale.py --endpoint-name whisper-vllm --region ap-south-1 \
        --schedule-name off-peak --schedule "cron(0 20 * * ? *)" \
        --schedule-min 1 --schedule-max 8 --timezone Asia/Kolkata
"""

import argparse

import boto3

SERVICE_NS = "sagemaker"
SCALABLE_DIM = "sagemaker:variant:DesiredInstanceCount"


def parse_args():
    p = argparse.ArgumentParser(description="Configure autoscaling for a SageMaker endpoint.")
    p.add_argument("--endpoint-name", required=True)
    p.add_argument("--variant-name", default="AllTraffic",
                   help="Production variant name (SageMaker default is 'AllTraffic').")
    p.add_argument("--region", default=None)
    p.add_argument("--min-capacity", type=int, default=1,
                   help="Minimum instances. Use >=2 for high availability across AZs.")
    p.add_argument("--max-capacity", type=int, default=4)
    p.add_argument("--target-invocations-per-instance", type=float, default=250.0,
                   help="Target invocations/instance/minute. Tune from load tests.")
    p.add_argument("--scale-out-cooldown", type=int, default=120)
    p.add_argument("--scale-in-cooldown", type=int, default=600)
    p.add_argument("--no-target-tracking", action="store_true",
                   help="Skip creating/updating the target-tracking policy (e.g. when you only "
                        "want to add a scheduled action).")

    # Scheduled scaling (optional).
    p.add_argument("--schedule", default=None,
                   help="Schedule expression for a scheduled action, e.g. "
                        "'cron(30 8 * * ? *)', 'rate(1 hour)', or 'at(2026-06-20T08:30:00)'. "
                        "When set, a scheduled action is created/updated.")
    p.add_argument("--schedule-name", default="prewarm",
                   help="Name for the scheduled action (unique per target).")
    p.add_argument("--schedule-min", type=int, default=None,
                   help="MinCapacity to set when the schedule fires (the pre-warm floor).")
    p.add_argument("--schedule-max", type=int, default=None,
                   help="MaxCapacity to set when the schedule fires (defaults to --max-capacity).")
    p.add_argument("--timezone", default="UTC",
                   help="IANA timezone for the schedule, e.g. 'Asia/Kolkata' (default UTC).")
    return p.parse_args()


def register_target(aas, resource_id, min_cap, max_cap):
    aas.register_scalable_target(
        ServiceNamespace=SERVICE_NS,
        ResourceId=resource_id,
        ScalableDimension=SCALABLE_DIM,
        MinCapacity=min_cap,
        MaxCapacity=max_cap,
    )
    print(f"Registered scalable target: {resource_id} (min={min_cap}, max={max_cap})")


def put_target_tracking(aas, args, resource_id):
    aas.put_scaling_policy(
        PolicyName=f"{args.endpoint_name}-invocations-target-tracking",
        ServiceNamespace=SERVICE_NS,
        ResourceId=resource_id,
        ScalableDimension=SCALABLE_DIM,
        PolicyType="TargetTrackingScaling",
        TargetTrackingScalingPolicyConfiguration={
            "TargetValue": args.target_invocations_per_instance,
            "PredefinedMetricSpecification": {
                "PredefinedMetricType": "SageMakerVariantInvocationsPerInstance",
            },
            "ScaleOutCooldown": args.scale_out_cooldown,
            "ScaleInCooldown": args.scale_in_cooldown,
        },
    )
    print(f"Applied target-tracking policy: {args.target_invocations_per_instance} "
          f"invocations/instance/min (out={args.scale_out_cooldown}s, in={args.scale_in_cooldown}s)")


def put_scheduled_action(aas, args, resource_id):
    action = {}
    if args.schedule_min is not None:
        action["MinCapacity"] = args.schedule_min
    action["MaxCapacity"] = args.schedule_max if args.schedule_max is not None else args.max_capacity
    if "MinCapacity" not in action and "MaxCapacity" not in action:
        raise SystemExit("Provide --schedule-min and/or --schedule-max for the scheduled action.")

    aas.put_scheduled_action(
        ServiceNamespace=SERVICE_NS,
        ResourceId=resource_id,
        ScalableDimension=SCALABLE_DIM,
        ScheduledActionName=args.schedule_name,
        Schedule=args.schedule,
        Timezone=args.timezone,
        ScalableTargetAction=action,
    )
    print(f"Scheduled action '{args.schedule_name}': {args.schedule} ({args.timezone}) "
          f"-> {action}")


def main():
    args = parse_args()
    aas = boto3.client("application-autoscaling", region_name=args.region)
    resource_id = f"endpoint/{args.endpoint_name}/variant/{args.variant_name}"

    # The scalable target must exist before policies or scheduled actions.
    register_target(aas, resource_id, args.min_capacity, args.max_capacity)

    if not args.no_target_tracking:
        put_target_tracking(aas, args, resource_id)

    if args.schedule:
        put_scheduled_action(aas, args, resource_id)

    print("\nDone. Monitor SageMakerVariantInvocationsPerInstance in CloudWatch and "
          "adjust the target/schedule after load testing.")


if __name__ == "__main__":
    main()
