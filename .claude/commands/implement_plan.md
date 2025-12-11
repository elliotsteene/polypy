---
description: Implement technical plans from thoughts/shared/plans with verification
---

# Implement Plan

You are tasked with implementing an approved technical plan from `thoughts/shared/plans/`. These plans contain phases with specific changes and success criteria.

## Getting Started

When given a plan path:
- Ensure that the latest version of the plan is loaded - run `just hl-sync`
- Read the plan completely and check for any existing checkmarks (- [x])
- Read the original ticket and all files mentioned in the plan
- **Read files fully** - never use limit/offset parameters, you need complete context
- **Check current git branch** - determine if you're resuming an in-progress stack or starting fresh
- Understand the stacked PR workflow: each phase becomes one PR in a stack
- Think deeply about how the pieces fit together
- Create a todo list to track your progress
- Start implementing if you understand what needs to be done

If no plan path provided, ask for one.

### Understanding the Stacked PR Workflow

This plan is designed for stacked PRs where:
- Each phase becomes one branch and one PR
- Phase 1 is based on `main`
- Phase N+1 is based on the Phase N branch
- After completing each phase, you'll use the `stacked-pr` skill to create the PR
- Each PR can be reviewed independently while maintaining stack context

## Implementation Philosophy

Plans are carefully designed, but reality can be messy. Your job is to:
- Follow the plan's intent while adapting to what you find
- Implement each phase fully before moving to the next
- Verify your work makes sense in the broader codebase context
- Update checkboxes in the plan as you complete sections

When things don't match the plan exactly, think about why and communicate clearly. The plan is your guide, but your judgment matters too.

If you encounter a mismatch:
- STOP and think deeply about why the plan can't be followed
- Present the issue clearly:
  ```
  Issue in Phase [N]:
  Expected: [what the plan says]
  Found: [actual situation]
  Why this matters: [explanation]

  How should I proceed?
  ```

## Context Efficiency (CRITICAL)

**Always prioritize context efficiency** when running verification commands.

Context tokens are precious - successful test runs should show a ✓, not 200+ lines of output. Only failures should show full details.

**Quick rules:**
- ✅ Use `just check-test` for verification (shows ✓ on success)
- ✅ Use `just tests` or `just test <path>` for testing
- ✅ Use `just check` for linting
- ❌ Never use raw `uv run pytest` or `uv run ruff` commands
- ❌ Don't let verbose output consume context tokens

**Why:** Verbose output wastes 2-3% of context per test run. Over multiple iterations, this pushes agents into "dumb zone" where important context gets dropped. The real cost is human time when agents lose context.

## Verification Approach

After implementing a phase:
- Run the success criteria checks (`just check-test` covers everything)
- Fix any issues before proceeding
- Update your progress in both the plan and your todos
- Check off completed items in the plan file itself using Edit
- **Pause for human verification**: After completing all automated verification for a phase, pause and inform the human that the phase is ready for manual testing. Use this format:
  ```
  Phase [N] Complete - Ready for Manual Verification

  Automated verification passed:
  - [List automated checks that passed]

  Please perform the manual verification steps listed in the plan:
  - [List manual verification items from the plan]

  Let me know when manual testing is complete so I can proceed to create the PR for this phase.
  ```

If instructed to execute multiple phases consecutively, skip the pause until the last phase. Otherwise, assume you are just doing one phase.

Do not check off items in the manual testing steps until confirmed by the user.

### After Manual Verification Confirmation

Once the user confirms manual verification is complete:
1. **Invoke the stacked-pr skill** to commit changes and create the PR
2. Check the current branch to determine phase type (see Phase Completion section below)
3. Extract PR context from the plan's "PR Context" section for this phase
4. After PR is created, ask if you should proceed to the next phase

## Phase Completion and Stacked PRs

Each phase you complete becomes one PR in a stack. This section explains how to use the `stacked-pr` skill to commit your changes and create PRs.

### When to Invoke the Stacked PR Skill

Invoke the `stacked-pr` skill **only** when:
- ✅ All automated verification has passed (`just check-test`)
- ✅ User has confirmed manual verification is complete
- ✅ There are uncommitted changes in the working directory
- ✅ You're ready to create a PR for this phase

**DO NOT** invoke it:
- ❌ Before verification is complete
- ❌ When there are no changes to commit
- ❌ Before user confirms manual testing

