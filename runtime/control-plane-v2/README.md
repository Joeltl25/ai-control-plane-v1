# Control Plane V2

V2 moves the live operational engine from Airtable to local SQLite on the Ubuntu VPS.

## Current slice

Implemented and running locally on the VPS:

- SQLite database at `/home/aioperator/control-plane/state/control-plane-v2/control-plane.sqlite`
- WAL mode enabled
- tables: projects, jobs, leases, approvals, workers, budgets, events, sync_outbox
- Airtable seed importer for current Projects and Jobs
- local API service on `127.0.0.1:8801`
- endpoints:
  - `GET /health`
  - `POST /bootstrap_or_continue`
  - `POST /stop`
- systemd user service: `control-plane-v2-gateway.service`
- workspace manifest example: `workspace.example.yaml`

## Safety

V1 remains live and untouched. V2 is local-only until fully tested.
Airtable is mirror/dashboard only; SQLite is the source of truth.
`STOP` currently records the emergency stop and releases the lease. The systemd worker kill hook is the next ticket.
