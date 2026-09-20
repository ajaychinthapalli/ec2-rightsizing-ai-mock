#!/usr/bin/env python3
"""
AI-assisted EC2 right-sizing from CloudWatch data.

1. Lists running EC2 instances (optionally filtered by tag).
2. Pulls hourly CloudWatch metrics (CPU avg/p95/max, network peak, memory if the
   CloudWatch agent or Container Insights is installed, CPU credits for burstable).
3. Writes a compact JSON summary (always).
4. If --model-id is given, sends the summary to Claude on Amazon Bedrock and
   prints the recommendations.

Usage:
  python3 rightsize_ec2.py --region us-east-2 --days 14
  python3 rightsize_ec2.py --region us-east-2 --tag Environment=dev --model-id <bedrock-model-or-profile-id>
  python3 rightsize_ec2.py --region us-east-2 --input data/rightsizing_mock.json --model-id <id>   # skip collection

Required IAM: ec2:DescribeInstances, ec2:DescribeInstanceTypes,
cloudwatch:GetMetricData, cloudwatch:ListMetrics, bedrock:InvokeModel (for AI step).
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone

import boto3

PERIOD = 3600  # hourly datapoints: 14 days = 336 points, well under API limits

MEMORY_CANDIDATES = [
    ("CWAgent", "mem_used_percent"),
    ("ContainerInsights", "node_memory_utilization"),
]

SYSTEM_PROMPT = """You are a cloud cost engineer reviewing EC2 right-sizing data.
Input is JSON: per-instance CloudWatch summaries plus current instance specs.

Rules:
- Base decisions on p95 and max, not averages. Target p95 CPU of roughly 40-60%
  and p95 memory below ~75% after resizing, keeping headroom for spikes.
- If memory is null, memory is UNKNOWN: set action to "needs-data" (never
  "downsize"). Mention any tentative target only in the reason and say the
  CloudWatch agent (or Container Insights) is needed.
- If observed_hours < 168 (7 days), mark the instance "insufficient-data".
- For burstable types (t2/t3/t4g), check cpu_credit_balance_min and surplus
  credits. Sustained low balance means the instance is undersized for its load.
- Keep the same CPU architecture unless suggesting Graviton (arm64) as a
  clearly labelled separate option that requires arm64-compatible images.
- If is_eks_node is true, remind that pod resource REQUESTS decide scheduling,
  so check requests vs usage (kubectl top, VPA, OpenCost) before resizing.
- Burstable types: t3.small and t3.medium share the same per-vCPU baseline (20%),
  so moving between them does not fix credit starvation. Prefer a non-burstable
  family, or a larger burstable size with a higher baseline.
- The "recommended" column must contain exactly one instance type (or "-").
  Put alternatives in the reason.
- Use only figures present in the input. Do not state credit caps, prices, or
  other specs that are not provided.
- Do not invent prices. Point to the pricing page or Cost Explorer for savings.

Output:
1. A markdown table: instance_id | name | current | action | recommended | confidence | reason
   where action is one of: keep, downsize, upsize, terminate-candidate, insufficient-data, needs-data.
