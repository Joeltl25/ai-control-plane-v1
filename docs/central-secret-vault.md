# Central Secret Vault Architecture

## Problem

Saving a secret only in Mac Keychain does not satisfy 24/7 VPS work.

The VPS cannot depend on a MacBook being open, online, or available for secret transfer. Requiring a Mac bridge creates repeated delays and blocks phone-only operation.

Saving credentials twice is also wrong. The user should not have to copy a secret into both Mac Keychain and the VPS.

## Decision

Use one self-hosted credential vault on the existing Ubuntu VPS.

Default: Vaultwarden, self-hosted on the existing VPS, with no new paid subscription.

Vaultwarden is Bitwarden-compatible, so humans can use Bitwarden-compatible apps/extensions on phone and Mac while the VPS keeps the runtime source available 24/7.

## Required behavior

1. User saves or updates a credential once in the self-hosted vault.
2. Credential target metadata is stored in Airtable Credential Targets, without secret values.
3. VPS workers use the local vault/resolver or cached local credential file.
4. Agents must not ask the user to paste the secret in chat.
5. Agents must not search n8n, Slack, GitHub, or Airtable for secret values.
6. If a secret is missing, alert Slack with a short action: add/update credential in the self-hosted vault.

## Why not Mac Keychain as source of truth

Mac Keychain is local to the Mac. It is fine for personal local use, but it is not a server-side runtime vault.

A Mac-based bridge only works while the Mac is reachable. That conflicts with the control-plane requirement that the laptop can stay closed.

## Security without friction

The goal is not maximum ceremony. The goal is one smooth path:

- one place to save credentials,
- one map for where each credential is used,
- one VPS resolver,
- no secret values in chat/logs/Slack/Airtable/GitHub,
- no repeated manual transfers.

## Implementation phases

### Phase 1: Self-host the vault

Install a self-hosted Vaultwarden service on the existing Ubuntu VPS.

Constraints:

- No new subscription.
- Prefer native/systemd deployment if possible.
- If a container is used, it must be explicit and isolated, not part of the normal worker runtime.
- Disable open signup after the user account is created.
- Keep backups for the encrypted vault data.

### Phase 2: Phone/Mac access

Use Bitwarden-compatible clients with the self-hosted server URL.

The user saves credentials once in this vault. Mac Keychain can remain personal backup only.

### Phase 3: VPS resolver

Add a `credential-resolver` command on the VPS:

```text
credential-resolver slack-signing-secret
credential-resolver airtable-pat
credential-resolver n8n-api-key
```

The resolver checks local cache first, then the self-hosted vault, then exits with a clear missing-credential message.

### Phase 4: Agent rule

Every agent uses Credential Targets first. No random discovery routes.

## Red gate

Creating a public vault URL, changing credential storage, or exposing a password manager service is a red action.

The safe prep work can be done automatically, but public exposure and first admin setup need explicit approval.