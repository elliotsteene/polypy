#!/bin/bash
# Smart wrapper for selective command output suppression
# Automatically applies run_silent to verbose commands while preserving useful output

set -eo pipefail

# Source the run_silent functions
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/run_silent.sh"

# Function to detect if a command should be wrapped
should_wrap() {
    local cmd="$1"

    # Commands that are verbose on success but valuable on failure
    # Returns 0 (true) if should wrap, 1 (false) if should not wrap

    # Test commands - always wrap
    if echo "$cmd" | grep -qE "^(uv run )?(pytest|python -m pytest)"; then
        return 0
    fi

    # Linting/formatting commands - always wrap
    if echo "$cmd" | grep -qE "^(uv run )?(ruff|pyrefly|black|isort|mypy|pylint)"; then
        return 0
    fi

    # Build commands - wrap
    if echo "$cmd" | grep -qE "^(docker build|cargo build|npm run build|make build)"; then
        return 0
    fi

    # Install/sync commands - wrap
    if echo "$cmd" | grep -qE "^(uv sync|pip install|npm install|yarn install)"; then
        return 0
    fi

    # Git commands - DO NOT wrap (except for push/pull which can be verbose)
    if echo "$cmd" | grep -qE "^git (push|pull|fetch)"; then
        return 0
    fi

    # DO NOT wrap these valuable commands
    if echo "$cmd" | grep -qE "^(ls|cat|grep|find|git status|git diff|git log|git branch)"; then
        return 1
    fi

    # Default: do not wrap
    return 1
}

# Function to generate description from command
generate_description() {
    local cmd="$1"

    # Try to generate a human-readable description
    if echo "$cmd" | grep -qE "^(uv run )?pytest"; then
        echo "Running tests"
    elif echo "$cmd" | grep -qE "^(uv run )?ruff check"; then
        echo "Ruff check"
    elif echo "$cmd" | grep -qE "^(uv run )?ruff format"; then
        echo "Ruff format"
    elif echo "$cmd" | grep -qE "^(uv run )?pyrefly"; then
        echo "Pyrefly check"
    elif echo "$cmd" | grep -qE "^docker build"; then
        echo "Docker build"
    elif echo "$cmd" | grep -qE "^git push"; then
        echo "Git push"
    elif echo "$cmd" | grep -qE "^uv sync"; then
        echo "UV sync"
    else
        # Generic description
        echo "$(echo "$cmd" | cut -d' ' -f1-3)"
    fi
}

# Main execution
if [ $# -eq 0 ]; then
    echo "Usage: smart_wrap.sh <command>"
    echo "Automatically wraps verbose commands with run_silent"
    exit 1
fi

COMMAND="$*"

if should_wrap "$COMMAND"; then
    # This is a verbose command - wrap it
    DESCRIPTION=$(generate_description "$COMMAND")

    # Check if it's a test command (use test counter variant)
    if echo "$COMMAND" | grep -qE "^(uv run )?(pytest|python -m pytest)"; then
        run_silent_with_test_count "$DESCRIPTION" "$COMMAND"
    else
        run_silent "$DESCRIPTION" "$COMMAND"
    fi
else
    # Not a verbose command - run normally
    eval "$COMMAND"
fi
