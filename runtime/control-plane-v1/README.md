# Runtime control plane V1

This folder stores non-secret runtime code for the phone-controlled AI Workspace Control Plane.

Current safe status:
- gateway defaults to localhost only
- Slack request signatures must verify before command execution
- approved Slack user and channel are checked
- Airtable status, approval, and lease writes are bounded to the control-plane tables
- no secrets are stored in this repo
- no public route is enabled from this repo

Red actions still require explicit approval:
- exposing a public endpoint
- adding or rotating secrets
- billing or permission changes
- destructive deletes
- production deploys
