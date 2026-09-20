# Expected answers for the mock dataset

Each instance in `data/rightsizing_mock.json` is built to test one behavior.
Grade the **action and reasoning**, not the exact instance type; several rows
are judgment calls.

| Instance | Scenario | Correct action |
|---|---|---|
| `i-mock01overprov` (m5.2xlarge) | p95 CPU 22%, memory p95 33% | **downsize** to about m5.xlarge (m5.large would push memory past 100%) |
| `i-mock02rightsz` (m5.large) | p95 CPU 52%, memory p95 61% | **keep** |
| `i-mock03undersz` (c5.large) | p95 CPU 96%, max 100% | **upsize** |
| `i-mock04membound` (r5.large) | CPU idle, memory p95 91% | **keep or upsize; never downsize** |
| `i-mock05nomemory` (m5.xlarge) | Low CPU, memory null | **needs-data** (any tentative downsize must be low confidence and say memory is unknown) |
| `i-mock06newinst` (m5.large) | 30 hours of data | **insufficient-data** |
| `i-mock07burstcpu` (t3.small) | Credits at 0, surplus charges | **upsize to a non-burstable type** (t3.medium does not fix it) |
| `i-mock08idle` (t3.large) | p95 CPU 4%, near-zero network | **terminate-candidate** (or downsize) |
| `i-mock09eksnode` (t3.small, EKS) | Healthy utilization | **keep**, with a reminder to check pod requests |

## Rules the system prompt enforces
- Decide on p95 and max, not averages
- Unknown memory means `needs-data`, not `downsize`
- Fewer than 168 observed hours means `insufficient-data`
- Burstable types: check credit balance and surplus credits
- One instance type per `recommended` cell
- Only use figures present in the input
