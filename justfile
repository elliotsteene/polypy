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

# Docker commands
docker-build:
    docker build -t polypy:latest .

docker-up:
    docker-compose up -d

docker-down:
    docker-compose down

docker-logs:
    docker-compose logs -f

docker-restart:
    docker-compose restart

docker-clean:
    docker-compose down -v
    docker rmi polypy:latest || true

# Start full stack (polypy + prometheus)
stack-up: docker-build docker-up
    @echo "✓ Stack is up!"
    @echo "  - PolyPy: http://localhost:8080"
    @echo "  - PolyPy Stats: http://localhost:8080/stats"
    @echo "  - PolyPy Health: http://localhost:8080/health"
    @echo "  - Prometheus: http://localhost:9090"

stack-down: docker-down
    @echo "✓ Stack is down"
