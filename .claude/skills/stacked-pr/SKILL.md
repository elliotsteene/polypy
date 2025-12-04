---
name: stacked-pr
description: Create and update stacked pull requests. Use this after completing an implementation phase to commit changed files.
---

# Stacked PR Workflow

This skill enables Claude to create stacked pull requests using git-town, where each phase of a plan becomes an atomic PR that builds on previous phases.

## When to Use This Skill

Invoke this skill after completing a phase from `/implement_plan` when:
1. All automated verification has passed (`just check-test`)
2. User has confirmed manual verification is complete
3. There are uncommitted changes ready to be stacked

DO NOT use this skill:
- Before verification is complete
- When there are no changes to commit
- For non-plan-based work (use standard git workflow instead)

## Pre-Flight Checks

Before creating the stack, verify:
1. Current git status shows modified/new files
2. All tests and checks have passed
3. You know the phase number and plan context

## Workflow Instructions

### Step 1: Assess Current Stack State

First, determine if this is the first phase or a subsequent phase:

```bash
# Check current branch
git branch --show-current

# Check if there are existing feature branches for this plan
git branch --list | grep -E "phase-[0-9]+"
```

**If on `main` branch**: This is Phase 1, start a new stack
**If on a `phase-N` branch**: This is a subsequent phase, append to stack

### Step 2: Create or Extend Stack

**For Phase 1 (starting new stack):**
```bash
# Create initial feature branch
git town hack phase-1-<descriptive-name>
```

**For Phase 2+ (extending stack):**
```bash
# Ensure you're on the previous phase branch first
git checkout phase-<N-1>-<name>

# Append new branch on top
git town append phase-<N>-<descriptive-name>
```

Branch naming convention: `phase-N-<short-description>`
- Example: `phase-1-websocket-foundation`
- Example: `phase-2-message-parser`
- Example: `phase-3-orderbook-state`

### Step 3: Create Commit

Create a meaningful commit message following this format:

```
<type>: <phase-summary>

Phase N: <detailed-description>

Changes:
- <key change 1>
- <key change 2>
- <key change 3>

<any relevant context or notes>
```

Types: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`

**Commit the changes:**
```bash
git add .
git commit -m "$(cat <<'EOF'
<your commit message here>
EOF
)"
```

### Step 4: Create Pull Request

Use GitHub CLI to create the PR with proper context:

```bash
gh pr create \
  --title "Phase N: <clear title>" \
  --body "$(cat <<'EOF'
## Phase N: <Title>

### Summary
<1-2 sentence overview of what this phase accomplishes>

### Changes
- <key change 1>
- <key change 2>
- <key change 3>

### Stack
<!-- branch-stack -->

### Verification
- [x] All automated tests passed (`just check-test`)
- [x] Manual verification completed
- [x] Code follows project patterns

### Related
Part of implementation plan: [link to plan if available]

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" \
  --base <base-branch>
```

**Base branch logic:**
- **Phase 1**: `--base main`
- **Phase 2+**: `--base phase-<N-1>-<name>` (base on previous phase)

### Step 5: Sync and Return

After creating the PR:

```bash
# Sync the stack to ensure all branches are up to date
git town sync --stack

# Return to main branch to prepare for next phase
git checkout main
```

## Example Workflows

### Example 1: Starting a New Stack (Phase 1)

```bash
# User confirms: "Manual verification complete for Phase 1"

# 1. Check status
git status
# Shows: modified files in src/connection/ (uncommitted)

# 2. Create initial stack branch (carries uncommitted changes)
git town hack phase-1-websocket-foundation

# 3. Commit changes on the new branch
git add .
git commit -m "$(cat <<'EOF'
feat: Add WebSocket connection foundation

Phase 1: Implement core WebSocket connection logic

Changes:
- Add WebsocketConnection class with lifecycle management
- Implement exponential backoff reconnection strategy
- Add connection health monitoring
- Create connection stats tracking

Establishes the foundation for real-time market data streaming.
EOF
)"

# 4. Create PR
gh pr create \
  --title "Phase 1: WebSocket Connection Foundation" \
  --body "$(cat <<'EOF'
## Phase 1: WebSocket Connection Foundation

