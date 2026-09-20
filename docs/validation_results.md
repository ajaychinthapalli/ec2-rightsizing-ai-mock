# Validation results

First run: Claude Sonnet 4.6 on Amazon Bedrock, **before** the prompt was tightened.
Graded against `expected_answers.md`.

| Check | Result |
|---|---|
| Memory-bound instance not downsized | Pass (recommended an upsize, which is defensible) |
| Unknown memory treated as low confidence | Pass |
| 30 hours of data marked insufficient | Pass |
| Burstable, credits exhausted | **Partial**: flagged the problem but recommended t3.medium, which shares t3.small's baseline and would not fix it |
| EKS node reminder about pod requests | Pass |
| Oversized, right-sized, undersized, idle | Pass |

## Problems found, and the prompt rules added to fix them
1. **t3.small to t3.medium for credit starvation.** Added a rule that both sizes share the same baseline.
2. **`recommended` cell contradicted the reasoning** (listed two types). Added: exactly one type per cell.
3. **Unknown-memory instance had action `downsize`**, which an automated reader would act on. Added the `needs-data` action.
4. **Invented detail** (a credit cap not present in the input). Added: use only figures present in the input.

## Status
The tightened prompt in `rightsize_ec2.py` has not been re-run against the mock data.
Re-run and update this file with the results.
