# Credential Vault Rule

## Core decision

The Ubuntu VPS runtime credential vault is the source of truth for 24/7 work.

Mac Keychain is allowed as a personal/bootstrap backup, but it is not the runtime source of truth because the VPS cannot depend on a closed or offline laptop.

## Correct order for every agent

1. Check the VPS credential vault first.
2. Check the non-secret Credential Targets map for the expected purpose and destination.
3. If the credential is present, use it and do not ask the user.
4. If the credential is missing, request one approved intake action that installs it into the VPS vault.
5. After install, verify only non-secret facts: file exists, nonzero size, restrictive permissions.

## Never do this

- Do not search n8n for VPS worker secrets.
- Do not ask the user to paste raw secrets in chat.
- Do not make future 24/7 work depend on Mac Keychain or the laptop being open.
- Do not create multiple ad hoc bridge methods.
- Do not print secret values in logs, Slack, Airtable, GitHub, or chat.

## Runtime vault path

```text
/home/aioperator/worker/credentials/
```

Project-specific credentials live under subfolders, for example:

```text
/home/aioperator/worker/credentials/ai-control-plane/slack-signing-secret
```

## Best permanent model

The VPS must have direct access to runtime credentials through one of these approved patterns:

1. Local VPS credential files under `/home/aioperator/worker/credentials/`.
2. A cloud secret manager with a VPS service credential.
3. A phone-approved one-time credential intake that writes into the VPS vault.

Mac Keychain sync is not a valid 24/7 runtime path unless a Mac service is guaranteed to stay online, which is not allowed for this project.
