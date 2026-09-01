# Deploying a repo to Callistra's shared infra

This is the developer-facing "how do I get my repo running" guide. For operating an
already-deployed app (logs, restart, rollback, secrets rotation), see [README.md](README.md).

## The VM

- Azure VM `callistra-combined-vm1`, public IP **`48.217.83.220`**, `Standard_D4s_v5`
  (4 vCPU / 16GB / 123GB disk), Ubuntu 24.04, admin user `azureuser`.
- Runs [Dokku](https://dokku.com) — a self-hosted, single-node "mini Heroku". Each repo
  becomes its own isolated Dokku app: its own container, its own env vars, its own
  git-push-to-deploy remote. Pushing one app never touches another.
- Not a general platform — this is internal infra for Callistra's own ~70 repos
  (ingestion workers, the agents backend, database API, integrations, etc.), not a
  product we're building for others.
- Firewall only allows 22 (SSH), 80/443 (HTTP/HTTPS). Everything else is closed.

## Why Dokku instead of Railway

Railway works, but this is meant to be a shared VM running many small independent
repos on existing cloud credits, not a per-service hosted bill. Dokku gets the same
git-push-to-deploy workflow with per-app isolation, at a fraction of the operational
surface of a real PaaS, and we don't need a dashboard — everything here is
developer/CLI-driven only.

## What every repo needs

1. **A `Dockerfile`.** We standardized on Dockerfile-based deploys (not Nixpacks/buildpack
   auto-detection) for predictability. Minimum shape for a Python worker:

   ```dockerfile
   FROM python:3.12-slim
   ENV PYTHONUNBUFFERED=1
   RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
       && rm -rf /var/lib/apt/lists/*
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   CMD ["./start.sh"]
   ```

   `ENV PYTHONUNBUFFERED=1` is not optional — see Gotcha #2 below.

2. **A `Procfile`**, one line per process type:
   - No HTTP listener (a pure background worker — most ingestion repos):
     `worker: ./start.sh`
   - Has an HTTP listener other things call into: `web: <start command>`

   Without a `Procfile`, a bare Dockerfile `CMD` is treated as a `web` process and Dokku
   expects it to bind a port — wrong for anything that's outbound-only.

3. **A `.dockerignore`** — exclude `.git/`, `.venv/`, `__pycache__/`, `.env*`, and
   anything already in `.gitignore` that isn't needed at runtime (local scratch data,
   generated CSVs, etc.).

4. **Env vars, curated — not the whole shared `.env`.** Several repos share one
   `.env` template with ~30 vars (AWS, Postgres, Azure OpenAI, Mongo, Supabase,
   Anthropic/OpenAI, etc.) — most repos only need a handful. Before setting config,
   check what the repo's own code actually reads:

   ```bash
   grep -rhoE "os\.(getenv|environ\.get|environ\[)\(?['\"][A-Z_][A-Z0-9_]*['\"]" --include="*.py" . \
     | grep -oE "['\"][A-Z_][A-Z0-9_]*['\"]" | tr -d "'\"" | sort -u
   ```

   Also grep for dynamic lookups (`os.getenv(name)` where `name` is a variable — common
   in the shared `analytics_db/db.py` module used across most ingestion repos) and read
   the surrounding code to find the real fallback var names.

## Onboarding steps

From `callistra-deployment-infra`:

```bash
./onboard-app.sh <app-name> [container-port] [domain]   # no port = outbound-only worker
./push-env.sh <app-name> <path-to-.env> VAR1 VAR2 ...    # curated vars only, never the whole file
```

Then from the app's own repo:

```bash
git remote add dokku dokku@48.217.83.220:<app-name>
git push dokku main
```

(If the repo has never been git-initialized — some haven't — `git init`, add a
`.gitignore` covering `.env*`/`.venv/`/`__pycache__/`, commit, then proceed as above.)

`git push origin main` (GitHub) and `git push dokku main` (the VM) are completely
independent remotes — pushing to one never touches the other.

## Two gotchas that look like failures but aren't

1. **First deploy of a `worker:` (non-`web`) process type runs at 0 instances.**
   Dokku defaults new non-`web` process types to zero — the build succeeds but nothing
   actually runs. Fix: `ssh azureuser@48.217.83.220 "sudo dokku ps:scale <app> worker=1"`
   right after the first push. (`onboard-app.sh` prints this reminder automatically for
   outbound-only apps.)

2. **`dokku logs` looks empty even though the container is running.** Python fully
   buffers stdout when it isn't a TTY (true inside any container) — `print()` output
   sits in a buffer indefinitely instead of flushing per line. Fixed by
   `ENV PYTHONUNBUFFERED=1` in the Dockerfile (already in the template above).

## Secrets hygiene

`dokku config:set` **echoes every value back to stdout** (unlike Heroku, which masks
it) — never run it directly where the output is visible/logged somewhere you don't
want secrets. Always use `./push-env.sh`, which redirects that output and only reports
which *keys* landed, never values. Same rule for any ad-hoc debugging: check presence/
length (`${#var}`), never `cat`/print a file containing real secrets.

## Verifying a deploy actually worked

Don't stop at "the build succeeded" — confirm the process is really running and doing
real work:

```bash
ssh azureuser@48.217.83.220 "sudo docker ps -a --filter name=<app> --format 'table {{.Names}}\t{{.Status}}'"
ssh azureuser@48.217.83.220 "sudo dokku logs <app> -n 40"
```

Look for real output specific to the app's job (a fetch count, a DB upsert count, a
cycle-complete line) — not just boot messages.
