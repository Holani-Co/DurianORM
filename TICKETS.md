# Tickets: fix, release, check

This file is for whoever picks up a client ticket in this repo, a person or an unattended Claude session (the ticket desk). If a step says to stop and nobody is there to ask, finish the ticket as blocked and say what is needed.

Facts here were checked on 21 Nov 2026 against the live box (read only) and GitHub. Anything not checked says `NOT VERIFIED`.

## 1. What it is and where it runs

- ORM is Durian's customer-messaging desk (WhatsApp, Instagram, email, Google reviews). It is a fork of Chatwoot (`chatwoot/`, Ruby on Rails) plus a helper called the Zoho bridge (`zoho-bridge/`, Python FastAPI) and the ORM MCP server (`mcp_server/`, Python).
- Live URL: https://orm.durianos.in (login page `/app/login`). People sign in from Durian OS.
- Server: 168.144.191.190. SSH: `ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190`. Run as root.
- Folder on the server: `/root/DurianORM`. It is a git checkout on branch `main`. On 21 Nov 2026 it was at `6e136eb2a`, the same commit as GitHub `main` (`Holani-Co/DurianORM`).
- Processes (systemd, all start on boot):
  - `chatwoot-web`: Rails (Puma) on 127.0.0.1:3000, log `/var/log/chatwoot-web.log`.
  - `chatwoot-worker`: Sidekiq jobs, log `/var/log/chatwoot-worker.log`.
  - `zoho-bridge`: FastAPI on 127.0.0.1:8420, log `/var/log/zoho-bridge.log`. Reads `zoho-bridge/.env` and the routing file `zoho-bridge/routing_rules.prod-test.yaml`.
  - `orm-mcp`: MCP server on 127.0.0.1:8432. It sees `/root/DurianORM/mcp_server` through a read-only mount, so a restart is enough to load new code.
  - `orm-support`: the support button helper, runs from `/opt/orm-support/server.py`. It is NOT in this repo (NOT VERIFIED where its source lives).
  - `nginx` (site `orm.durianos.in`), `postgresql`, `redis-server`.
- Ruby is `/root/.rbenv/versions/3.4.4/bin/ruby`. Node is under `/root/.nvm`. Use `bash -l` for anything that runs bundle or pnpm.
- The server's git remote is `https://github.com/singhaditya28/DurianORM.git`. GitHub forwards that name to `Holani-Co/DurianORM` and it works (fetch gives the same commits). Do not change it without being asked.
- The box keeps old edits of its own. Leave them alone: deleted `chatwoot/log/.keep` and `chatwoot/tmp/.keep`, and a changed `zoho-bridge/routing_rules.prod-test.yaml`. There are also many untracked `.env.bak*` files. Never delete, reset or commit these. Never print a `.env` value.
- A daily state backup lands in `/root/backups/orm-state-<date>.tgz`.

## 2. Run it or reproduce a problem

Most tickets are best reproduced by reading, not by running a copy.

1. Read the live logs (read only):
   `ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190 'tail -n 200 /var/log/chatwoot-web.log'`
   Same for `chatwoot-worker.log` and `zoho-bridge.log`. Search with `grep -n "<conversation id or error>"`.
2. Read the data through ORM's own tools (Durian OS Connect, the `orm__` tools) when you have them. Reads are safe. Do not use write tools on customer conversations to "test".
3. Read the docs: `HANDOFF.md`, `zoho-bridge/README.md`, `zoho-bridge/HANDOFF.md`, `docs/mcp/MCP.md` (sections 14 and 15 are the test and release notes), `chatwoot/AGENTS.md`.
4. To run the Rails specs on a laptop you need Docker. `docs/mcp/MCP.md` section 14 has the exact `docker run` commands (a throwaway Postgres and Redis, Ruby 3.4.4). Do not run specs or tests on the production box.
5. Zoho bridge on a laptop: `cd zoho-bridge`, make a Python 3.11 venv, `pip install -r requirements.txt`, `uvicorn main:app --host 127.0.0.1 --port 8420`. It needs a `.env` (see the README). Never point a laptop copy at live customer accounts.
6. Where things are: sending and receiving messages, inboxes, labels, UI: `chatwoot/app/...`. Our own additions are mostly under `chatwoot/**/custom` and `chatwoot/spec/custom`. Email routing, AI agent replies, Zoho CRM sync, Google reviews: `zoho-bridge/`.

## 3. Checks before a release

Run what matches the files you changed. Say in the ticket which you ran.