2. A short "Caveats" list. Be concise."""


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def rnd(x, n=1):
    return None if x is None else round(x, n)


def get_instances(ec2, tag_filters):
    filters = [{"Name": "instance-state-name", "Values": ["running"]}]
    for t in tag_filters:
        key, _, val = t.partition("=")
        filters.append({"Name": f"tag:{key}", "Values": [val]})
    found = []
    for page in ec2.get_paginator("describe_instances").paginate(Filters=filters):
        for res in page["Reservations"]:
            found.extend(res["Instances"])
    return found


def get_type_specs(ec2, types):
    specs = {}
    types = sorted(set(types))
    for i in range(0, len(types), 100):
        resp = ec2.describe_instance_types(InstanceTypes=types[i : i + 100])
        for t in resp["InstanceTypes"]:
            specs[t["InstanceType"]] = {
                "vcpu": t["VCpuInfo"]["DefaultVCpus"],
                "memory_gib": round(t["MemoryInfo"]["SizeInMiB"] / 1024, 1),
                "arch": t["ProcessorInfo"]["SupportedArchitectures"],
                "network": t["NetworkInfo"]["NetworkPerformance"],
                "burstable": bool(t.get("BurstablePerformanceSupported", False)),
            }
    return specs


def query(qid, ns, metric, dims, stat):
    return {
        "Id": qid,
        "MetricStat": {
            "Metric": {"Namespace": ns, "MetricName": metric, "Dimensions": dims},
            "Period": PERIOD,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def fetch(cw, queries, start, end):
    out = {}
    pager = cw.get_paginator("get_metric_data")
    for page in pager.paginate(MetricDataQueries=queries, StartTime=start, EndTime=end):
        for res in page["MetricDataResults"]:
            out.setdefault(res["Id"], []).extend(res["Values"])
    return out


def find_memory_metric(cw, instance_id):
    """Return (namespace, metric, dimensions) for memory, or None."""
    for ns, metric in MEMORY_CANDIDATES:
        resp = cw.list_metrics(
            Namespace=ns,
            MetricName=metric,
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        )
        if resp["Metrics"]:
            return ns, metric, resp["Metrics"][0]["Dimensions"]
    return None


def summarize_instance(cw, inst, spec, start, end, days):
    iid = inst["InstanceId"]
    itype = inst["InstanceType"]
    dims = [{"Name": "InstanceId", "Value": iid}]
    qs = [
        query("cpu_avg", "AWS/EC2", "CPUUtilization", dims, "Average"),
        query("cpu_p95", "AWS/EC2", "CPUUtilization", dims, "p95"),
        query("cpu_max", "AWS/EC2", "CPUUtilization", dims, "Maximum"),
        query("net_in", "AWS/EC2", "NetworkIn", dims, "Sum"),
        query("net_out", "AWS/EC2", "NetworkOut", dims, "Sum"),
    ]
    if spec["burstable"]:
        qs.append(query("credit_min", "AWS/EC2", "CPUCreditBalance", dims, "Minimum"))
        qs.append(
            query("surplus_max", "AWS/EC2", "CPUSurplusCreditsCharged", dims, "Maximum")
        )

    mem = find_memory_metric(cw, iid)
    if mem:
        ns, metric, mdims = mem
        qs.append(query("mem_avg", ns, metric, mdims, "Average"))
        qs.append(query("mem_p95", ns, metric, mdims, "p95"))
        qs.append(query("mem_max", ns, metric, mdims, "Maximum"))

    d = fetch(cw, qs, start, end)
    cpu_avg = d.get("cpu_avg", [])

    def mbps(vals):
        return max(vals) * 8 / PERIOD / 1e6 if vals else None

    summary = {
        "instance_id": iid,
        "name": next(
            (t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), None
        ),
        "is_eks_node": any(
            t["Key"].startswith(("kubernetes.io/cluster/", "eks:", "aws:eks:"))
            for t in inst.get("Tags", [])
        ),
        "instance_type": itype,
        "specs": spec,
        "observed_hours": len(cpu_avg),
        "requested_hours": days * 24,
        "cpu_avg_pct": rnd(statistics.mean(cpu_avg)) if cpu_avg else None,
        # approximation: 95th percentile across the hourly p95 values
        "cpu_p95_pct": rnd(pct(d.get("cpu_p95", []), 95)),
        "cpu_max_pct": rnd(max(d["cpu_max"])) if d.get("cpu_max") else None,
        "network_in_peak_mbps": rnd(mbps(d.get("net_in", [])), 2),
        "network_out_peak_mbps": rnd(mbps(d.get("net_out", [])), 2),
        "memory_source": f"{mem[0]}/{mem[1]}" if mem else None,
        "mem_avg_pct": rnd(statistics.mean(d["mem_avg"])) if d.get("mem_avg") else None,
        "mem_p95_pct": rnd(pct(d.get("mem_p95", []), 95)),
        "mem_max_pct": rnd(max(d["mem_max"])) if d.get("mem_max") else None,
    }
    if spec["burstable"]:
        summary["cpu_credit_balance_min"] = (
            rnd(min(d["credit_min"])) if d.get("credit_min") else None
        )
        summary["cpu_surplus_credits_max"] = (
            rnd(max(d["surplus_max"])) if d.get("surplus_max") else None
        )
    return summary


def ask_claude(region, model_id, payload):
    br = boto3.client("bedrock-runtime", region_name=region)
    resp = br.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[
            {"role": "user", "content": [{"text": json.dumps(payload, indent=2)}]}
        ],
        inferenceConfig={"maxTokens": 4000},
    )
    return resp["output"]["message"]["content"][0]["text"]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--region", required=True)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument(
        "--tag", action="append", default=[], help="Key=Value filter, repeatable"
    )
    ap.add_argument(
        "--model-id",
        help="Bedrock model ID or inference profile ID; omit to skip the AI step",
    )
    ap.add_argument(
        "--input",
        help="Use an existing summary JSON (e.g. mock data) instead of collecting from AWS",
    )
    ap.add_argument("--out", default="rightsizing.json")
    args = ap.parse_args()

    if args.input:
        with open(args.input) as f:
            payload = json.load(f)
        results = payload["instances"]
        print(
            f"Loaded {len(results)} instances from {args.input} (no AWS collection)",
            file=sys.stderr,
        )
    else:
        ec2 = boto3.client("ec2", region_name=args.region)
        cw = boto3.client("cloudwatch", region_name=args.region)

        instances = get_instances(ec2, args.tag)
        if not instances:
            sys.exit("No running instances matched.")
        specs = get_type_specs(ec2, [i["InstanceType"] for i in instances])

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.days)

        results = []
        for inst in instances:
            print(
                f"Collecting {inst['InstanceId']} ({inst['InstanceType']})...",
                file=sys.stderr,
            )
            results.append(
                summarize_instance(
                    cw, inst, specs[inst["InstanceType"]], start, end, args.days
                )
            )

        payload = {
            "region": args.region,
            "window_days": args.days,
            "instances": results,
        }
        with open(args.out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.out} ({len(results)} instances)", file=sys.stderr)

    for r in results:
        if r["memory_source"] is None:
            print(
                f"  ! {r['instance_id']}: no memory metric found (install CloudWatch agent / Container Insights)",
                file=sys.stderr,
            )
        if r["observed_hours"] < 168:
            print(
                f"  ! {r['instance_id']}: only {r['observed_hours']}h of data; too little for a reliable recommendation",
                file=sys.stderr,
            )

    if args.model_id:
        print("\n" + ask_claude(args.region, args.model_id, payload))
    else:
        print(
            "No --model-id given: skipped AI step. Paste the JSON into Claude, or rerun with --model-id.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
