# Deploying OMP

## First run

```bash
cp .env.docker.example .env.docker
python -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY
# fill in DB_USER (not root), DB_PASSWORD, DB_ROOT_PASSWORD, first-run logins

docker compose --env-file .env.docker up -d --build
docker compose --env-file .env.docker logs -f app          # watch it provision
```

`.env` and `.env.docker` are separate files. `.env` points at a MySQL on
localhost as root, which is right for `python app.py` and wrong for the
container — the app reaches MySQL as host `db`, and the mysql image exits on
boot if `MYSQL_USER` is root. Compose reads `.env` automatically, so the
`--env-file` flag is what keeps the two apart. Every `docker compose` command
below needs it.

The app comes up on `127.0.0.1:8000`. It is bound to loopback on purpose — put
nginx or Caddy in front for TLS rather than exposing it to the network.

Once HTTPS is terminating in front, set `SESSION_COOKIE_SECURE=true` and
restart. Not before: the browser drops a secure cookie sent over plain HTTP
and nobody can sign in.

## What the entrypoint does

1. Waits for MySQL to answer on its port, up to `DB_WAIT_SECONDS` (60).
2. Imports the app once, which creates the database, the tables, any columns
   added to the DDL since, any widened primary keys, and the default logins.
   Idempotent, so a restart is safe.
3. Starts gunicorn with `WEB_CONCURRENCY` workers and a 120 s timeout — the
   allocation check is CPU-bound over a few thousand rows and a request can
   legitimately take seconds.

## Volumes — do not skip these

| Path | Why |
|---|---|
| `./config` | The allocation, max loss, cycle and strategy tag rules are **edited from Admin Controls at runtime**. Bind-mounted from the host so a rebuild does not throw away every rule change. |
| `omp-logs` | `logs/omp.log`. |
| `omp-data` | Uploaded sample files. |
| `db-data` | MySQL. |

`config` is a bind mount rather than a named volume so the rules can be read,
backed up and diffed from the host.

## Backups

`config/allocation_rules.json` and the MySQL volume are the two things worth
backing up. The rules file is small and changes rarely:

```bash
docker compose --env-file .env.docker exec -T db \
  mysqldump -u root -p"$DB_ROOT_PASSWORD" omp \
  | gzip > "omp-$(date +%F).sql.gz"
cp config/allocation_rules.json "rules-$(date +%F).json"
```

## Upgrading

```bash
git pull
docker compose --env-file .env.docker up -d --build
```

New columns and widened keys are applied on boot. Nothing drops a column or
narrows a key automatically — those are logged for a human.

---

# Before this is genuinely production-ready

The container is production-shaped. The application still carries four things
that were deliberate decisions during development and are now risks. None is a
blocker for an internal tool on a trusted network; all of them matter if that
assumption ever stops holding.

### 1. No CSRF protection — the one I would fix first

Every state-changing endpoint accepts a plain form POST with no token. Any page
a signed-in user visits can silently submit to OMP with their cookie. That
includes **Apply selected**, which writes allocations and max losses to live
accounts.

`SESSION_COOKIE_SAMESITE=Lax` (now set) blocks the cross-site *form* case in
current browsers, which is most of the exposure. It is mitigation, not a fix.
`Flask-WTF` with `CSRFProtect(app)` plus a hidden field in each form is roughly
an hour's work.

### 2. Passwords are stored in plain text

An explicit decision — superadmin can read every password from MSUsers. It
means a database dump is a credential dump, and people reuse passwords. If that
is ever revisited, `werkzeug.security.generate_password_hash` is a small change
plus a one-off migration.

### 3. `usersetting` holds live broker credentials in plain text

API keys, API secrets, passwords, PINs and 2FA seeds. Two download buttons
export them — the per-server CSVs, available to operators for their own
servers, and the compiled workbook, admin only. Anyone who can reach the
portal can walk out with working broker credentials. Worth deciding
deliberately whether those columns need encrypting at rest.

### 4. `running_users.proxy` stores broker connection strings

Including credentials, in a table any signed-in role can read.

---

## Operational notes

- **Health**: `GET /health` returns 200 with the database name, or 503 with the
  error. The container healthcheck uses it, so a database that goes away marks
  the container unhealthy rather than leaving it silently broken.
- **Uploads** are capped by `MAX_UPLOAD_MB` (25) and rejected before being read
  into memory.
- **Logs** go to both stdout (so `docker compose logs` works) and
  `logs/omp.log`. There is no rotation — add `logrotate` on the host, or the
  file grows without limit.
- **The dev server is gone from the container path.** `python app.py` still
  works locally, and now defaults `debug` to **false**; it used to default to
  true, which would have shipped a remote code-execution console had the
  environment variable been missing.
