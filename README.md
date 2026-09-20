# AI-assisted EC2 right-sizing with CloudWatch metrics and Claude on Amazon Bedrock

Turns CloudWatch utilization metrics into per-instance right-sizing recommendations
(keep, downsize, upsize, terminate-candidate) using Claude on Amazon Bedrock, and
includes a **mock dataset with known answers** so you can test the AI's judgment
before trusting it on real infrastructure.

```
Metrics (CloudWatch) -> Summary (JSON) -> Analysis (Claude on Bedrock) -> Human review
```

> The data in this repo is **synthetic**. Nothing here comes from a real account.

## What it does
- Collects hourly CPU (average, p95, max), peak network throughput, memory, and CPU
  credit balance (burstable types) for running EC2 instances
- Reads memory from the CloudWatch agent (`CWAgent`) or Container Insights, since EC2
  does not publish memory by default
- Summarizes each instance into compact JSON
- Sends the JSON to Claude through the Bedrock Converse API with a rules-based prompt
- Runs fully offline against the mock dataset with `--input`

## Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 1. Offline check: no AWS calls, no Bedrock
python3 rightsize_ec2.py --region us-east-2 --input data/rightsizing_mock.json

# 2. Run the AI step on the mock data
python3 rightsize_ec2.py --region us-east-2 --input data/rightsizing_mock.json \
  --model-id <bedrock-model-or-inference-profile-id>

# 3. Collect from a real account, then analyze (needs at least ~7 days of history)
python3 rightsize_ec2.py --region us-east-2 --days 14 --tag Environment=dev \
  --model-id <bedrock-model-or-inference-profile-id>
```

List available inference profiles with:

```bash
aws bedrock list-inference-profiles --region us-east-2 \
  --query "inferenceProfileSummaries[?contains(inferenceProfileId,'anthropic')].inferenceProfileId" --output text
```

## Bedrock prerequisites
- Complete the Anthropic **use case details** form in the Bedrock console (once per account).
  Without it, calls fail with `ResourceNotFoundException: Model use case details have not been submitted`.
  Check with `aws bedrock get-use-case-for-model-access --region <region>`.
- Use an inference profile ID (for example `us.anthropic.<model>`), not a bare model ID.
- Use a least-privilege role, not root. See `iam/least-privilege-policy.json`.

## Mock dataset
`data/rightsizing_mock.json` has 9 instances, each designed to test one behavior:

| Instance | Scenario | Correct action |
|---|---|---|
| `i-mock01overprov` | Oversized m5.2xlarge | downsize |
| `i-mock02rightsz` | Well-sized m5.large | keep |
| `i-mock03undersz` | CPU-saturated c5.large | upsize |
| `i-mock04membound` | Idle CPU, memory at 91% | keep or upsize, never downsize |
| `i-mock05nomemory` | Low CPU, no memory data | needs-data |
| `i-mock06newinst` | 30 hours of history | insufficient-data |
| `i-mock07burstcpu` | t3.small, credits exhausted | upsize to non-burstable |
| `i-mock08idle` | t3.large, almost no activity | terminate-candidate |
| `i-mock09eksnode` | Healthy EKS node | keep, check pod requests |

Full answer key: [`docs/expected_answers.md`](docs/expected_answers.md).
Results and the prompt fixes they led to: [`docs/validation_results.md`](docs/validation_results.md).

## Design choices
- **p95 and max, not averages.** Averages hide the peaks that cause incidents.
- **Missing data is a first-class outcome.** Unknown memory or under 7 days of history
  produces `needs-data` or `insufficient-data`, not a guess.
- **The model recommends; a human applies.** Never wire the output straight into
  `update-stack` or an ASG change.
- **EKS nodes need one more check.** Pod resource requests decide what fits on a node,
  so verify requests against usage (`kubectl top`, VPA, OpenCost) before resizing.

## Limitations
- The reported p95 is the 95th percentile of hourly p95 values, an approximation of the
  p95 over raw datapoints.
- The model does not see prices. Use Cost Explorer or the EC2 pricing page for savings.
- A 14-day window can miss monthly or quarterly spikes. Cross-check job schedules.
- Cross-check against AWS Compute Optimizer (`aws compute-optimizer get-ec2-instance-recommendations`).

## Layout

```
rightsize_ec2.py                 collector + Bedrock call
data/rightsizing_mock.json       synthetic dataset
docs/expected_answers.md         answer key
docs/validation_results.md       grading and prompt fixes
iam/least-privilege-policy.json  minimal permissions
requirements.txt
```
