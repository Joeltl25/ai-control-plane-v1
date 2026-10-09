# JOB-0002 Durable Slack Command Ingress

## Goal

Make the control plane usable from the phone without ChatGPT staying open.

The durable route must support:

- `STATUS`
- `PLAN`
- `APPROVE PLAN`
- `CODE`
- `APPROVE`
- `RETRY`
- `SWITCH`
- `RESET`
- `STOP`
- `DISCUSS`

## Current verified state

JOB-0001 is complete:

- PR: https://github.com/Joeltl25/ai-control-plane-v1/pull/1
- Merge SHA: `4415bd73e1d6c5e8e799d0901eee62cb3fcb7d64`
- `main/file.txt` is verified as `Banana`

VPS runtime path:

```text
/home/aioperator/control-plane/runtime/control-plane-v1
```

Files created on the VPS:

```text
command-contract.md
run-final-selfcheck.sh
next-ticket-template.json
build-status.json
status-v1.sh
watchdog-gateway.sh
pr-readiness.sh
```

## Current blocker

The durable Slack listener is not active because the reusable Slack credential is not installed inside the worker boundary.

Expected secure file path:

```text
/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret
```

Do not paste the secret in chat. Install it directly on the VPS or use a managed credential route.

## Safe activation rule

Do not expose a public route until the listener can verify Slack signatures.

The gateway should remain localhost-only until:

1. Slack signing verification works.
2. Approved user and channel checks work.
3. Audit logging works.
4. The public route is explicitly approved.

## Preferred route

```text
Slack slash command
→ approved public route
→ VPS localhost gateway on 127.0.0.1:8798
→ Slack signature verification
→ Airtable job/ticket/lease updates
→ VPS worker execution
→ GitHub PR
→ Slack PR link
→ approved merge
```

## Fallback route

Use n8n or Airtable webhook only if it can verify the caller and avoids the current n8n Basic Auth webhook block.

## Stop rules

Stop before:

- exposing a public endpoint without approval;
- storing or printing raw secrets;
- running commands without Slack signature verification;
- merging any PR without expected head SHA protection;
- touching Windows SuSocial or unrelated production assets.
