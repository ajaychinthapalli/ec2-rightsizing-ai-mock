# Contributing

Thanks for helping improve this project.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Validation

Before opening a PR, run the offline mock validation to confirm the change does not break the expected behavior:

```bash
python3 rightsize_ec2.py --region us-east-2 --input data/rightsizing_mock.json
```

## Pull request expectations

- Keep changes small and focused.
- Update documentation when behavior or inputs change.
- Include a quick explanation of the reason for the change and the validation you ran.
- Do not commit AWS credentials or any real account data.

## Code style

- Follow the existing Python conventions in the repository.
- Prefer clear, readable logic over clever shortcuts.
- Keep synthetic/mock data isolated from real account usage.
