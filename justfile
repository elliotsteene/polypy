run:
    @ PYTHONASYNCIODEBUG=1 && uv run src/main.py

hl-sync:
    @ humanlayer thoughts sync

hl-status:
    @ humanlayer thoughts status

check:
    @ uv run ruff check --force-exclude --fix
    @ uv run ruff format --force-exclude

test:
    @ uv run pytest

check-test:
    @ just check
    @ just test