### Preparing the PR Information

Before invoking the skill, extract information from the plan:

1. **Phase Number**: From the phase heading (e.g., "Phase 1", "Phase 2")
2. **Branch Name**: Create from the phase title
   - Format: Short kebab-case description (e.g., `websocket-foundation`, `message-parser`)
   - Extract from the phase's "Descriptive Name" in the plan
3. **Commit Message**: Structured format:
   ```
   <type>: <phase-summary>

   Phase N: <detailed-description>

   Changes:
   - <key change 1>
   - <key change 2>
   - <key change 3>

   <any relevant context from the plan>
   ```
   Types: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`

4. **PR Title**: Format: `Phase N: <Descriptive Name>`
   - Example: `Phase 1: WebSocket Connection Foundation`

5. **PR Body**: Use the plan's "PR Context" section as a guide:
   ```markdown
   ## Phase N: <Title>

   ### Summary
   <1-2 sentence overview from the plan's "Purpose">

   ### Changes
   - <list key changes from the plan's "Changes Required" section>

   ### Stack Context
   <Information from the plan's "Enables" and "Builds On" fields>

   ### Stack
   <!-- branch-stack -->

   ### Verification
   - [x] All automated tests passed (`just check-test`)
   - [x] Manual verification completed
   - [x] Code follows project patterns

   ### Related
   Part of implementation plan: [plan file path]

   ---
   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   ```

### Example: Invoking the Skill

When you're ready to create the PR, simply invoke:

```
I'm going to use the stacked-pr skill to commit these changes and create the PR.
```

Then use the Skill tool with `skill: "stacked-pr"`. The skill will guide you through the process and execute the appropriate automation script.

### What Happens After PR Creation

After the PR is created:
1. The skill automatically syncs the git-town stack
2. You're now on a new phase branch (e.g., `phase-1-websocket-foundation`)
3. The plan checkboxes are updated
4. You should ask the user if they want to proceed to the next phase

If proceeding to the next phase:
- You're already on the correct branch (the previous phase branch)
- Start implementing the next phase
- When done, the skill will automatically detect you're on `phase-N-*` and use `append-stack.sh`

### Handling Fixes to Earlier Phases

If you need to fix something in an earlier phase:

1. **STOP implementation of current phase**
2. **Inform the user**:
   ```
   I've discovered an issue in Phase [N] that needs to be fixed before continuing.

   Issue: [description]
   Impact: [how it affects current/future phases]

   I recommend:
   1. Checkout phase-N-<name> branch
   2. Make the fix
   3. Re-run verification
   4. Sync the stack to propagate changes forward

   Should I proceed with this fix?
   ```
3. **After user approval**, checkout the earlier phase branch:
   ```bash
   git checkout phase-N-<name>
   ```
4. **Make the fix**, commit it normally (not via stacked-pr skill)
5. **Sync the stack** to propagate changes:
   ```bash
   git town sync --stack
   ```
6. **Return to current phase** and continue

## If You Get Stuck

When something isn't working as expected:
- First, make sure you've read and understood all the relevant code
- Consider if the codebase has evolved since the plan was written
- Present the mismatch clearly and ask for guidance

Use sub-tasks sparingly - mainly for targeted debugging or exploring unfamiliar territory.

## Resuming Work

When resuming work on a plan that has some phases already completed:

### Step 1: Check Current Branch

Run `git branch --show-current` to see where you are:
- **On `main`**: Starting fresh, no phases implemented yet
- **On `phase-N-<name>`**: Phase N is complete, Phase N+1 is next
- **On a feature branch**: Not in stacked PR mode, check with user

### Step 2: Review Plan Checkmarks

If the plan has existing checkmarks:
- Trust that completed work is done
- Pick up from the first unchecked phase
- Verify previous work only if something seems off
- Don't re-implement completed phases

### Step 3: Verify Stack State

Check if PRs exist for completed phases:
```bash
git branch -a | grep phase-
gh pr list --search "Phase" --state open
```

This helps you understand:
- Which phases have been implemented
- Which PRs are already created
- Where to resume work

### Step 4: Resume Implementation

- Start implementing the first uncompleted phase
- Follow the normal workflow (implement → verify → manual test → stacked-pr skill)
- The skill will automatically detect your current branch and use the correct script

Remember: You're implementing a solution, not just checking boxes. Keep the end goal in mind and maintain forward momentum.