- Ruby files (`chatwoot/**/*.rb`): the syntax check is done again on the box (step 4.6). Locally, in Docker as in MCP.md section 14, `bundle exec rspec spec/custom` (our own specs) and the spec for the file you touched, for example `bundle exec rspec spec/path/to_file_spec.rb`.
- Ruby style: `bundle exec rubocop <file>` (150 character lines). NOT VERIFIED that it is clean on this repo today.
- JavaScript or Vue (`chatwoot/app/javascript/**`): `pnpm eslint` and `pnpm test` in `chatwoot/`. NOT VERIFIED on this laptop. The build (`rails assets:precompile`) only runs on the box, so a Vue error shows up there; write plain templates (no `??` in a template, it breaks the build).
- Zoho bridge: from `zoho-bridge/`, `python -m pytest tests -q --ignore=tests/agent` (the `tests/agent` folder calls real AI models). Result when this file was written: see below.
- MCP server: from the repo folder, `pip install -r mcp_server/requirements.txt -r mcp_server/requirements-test.txt` then `python -m pytest mcp_server/tests -q`. If you add or change an API route, the tool list is checked by `spec/custom/config/mcp_registry_spec.rb` and `mcp_server/tests/test_registry.py`.
- There is no CI in this repo (the `.github` folder inside `chatwoot/` is Chatwoot's own and does not run on our GitHub). So these checks are the only gate.

Result when this file was written (21 Nov 2026, Windows laptop, Python 3.11): the Zoho bridge tests gave `73 passed` and the MCP server tests gave `56 passed`. The bridge tests need dummy values for these variables first (they only check settings are present): `ZOHO_CLIENT_ID ZOHO_CLIENT_SECRET ZOHO_REFRESH_TOKEN ZOHO_ORG_ID ZOHO_DEPARTMENT_ID CHATWOOT_API_TOKEN OPENAI_API_KEY` (any text) and `TEAM_ID_LEGAL TEAM_ID_MARKETING TEAM_ID_HR TEAM_ID_SUPPORT` (a number such as 1). On Windows also `pip install tzdata`. Rails specs and the JavaScript checks were NOT run for this file (NOT VERIFIED).

## 4. Release to live

Do this from the laptop, in Git Bash. In the commands, `SSH` means `ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190`.

1. Make sure what is live is on GitHub (so a teammate's unpushed work is not overwritten).
   ```
   git fetch origin
   LIVE=$(ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190 'git -C /root/DurianORM rev-parse HEAD')
   git merge-base --is-ancestor "$LIVE" origin/main && echo "OK: live is on GitHub" || echo "STOP"
   git log --oneline "$LIVE"..origin/main      # commits that will go live together with yours
   ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190 'git -C /root/DurianORM status --short | grep -v "^??"'
   ```
   - If it says STOP, the box has a commit GitHub does not have. Stop, do not release, finish the ticket as blocked and name the commit (`git -C /root/DurianORM log -3` on the box).
   - The last command must print only the three known edits (`D chatwoot/log/.keep`, `D chatwoot/tmp/.keep`, `M zoho-bridge/routing_rules.prod-test.yaml`). Any other line means someone edited files on the box by hand: stop and finish as blocked.
   - Read every commit in the `git log` list. They go live with yours.
2. Make the fix on a new branch from `origin/main`, in a separate worktree folder (never switch the branch of the main checkout): `git worktree add -b fix/<ticket> "<folder>/.wt/<ticket>" origin/main`. Run the checks in part 3. Commit.
3. Merge and push to GitHub ourselves, no pull request:
   ```
   git fetch origin
   git rebase origin/main          # or merge it; fix any conflict, re-run the checks
   git push origin HEAD:main       # must be a plain fast-forward; never use --force
   ```
4. On the box. Open a shell and set the names:
   ```
   ssh -i ~/.ssh/miraclekey -o IdentitiesOnly=yes root@168.144.191.190
   cd /root/DurianORM
   OLD=$(git rev-parse HEAD)
   git tag pre-<ticket>-$(date +%Y%m%d-%H%M)      # the rollback point
   git fetch origin main
   git diff --name-only $OLD origin/main           # read this list: it decides step 5
   git merge --ff-only origin/main                 # if this fails, stop (do not use reset or --force)
   ```
   The box's own edits stay untouched by a fast-forward. If it refuses because a changed file would be overwritten, stop and finish as blocked.
5. Decide what else is needed, from the file list in step 4:

   | A changed file is under | Also do |
   |---|---|
   | `chatwoot/Gemfile.lock` | new gems: use `redeploy.sh` (see below) instead of steps 6 and 7 |
   | `chatwoot/app/javascript/`, `chatwoot/app/assets/`, `package.json`, `pnpm-lock.yaml`, `vite.config.*`, `tailwind.config.js` | asset build (`rails assets:precompile`): use `redeploy.sh`. Changed locale `.yml` files under `chatwoot/config/locales` need only a restart |
   | `chatwoot/db/migrate/` | migration: use `redeploy.sh`, after a database dump (below) |
   | `chatwoot/app/jobs`, `app/services`, `app/models`, `app/listeners`, `lib`, `config/initializers`, `enterprise/` or anything a background job uses | restart `chatwoot-worker` too |
   | `chatwoot/app/controllers`, `app/views`, `config/routes.rb` only | restarting `chatwoot-web` is enough (if unsure, restart both) |
   | `zoho-bridge/**` | `systemctl restart zoho-bridge` (new Python packages: `/root/DurianORM/zoho-bridge/venv/bin/pip install -r zoho-bridge/requirements.txt` first) |
   | `mcp_server/**` | `systemctl restart orm-mcp` (new packages: `/opt/orm-mcp/venv/bin/pip install -r /root/DurianORM/mcp_server/requirements.txt` first) |
   | `docs/` or `*.md` only | nothing to restart |

   `redeploy.sh` (in the repo root) does: `git pull`, bundle install, pnpm install, asset build while the site stays up (it stops with the old build live if the build fails; it refuses to start if RAM plus free swap is under 5 GB), `db:migrate`, restart of web, worker, zoho-bridge and orm-mcp, then a health check. Its `git pull` is a merge; since you already fast-forwarded in step 4 it adds nothing. Run it as `bash -l -c './redeploy.sh'` from `/root/DurianORM`. Expect several minutes. The site keeps serving during the build and is down only for the restart.
   Database dump before any migration (the daily backup is files, not a dump): `cd /root/DurianORM/chatwoot && set -a && . ./.env && set +a && PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h "$POSTGRES_HOST" -U "$POSTGRES_USERNAME" -Fc "$POSTGRES_DATABASE" > /root/backups/orm-pre-<ticket>.dump`. NOT VERIFIED that this exact command runs (not run, it writes a file). A migration must be additive (new column or table), because the old code is still running during the build.
6. Check Ruby syntax of each changed `.rb` file (the tool is not on the PATH): `for f in $(git diff --name-only $OLD HEAD | grep '\.rb$'); do /root/.rbenv/versions/3.4.4/bin/ruby -c "$f"; done`. Every line must say `Syntax OK`. If not, roll back (below) before restarting.
7. Restart: `systemctl restart chatwoot-web` (back in about 12 seconds), and `systemctl restart chatwoot-worker` if step 5 says so. Then part 5.

Rollback (quickest first):
- Code only: `cd /root/DurianORM && git reset --keep <the pre-<ticket> tag>` (it keeps the box's own edits and refuses if it would overwrite them), then restart what you restarted. If the release changed JavaScript or Vue, the old screens need `redeploy.sh`-style asset build again, because the build output is not in git.
- A migration cannot be undone by git. Restore only from the dump, and only with a person's say-so. In an unattended session, stop and finish as blocked.
- For `zoho-bridge`, `orm-mcp`: reset the code the same way and restart that service.
- Tags already on the box look like `pre-orm-ai-writes-20261110-2138`; use the same style.

## 5. Check on live

1. On the box:
   ```
   systemctl is-active chatwoot-web chatwoot-worker zoho-bridge orm-mcp nginx
   curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:3000/app/login     # 200
   curl -s -o /dev/null -w '%{http_code}\n' 127.0.0.1:3000/health        # 200
   curl -s 127.0.0.1:8420/health                                         # {"ok":true}
   tail -n 50 /var/log/chatwoot-web.log                                   # no new errors
   tail -n 50 /var/log/chatwoot-worker.log
   ```
2. From the laptop (use `/c/Windows/System32/curl.exe`; plain `curl` in Git Bash failed here): `curl.exe -s -o NUL -w "%{http_code}" https://orm.durianos.in/app/login` gives 200, and `https://orm.durianos.in/health` gives `{"status":"woot"}`.
3. What a signed-in person sees: they open https://orm.durianos.in from Durian OS and land on the inbox, `/app/accounts/1/dashboard` (account 1 per `docs/mcp/MCP.md`). Open a conversation that shows the thing you fixed. A person who works the desk can confirm. You cannot sign in as a person without them; never enter a password for them.
4. Tell the ticket what you checked and the commit that is now live: `git -C /root/DurianORM log -1 --oneline` on the box.

## 6. Be careful with

- This is live. Customer Support uses it daily; real customers are waiting in WhatsApp, Instagram, email and Google reviews. Releasing in office hours drops the page for about 12 seconds and logged-in people may need to refresh. Prefer a quiet time for anything with a build or migration.
- Never send a message, run a campaign, change a conversation or a contact "to test". Test with the specs or on a copy.
- The Zoho bridge talks to Zoho CRM and to Chatwoot with real credentials. It also replies to customers on its own (email acknowledgements, AI agent replies) and changes the routing of email. `zoho-bridge/routing_rules.prod-test.yaml` is the live routing file and the box version differs from git on purpose. Do not "fix" it by overwriting.
- The bridge has Google review polling and a state file (`zoho-bridge/website_reviews_state.db`, `review_dates.json`, untracked). Do not delete them.
- The Sidekiq worker runs jobs for every inbox. A bad change in a model, service or job can stop messages everywhere, not just on one screen.
- ORM is used by other apps: Durian OS signs people in and manages their access, the ORM MCP server gives AI tools access to it (`orm__...`), and Work OS has a support button (`orm-support`). Do not change sign-in, the `/api/v1/ai/...` routes, `/platform/` or token handling for a small ticket. A new API route needs the registry updated (part 3).
- nginx limits some paths by IP (`/platform/`, `/mcp`, `/api/support/snapshot/`). Do not edit the nginx site for a ticket.
- The box has 16 GB RAM and 4 GB swap. Do not start a second asset build while one runs.
- Secrets sit in `.env` files and in systemd unit files on the box. Never paste them into a ticket, commit or log.
- One thing is NOT VERIFIED: how `orm-support` (`/opt/orm-support/server.py`) is released. It is not in this repo; do not touch it.
