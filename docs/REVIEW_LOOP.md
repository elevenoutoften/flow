# Review Loop

## Overview

Flow uses an automated review loop to ensure code quality before tasks reach `done`.

## Lifecycle

1. **Implementer** claims a task → status moves to `doing`
2. **Implementer** completes work → moves task to `review` with a handoff summary
3. **Reviewer agent** is automatically dispatched when task reaches `review`
4. **Reviewer** either:
   - Approves → moves task to `done` with a completion note
   - Rejects → moves task back to `todo` with a rejection note and `human_required=true`

## Automation Rules

| Rule | Trigger | Condition | Action | Priority |
|------|---------|-----------|--------|----------|
| Route review tasks to reviewer | `task_moved` | `status == "review"` | Dispatch `reviewer-agent` | 90 |
| Block tasks missing handoff | `task_moved` | `status == "review"` and `human_required == false` | Add warning note | 80 |
| Notify on task completion | `task_completed` | _(any)_ | Trigger notifications | 50 |
| Auto-promote backlog tasks | `task_created` | `status == "backlog"` | Move to `todo` | 10 |

## Handoff Requirements

When moving a task to `review`, the implementer should provide:
- **Summary** of what was done
- **Changed files** list
- **Tests run** and their results
- **Remaining work** if any

Tasks arriving in `review` without a handoff will receive an automated warning note.

## Rejection Flow

When a reviewer rejects a task:
1. Move task back to `todo`
2. Set `human_required = true`
3. Add a note explaining what needs to change

The task will stay in `todo` until a human or implementer addresses the feedback.

## Configuration

Rules are created during `flow-bootstrap` and can be managed through the `/api/automation-rules` endpoint. Disable or modify rules as needed for your workflow.
