# AI Control Plane V1 Command Contract

This repo is the V1 proof surface for the phone-controlled AI Workspace Control Plane.

## Supported phone commands

- `STATUS`
  - Reads Airtable job state.
  - Replies with status, current step, next action, PR URL, and blocker.

- `PLAN`
  - Converts a broad request into draft coding tickets only.
  - Does not code.

- `APPROVE PLAN`
  - Moves approved draft tickets into the queue.

- `CODE`
  - Claims one Ready ticket with one timed lease.
  - Creates one branch.
  - Runs one worker only.

- `APPROVE`
  - Valid only after GitHub Mobile review.
  - Must re-fetch PR, confirm tests, and merge with expected head SHA protection.

- `RETRY`
  - Retries the same route only when attempts remain.

- `SWITCH`
  - Uses the next approved worker only after releasing or expiring the old lease.

- `RESET`
  - Returns the job to the rollback point.
  - No broad delete.

- `STOP`
  - Pauses the job safely and releases or expires its lease.

- `DISCUSS`
  - Creates a short reasoning packet for ChatGPT mobile.
  - Does not execute code.

## Required checks before any command executes

Every durable command listener must:

1. Verify Slack request signature.
2. Verify approved Slack user.
3. Verify approved Slack channel.
4. Write an audit record.
5. Read or write Airtable only inside the approved job boundary.
6. Never print secrets.
7. Never expose unrestricted shell.
8. Never merge without PR re-fetch and expected head SHA protection.

## Current hard gate

The code path exists on the VPS, but durable Slack command ingress still needs one of these:

- Slack signing secret installed on the VPS at the approved credential path, plus an approved public route to the localhost gateway.
- Or an n8n/Airtable command route that can verify the caller and is not blocked by instance Basic Auth.

Until then, ChatGPT can post Slack updates manually, but Slack commands are not yet 24/7 durable.
