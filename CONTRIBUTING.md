# Contributing to Flow

Thanks for your interest in contributing! This guide covers setup, code style, and the PR process.

## Development Setup

```bash
# Clone and enter the repo
git clone https://github.com/elevenoutoften/flow.git
cd flow

# Create a virtual environment
python -m venv .venv
. .venv/bin/activate

# Install with test dependencies
pip install -e ".[test]"
```

## Running Tests

```bash
pytest
```

## Running Locally

```bash
uvicorn flow_app.main:app --host 0.0.0.0 --port 8100
```

The board UI is available at `http://localhost:8100`.

## Code Style

- **Python**: [Ruff](https://docs.aufs.com/ruff/) for linting and formatting. Run `ruff check .` before committing.
- **HTML/CSS/JS**: Follow existing patterns in `flow_app/templates/` and `flow_app/static/`.
- **Commits**: Explain *why*, not just *what*. Short subject line, optional body.

## Pull Request Process

1. **Fork** the repository and create a branch from `main`.
2. **Make your changes** with clear, focused commits.
3. **Add tests** for any new functionality or bug fixes.
4. **Run the test suite** — all tests must pass before submitting.
5. **Open a PR** against `main`. Fill in the PR template.
6. **Address review feedback** — push fixes to the same branch.

## Reporting Issues

- **Bug reports**: Use the [Bug Report](../../issues/new?template=bug_report.yml) template.
- **Feature requests**: Use the [Feature Request](../../issues/new?template=feature_request.yml) template.

## Good First Issues

Look for issues labeled [`good first issue`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) or [`help wanted`](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) — these are great entry points for new contributors.

## Questions?

Open a [Discussion](../../discussions) for questions that aren't bugs or feature requests.