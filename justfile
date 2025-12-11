run:
    @ PYTHONASYNCIODEBUG=1 && uv run src/main.py

hl-sync:
    @. ./.claude/scripts/run_silent.sh && run_silent "Thought sync successful" "humanlayer thoughts sync"

hl-status:
    @ humanlayer thoughts status

check:
    @. ./.claude/scripts/run_silent.sh && run_silent "Ruff check passed" "uv run ruff check --force-exclude --fix"
    @. ./.claude/scripts/run_silent.sh && run_silent "Ruff format passed" "uv run ruff format --force-exclude"
    @. ./.claude/scripts/run_silent.sh && run_silent "Pyrefly check passed" "uv run pyrefly check"

tests:
    @. ./.claude/scripts/run_silent.sh && run_silent_with_test_count "Pytests passed successfully" "uv run pytest -x"

test TEST:
    @. ./.claude/scripts/run_silent.sh && run_silent_with_test_count "{{TEST}} passed successfully" "uv run pytest -x {{TEST}}"


test-cov:
    @ uv run pytest --cov=src --cov-report=term-missing --cov-fail-under=90

check-test:
    @ just check
    @ just tests

sync-stack-changes:
    @ git town sync --stack --detached

ship:
    @ git town ship
    @ git town sync --all

new-stack PHASE_NUM BRANCH_NAME COMMIT_MSG PR_TITLE PR_BODY:
    @. ./.claude/scripts/run_silent.sh && run_silent "Stack created successfully! Branch: {{BRANCH_NAME}}" "./.claude/skills/stacked-pr/scripts/new-stack.sh \
      {{PHASE_NUM}} \
      {{BRANCH_NAME}} \
      {{COMMIT_MSG}} \
      {{PR_TITLE}} \
      {{PR_BODY}}"

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
