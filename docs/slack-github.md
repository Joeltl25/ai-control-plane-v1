# Slack and GitHub visibility

GitHub has been connected to Slack for phone visibility.

Use this for lightweight updates only:
- PR links should show clearly in Slack.
- Slack remains the phone command surface.
- GitHub Mobile remains the code review surface.
- Do not paste raw diffs into Slack.

This does not replace the durable Slack command listener.

Still required before true 24/7 Slack commands:
- Slack signing secret or managed Slack credential.
- Approved public route to the VPS gateway.
- Gateway must keep request signature checks, approved-user checks, and approved-channel checks.

Red actions still need explicit approval:
- public endpoint exposure
- adding or rotating secrets
- billing or permission changes
- destructive deletes
- production deploys
