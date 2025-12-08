run:
    @ PYTHONASYNCIODEBUG=1 && uv run src/main.py

hl-sync:
    @ humanlayer thoughts sync

hl-status:
    @ humanlayer thoughts status

check:
    @ uv run ruff check --force-exclude --fix
    @ uv run ruff format --force-exclude
    @ uv run pyrefly check

test:
    @ uv run pytest

test-cov:
    @ uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=90

check-test:
    @ just check
    @ just test

sync-stack-changes:
    @ git town sync --stack --detached

ship:
    @ git town ship
    @ git town sync --all
