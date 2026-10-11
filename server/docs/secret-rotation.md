# Secret Rotation

Rotate without ever printing, committing, or logging the value. Verify each step
before removing the old credential.

## Neon database password

1. In Neon, reset the role password (Console → Roles, or `neonctl`).
2. Update the Hyperdrive configuration to the new connection string:
   `npx wrangler hyperdrive update <ID> --connection-string="postgresql://...?sslmode=require"`
   (or recreate it and update the id in `cloudflare/wrangler.jsonc`).
3. Update `PRODUCTION_DATABASE_URL` (Neon direct endpoint) in the GitHub
   `production` environment secrets.
4. Redeploy / re-verify: `scripts/verify_deployment.sh <base-url>` → `/ready` ok.
5. Only then discard the old password.

## Hyperdrive configuration

- Prefer `wrangler hyperdrive update` to rotate the upstream connection string.
- If the id changes, set the new id in `cloudflare/wrangler.jsonc` and redeploy.
- Hyperdrive ids are identifiers, not secrets, but are account-specific.

## Cloudflare API token

1. Create a new token scoped to Workers (and needed account resources).
2. Update the GitHub secret `CLOUDFLARE_API_TOKEN` (and `CLOUDFLARE_ACCOUNT_ID` if it
   changed).
3. Run a deploy/smoke to confirm, then revoke the old token.

Keep caching disabled on the primary Hyperdrive configuration after rotation.
Run Wrangler commands from `cloudflare/`; there are no Wrangler environment blocks.

## GitHub Actions environment secrets

- Secret `PRODUCTION_DATABASE_URL` and the Cloudflare
  secrets live in the repo/environment settings — never in workflow source.
- Workflows reference them by name only (`${{ secrets.NAME }}`).

## Verification after any rotation

```
curl -s <base-url>/ready      # {"status":"ready"}
scripts/verify_deployment.sh <base-url>
```

## Never

- Commit, print, or log credentials; put them in `cloudflare/wrangler.jsonc`, README examples,
  or workflow source.
- Weaken the secret scanner to make a rotation pass.
- Rotate during an active competition round unless it is an emergency.
