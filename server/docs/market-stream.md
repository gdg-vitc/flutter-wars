# Market caching and live updates

Public market summaries, current rounds, listing quotes, and stream snapshots use a bounded per-process cache (256 entries, at most two seconds). Quote entries expire earlier at their earliest `valid_until`. Each lookup checks PostgreSQL's current transaction snapshot; a committed write changes that version, including writes from another Worker. Cache fills check the version again before publishing. Transactions with writes, pending ORM changes, nested transactions, or stronger isolation bypass the cache. Mutation services always read authoritative state.

A warm read still makes one database version query. Cold reads add two version queries. Any database write conservatively invalidates cached versions, so busy workloads can have fewer hits. This avoids a shared version-row write lock. Hyperdrive query caching must remain disabled so the freshness check reaches PostgreSQL. Cache values are copied and `server_time` is refreshed for each response.

## Client protocol

1. Authenticate normally and POST `/market/rounds/{round_id}/stream-ticket` with the Bearer token.
2. Open the returned `path` using WebSocket subprotocols `flutter-wars.v1` and `ticket.<ticket>`. Tickets expire within 30 seconds and are scoped to one round. Credentials never go in the URL.
3. Read `market_snapshot` messages containing `round_id`, `status`, `revision`, `sequence` (Worker transport), `server_time`, `valid_until`, and listing price/stock snapshots. Reconnects receive a complete current snapshot; no replay log is required.
4. Derive countdowns from `valid_until - server_time`, adjusting for elapsed client time. Fetch REST quotes again when reconnecting or when the deadline passes without a new snapshot. Trading still uses the normal REST endpoints and authoritative server checks.

The Cloudflare Worker uses one SQLite-backed Python Durable Object per round. Hibernating sockets retain their authentication attachments. Successful committed mutation responses trigger a best-effort refresh; failed requests do not. Alarms refresh at the next price deadline, session expiry, or within five seconds to recover a missed notification. Current membership is rechecked in a batched query before broadcasting; revoked connections close with code 1008. Each object admits up to 128 connections; clients denied admission should use REST polling. Send `ping` for an application `pong` with server time.

Local ASGI development exposes the same ticket and socket paths, refreshing once per second. Production Worker delivery uses alarms and persistent sequence numbers. Prices and public stock are broadcast; private bids and player data are not included.

Validation includes PostgreSQL cache concurrency/rollback tests, authenticated ASGI socket tests, Durable Object hibernation/alarm tests, and a real local workerd smoke check. Local validation does not deploy to staging or production.
