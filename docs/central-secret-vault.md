# Central Secret Vault Architecture

## Problem

Saving a secret only in Mac Keychain does not satisfy 24/7 VPS work.

The VPS cannot depend on a MacBook being open, online, or available for secret transfer. Requiring a Mac bridge creates repeated delays and blocks phone-only operation.

## Decision

Use a central credential vault that both humans and machines can access.

Recommended default:

- Human access: Bitwarden app/browser extension on phone and Mac.
- Machine access: Bitwarden Secrets Manager CLI or SDK on the Ubuntu VPS through a scoped machine account.
- Runtime fallback: local VPS credential files under `/home/aioperator/worker/credentials/` only for secrets already pulled or installed.

## Required behavior

1. User saves or updates a credential once in the central vault.
2. Credential target metadata is stored in Airtable Credential Targets, without secret values.
3. VPS workers fetch required secrets from the central vault or use the cached local credential file.
4. Agents must not ask the user to paste the secret in chat.
5. Agents must not search n8n, Slack, GitHub, or Airtable for secret values.
6. If a secret is missing, alert Slack with a short action: add/update credential in the central vault.

## Why not Mac Keychain as source of truth

Mac Keychain is local to the Mac. It is fine for personal local use, but it is not a server-side runtime vault.

A Mac-based bridge only works while the Mac is reachable. That conflicts with the control-plane requirement that the laptop can stay closed.

## Security without friction

The goal is not maximum ceremony. The goal is one safe path:

- one place to save credentials,
- one map for where each credential is used,
- one VPS pull mechanism,
- no secret values in chat/logs/Slack/Airtable/GitHub.

## Implementation phases

### Phase 1: Choose central vault

Default: Bitwarden Secrets Manager if available.

Alternative: self-hosted Vaultwarden for human password management, plus a separate machine-secret layer if needed.

### Phase 2: VPS client

Install and configure the vault CLI on the Ubuntu VPS.

The VPS stores only a scoped machine credential that can read the specific project secrets it needs.

### Phase 3: Resolver

Add a `credential-resolver` command on the VPS:

```text
credential-resolver slack-signing-secret
credential-resolver airtable-pat
credential-resolver n8n-api-key
```

The resolver checks local cache first, then central vault, then exits with a clear missing-credential message.

### Phase 4: Agent rule

Every agent uses Credential Targets first. No random discovery routes.