### Summary
Implements the core WebSocket connection class with automatic reconnection, health monitoring, and stats tracking.

### Changes
- Add WebsocketConnection class with lifecycle management
- Implement exponential backoff reconnection strategy (1s → 30s)
- Add connection health monitoring (60s silence detection)
- Create connection stats tracking

### Stack
<!-- branch-stack -->

### Verification
- [x] All automated tests passed (`just check-test`)
- [x] Manual verification: Connected to Polymarket API successfully
- [x] Code follows project patterns

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" \
  --base main

# 5. Return to main
git town sync --stack
git checkout main
```

### Example 2: Extending the Stack (Phase 2)

```bash
# User confirms: "Manual verification complete for Phase 2"

# 1. Check current state
git branch --show-current
# Output: main

git branch --list | grep phase
# Output: phase-1-websocket-foundation

git status
# Shows: modified files (uncommitted)

# 2. Extend stack on top of Phase 1
git checkout phase-1-websocket-foundation
git town append phase-2-message-parser

# 3. Commit changes on the new branch
git add .
git commit -m "$(cat <<'EOF'
feat: Add message parsing with msgspec

Phase 2: Implement zero-copy message parser

Changes:
- Add msgspec-based message parser
- Define protocol structures for BookSnapshot, PriceChange, LastTradePrice
- Implement generator-based parsing for streaming
- Handle integer scaling for prices/sizes

Enables efficient parsing of WebSocket messages with zero-copy optimization.
EOF
)"

# 4. Create PR based on Phase 1
gh pr create \
  --title "Phase 2: Message Parser with msgspec" \
  --body "$(cat <<'EOF'
## Phase 2: Message Parser with msgspec

### Summary
Implements zero-copy message parsing using msgspec, enabling efficient processing of WebSocket events.

### Changes
- Add msgspec-based MessageParser class
- Define protocol structures (BookSnapshot, PriceChange, LastTradePrice)
- Implement generator-based parsing for streaming
- Handle integer scaling (PRICE_SCALE=1000, SIZE_SCALE=100)

### Stack
<!-- branch-stack -->

Merge order: This PR should be merged after Phase 1.

### Verification
- [x] All automated tests passed (`just check-test`)
- [x] Manual verification: Parsed live messages successfully
- [x] Code follows project patterns

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)" \
  --base phase-1-websocket-foundation

# 5. Sync and return
git town sync --stack
git checkout main
```

## Common Issues and Solutions

### Issue: Merge conflicts in stack

If changes in an earlier phase conflict with later phases:
```bash
# Sync the entire stack
git town sync --stack

# Resolve conflicts if any
# Then continue
git town continue
```

### Issue: Need to update an earlier phase

If you need to modify Phase N while on Phase N+2:
```bash
# Checkout the phase that needs changes
git checkout phase-N-<name>

# Make changes and commit
# ...

# Sync stack to propagate changes forward
git town sync --stack
```

### Issue: Wrong base branch

If PR was created with wrong base:
```bash
# Use GitHub CLI to update
gh pr edit <pr-number> --base <correct-base-branch>
```

## Key Principles

1. **One phase = One PR**: Each phase should be atomic with passing CI
2. **Sequential dependencies**: Phase N+1 builds on Phase N
3. **Clear commit messages**: Reference phase number and provide context
4. **Proper base branches**: Phase 1 → main, Phase N → Phase N-1
5. **Always sync**: After creating PRs, sync stack to keep branches updated
6. **Return to main**: After stacking, return to main for next phase work

## Integration with /implement_plan

This skill integrates with the `/implement_plan` workflow:

1. `/implement_plan` implements a phase
2. Automated verification runs (`just check-test`)
3. Claude pauses for manual verification
4. User confirms: "Manual verification complete"
5. **Claude invokes stacked-pr skill**
6. Skill creates commit, stacks PR, returns to main
7. Ready for next phase

## Notes

- This workflow uses `git-town` for stack management
- PRs are created using GitHub CLI (`gh`)
- The main branch is `main` (configured in git-town)
- Stack branches should be merged in order (Phase 1, then 2, then 3, etc.)
- After all phases are merged, the stack is automatically cleaned up by git-town
