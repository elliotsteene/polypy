#!/bin/bash
set -euo pipefail

# new-stack.sh - Create a new stacked PR (Phase 1)
#
# Usage: new-stack.sh <phase-num> <branch-name> <commit-msg> <pr-title> <pr-body>
#
# Arguments:
#   phase-num    Phase number (e.g., 1)
#   branch-name  Descriptive branch name (e.g., "websocket-foundation")
#   commit-msg   Commit message (multiline string)
#   pr-title     PR title
#   pr-body      PR body (multiline string)

PHASE_NUM=$1
BRANCH_NAME=$2
COMMIT_MSG=$3
PR_TITLE=$4
PR_BODY=$5

FULL_BRANCH_NAME="phase-${PHASE_NUM}-${BRANCH_NAME}"

echo "🚀 Creating new stack: ${FULL_BRANCH_NAME}"

# Pre-flight check: Ensure we're on main
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "⚠️  Warning: Not on main branch (currently on ${CURRENT_BRANCH})"
    echo "   Switching to main..."
    git checkout main
fi

# Check for uncommitted changes (includes untracked, staged, and modified files)
if [ -n "$(git status --porcelain)" ]; then
    echo "✓ Found uncommitted changes"
else
    echo "✗ No uncommitted changes detected"
    exit 1
fi

# Create initial feature branch (carries uncommitted changes)
echo "Creating branch: ${FULL_BRANCH_NAME}"
git town hack "$FULL_BRANCH_NAME"

# Commit changes on the new branch
echo "Committing changes..."
git add .
git commit -m "$COMMIT_MSG"

# Push branch to remote
echo "Pushing branch to remote..."
git push -u origin "$FULL_BRANCH_NAME"

# Create PR with base as main
echo "Creating pull request..."
gh pr create \
  --title "$PR_TITLE" \
  --body "$PR_BODY" \
  --base main

# Sync stack and return to main
echo "Syncing stack..."
git town sync --stack

# echo "Returning to main branch..."
# git checkout main

echo "✅ Stack created successfully!"
echo "   Branch: ${FULL_BRANCH_NAME}"
echo "   Base: main"
