set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

default:
    @just --list

# Install the project and dev tools from the lockfile
setup:
    uv sync --locked

# Run the test suite
test *args:
    uv run pytest {{args}}

# Lint, check formatting and type-check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy src tests

# Format and apply safe lint fixes
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Everything CI runs
check: lint test

# Run the CLI, e.g. `just run proxy --help`
run *args:
    uv run thetacode {{args}}
