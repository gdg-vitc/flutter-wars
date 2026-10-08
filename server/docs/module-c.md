# Module C: Team API Keys & AppDev IDE Synchronization

## Ownership

Module C owns `team_api_key`, API-key issuance/revocation, API-key authentication, and the read-only IDE sync API. It does not own teams, inventory, catalog records, credits, or participant authentication.

Module C's schema is revision `0001_module_c` in the Alembic migration chain.
Run `uv run alembic upgrade head` before deploying these endpoints.

## API

`POST /admin/teams/{team_id}/api-keys` is organizer/admin-only (the verified principal is supplied by Module B). It creates a key and returns `{ "key_id", "api_key", "created_at" }`. The `api_key` value is shown only in this issuance response.

`DELETE /admin/teams/{team_id}/api-keys/{key_id}` is organizer/admin-only. It marks the matching key revoked; a revoked key immediately fails authentication.

`GET /ide/state` requires `X-Team-API-Key`, not a participant JWT. The key resolves the team; a `team_id` query parameter is ignored and cannot select another team's data. The endpoint is read-only and returns only the Module F projection:

```json
{
  "team_id": "team-001",
  "widgets": [{"widget_id": "button_primary", "quantity": 3}]
}
```

## Security

Keys use `twk_<key-id>_<random-secret>`. Only SHA-256 verification material is stored; the plaintext is never persisted or logged. Secret comparison uses constant-time comparison. The current issuance policy is one active key per team: issuing a replacement revokes the prior active key in the same transaction. API-key format, multi-key support, sync rate limits, and ETag/version behavior remain TBD with the lead.

## Integration contracts

- Module A: SQLModel session, error contract, router registration, logging hook.
- Module B: authenticated `Principal` supplied through `get_principal`; C only checks `organizer`/`admin` for lifecycle endpoints.
- Module F: provides `get_team_inventory(team_id) -> list[WidgetAllowance]`; C has no inventory table or mutation logic.
- Module D: Module F's projection must use Module D's stable widget IDs. C exposes only those IDs and quantities.
- Module L: can observe lifecycle/authentication events through the shared logging hook; C does not implement observability storage.

Until Modules B/F/D are integrated, tests inject the organizer principal and an inventory reader fake. A missing inventory reader returns safe `503 DEPENDENCY_UNAVAILABLE` rather than fabricated state.
