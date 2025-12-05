#!/bin/bash
set -euo pipefail

# append-stack.sh - Append to an existing stacked PR (Phase 2+)
#
# Usage: append-stack.sh <phase-num> <branch-name> <commit-msg> <pr-title> <pr-body>
#
# Arguments:
#   phase-num          Phase number (e.g., 2)
#   branch-name        Descriptive branch name (e.g., "message-parser")
#   commit-msg         Commit message (multiline string)
#   pr-title           PR title
#   pr-body            PR body (multiline string)

PHASE_NUM=$1
BRANCH_NAME=$2
COMMIT_MSG=$3
PR_TITLE=$4
PR_BODY=$5

FULL_BRANCH_NAME="phase-${PHASE_NUM}-${BRANCH_NAME}"

echo "📚 Appending to stack: ${FULL_BRANCH_NAME}"

# Pre-flight check: Ensure not on main
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" == "main" ]; then
    echo "⚠️  Warning: Not on feature branch (currently on ${CURRENT_BRANCH})"
    echo "✗ Error: Switch to the top of the stack!"
    exit 1
fi

# Use current branch as the base for the new phase
PREV_PHASE_BRANCH="$CURRENT_BRANCH"
echo "Using current branch as base: ${PREV_PHASE_BRANCH}"

# Check for uncommitted changes (includes untracked, staged, and modified files)
if [ -n "$(git status --porcelain)" ]; then
    echo "✓ Found uncommitted changes"
else
    echo "✗ No uncommitted changes detected"
    exit 1
fi

# Append new branch on top (carries uncommitted changes)
echo "Appending branch: ${FULL_BRANCH_NAME}"
git town append "$FULL_BRANCH_NAME"

# Commit changes on the new branch
echo "Committing changes..."
git add .
git commit -m "$COMMIT_MSG"

# Push branch to remote
echo "Pushing branch to remote..."
git push -u origin "$FULL_BRANCH_NAME"

# Create PR with base as previous phase
echo "Creating pull request..."
gh pr create \
  --title "$PR_TITLE" \
  --body "$PR_BODY" \
  --base "$PREV_PHASE_BRANCH"

# Sync stack and return to main
echo "Syncing stack..."
git town sync --stack

echo "✅ Stack extended successfully!"
echo "   Branch: ${FULL_BRANCH_NAME}"
echo "   Base: ${PREV_PHASE_BRANCH}"
