# Chatwoot Development Guidelines

## Build / Test / Lint

- **Setup**: `bundle install && pnpm install`
- **Run Dev**: `pnpm dev` or `overmind start -f ./Procfile.dev`
- **Seed Local Test Data**: `bundle exec rails db:seed` (quickly populates minimal data for standard feature verification)
- **Seed Search Test Data**: `bundle exec rails search:setup_test_data` (bulk fixture generation for search/performance/manual load scenarios)
- **Seed Account Sample Data (richer test data)**: `Seeders::AccountSeeder` is available as an internal utility and is exposed through Super Admin `Accounts#seed`, but can be used directly in dev workflows too:
  - UI path: Super Admin → Accounts → Seed (enqueues `Internal::SeedAccountJob`).
  - CLI path: `bundle exec rails runner "Internal::SeedAccountJob.perform_now(Account.find(<id>))"` (or call `Seeders::AccountSeeder.new(account: Account.find(<id>)).perform!` directly).
- **Lint JS/Vue**: `pnpm eslint` / `pnpm eslint:fix`
- **Lint Ruby**: `bundle exec rubocop -a`
- **Test JS**: `pnpm test` or `pnpm test:watch`
- **Test Ruby**: `bundle exec rspec spec/path/to/file_spec.rb`
- **Single Test**: `bundle exec rspec spec/path/to/file_spec.rb:LINE_NUMBER`
- **Run Project**: `overmind start -f Procfile.dev`
- **Ruby Version**: Manage Ruby via `rbenv` and install the version listed in `.ruby-version` (e.g., `rbenv install $(cat .ruby-version)`)
- **rbenv setup**: Before running any `bundle` or `rspec` commands, init rbenv in your shell (`eval "$(rbenv init -)"`) so the correct Ruby/Bundler versions are used
- Always prefer `bundle exec` for Ruby CLI tasks (rspec, rake, rubocop, etc.)

## Code Style

- **Ruby**: Follow RuboCop rules (150 character max line length)
- **Vue/JS**: Use ESLint (Airbnb base + Vue 3 recommended)
- **Vue Components**: Use PascalCase
- **Events**: Use camelCase
- **I18n**: No bare strings in templates; use i18n
- **Error Handling**: Use custom exceptions (`lib/custom_exceptions/`)
- **Models**: Validate presence/uniqueness, add proper indexes
- **Type Safety**: Use PropTypes in Vue, strong params in Rails
- **Naming**: Use clear, descriptive names with consistent casing
- **Vue API**: Always use Composition API with `<script setup>` at the top

## Styling

- **Tailwind Only**:  
  - Do not write custom CSS  
  - Do not use scoped CSS  
  - Do not use inline styles  
  - Always use Tailwind utility classes  
- **Colors**: Refer to `tailwind.config.js` for color definitions

## General Guidelines

- MVP focus: Least code change, happy-path only
- No unnecessary defensive programming
- Ship the happy path first: limit guards/fallbacks to what production has proven necessary, then iterate
- Prefer minimal, readable code over elaborate abstractions; clarity beats cleverness
- Break down complex tasks into small, testable units
- Iterate after confirmation
- Avoid writing specs unless explicitly asked
- Remove dead/unreachable/unused code
- Don’t write multiple versions or backups for the same logic — pick the best approach and implement it
- Prefer `with_modified_env` (from spec helpers) over stubbing `ENV` directly in specs
- Specs in parallel/reloading environments: prefer comparing `error.class.name` over constant class equality when asserting raised errors

## Codex Worktree Workflow

- Use a separate git worktree + branch per task to keep changes isolated.
- Keep Codex-specific local setup under `.codex/` and use `Procfile.worktree` for worktree process orchestration.
- The setup workflow in `.codex/environments/environment.toml` should dynamically generate per-worktree DB/port values (Rails, Vite, Redis DB index) to avoid collisions.
- Start each worktree with its own Overmind socket/title so multiple instances can run at the same time.

## Commit Messages

- Prefer Conventional Commits: `type(scope): subject` (scope optional)
- Example: `feat(auth): add user authentication`
- Don't reference Claude in commit messages

## PR Description Format

- Start with a short, user-facing paragraph describing the product change.
- Add a `Closes` section with relevant issue links (GitHub, Linear, etc.).
- For feature PRs, add `How to test` from a product/UX standpoint.
- For bugfix PRs, use `How to reproduce` when helpful.
- Optionally add a `What changed` section for implementation highlights.
- Do not add a `How this was tested` section listing specs/commands.

## Project-Specific

- **Translations**:
  - Only update `en.yml` and `en.json`
  - Other languages are handled by the community
  - Backend i18n → `en.yml`, Frontend i18n → `en.json`
- **Frontend**:
  - Use `components-next/` for message bubbles (the rest is being deprecated)

## Ruby Best Practices

- Use compact `module/class` definitions; avoid nested styles

## Enterprise Edition Notes

- Chatwoot has an Enterprise overlay under `enterprise/` that extends/overrides OSS code.
- When you add or modify core functionality, always check for corresponding files in `enterprise/` and keep behavior compatible.
- Follow the Enterprise development practices documented here:
  - https://chatwoot.help/hc/handbook/articles/developing-enterprise-edition-features-38

Practical checklist for any change impacting core logic or public APIs
- Search for related files in both trees before editing (e.g., `rg -n "FooService|ControllerName|ModelName" app enterprise`).
- If adding new endpoints, services, or models, consider whether Enterprise needs:
  - An override (e.g., `enterprise/app/...`), or
  - An extension point (e.g., `prepend_mod_with`, hooks, configuration) to avoid hard forks.
- Avoid hardcoding instance- or plan-specific behavior in OSS; prefer configuration, feature flags, or extension points consumed by Enterprise.
- Keep request/response contracts stable across OSS and Enterprise; update both sets of routes/controllers when introducing new APIs.
- When renaming/moving shared code, mirror the change in `enterprise/` to prevent drift.
- Tests: Add Enterprise-specific specs under `spec/enterprise`, mirroring OSS spec layout where applicable.
- When modifying existing OSS features for Enterprise-only behavior, add an Enterprise module (via `prepend_mod_with`/`include_mod_with`) instead of editing OSS files directly—especially for policies, controllers, and services. For Enterprise-exclusive features, place code directly under `enterprise/`.

## Branding / White-labeling note

- For user-facing strings that currently contain "Chatwoot" but should adapt to branded/self-hosted installs, prefer applying `replaceInstallationName` from `shared/composables/useBranding` in the UI layer (for example tooltip and suggestion labels) instead of adding hardcoded brand-specific copy.

## Durian OS

ORM is one of the eight apps managed by Durian OS (durianos.in). The rules come from the Durian OS app contract (v2), section 8.3 and the ORM appendix 10.8. Read them before touching sign-in, users, memberships or tokens.

**The rule: never add a separate login, role list or user directory here.** Durian OS is the only sign-in, and it holds the list of people. A person's access lives in Chatwoot's own rows (one row per access) and Durian OS edits those same rows through Chatwoot's own APIs.

### How Durian OS talks to ORM

- ORM serves no `/os/v2` API. A connector inside Durian OS reads and writes through Chatwoot's Account API (`ORM_ACCOUNT_TOKEN`, the "Durian OS" administrator service user) and Platform API (`ORM_PLATFORM_TOKEN`). The proxy serves `/platform/` only to Durian OS's server.
- What the connector reads and writes, per person in account 1 (`OS_ACCOUNT_ID`):
  - `localUserId` = `users.id`; the link is `users.custom_attributes.os_person_id` (unique index `index_users_on_os_person_id`), written only by the Platform update;
  - `custom_attributes.os_kind`: `service` or `emergency` (absent = person); `custom_attributes.os_offboarded_at`;
  - role = `account_users.role` (`agent` | `administrator`; administrator is the admin role), inboxes = `inbox_members`, teams = `team_members`, custom role = `account_users.custom_role_id`;
  - state: `active` while the account-1 `account_users` row exists, `offboarded` if not and `os_offboarded_at` is set, else `inactive`;
  - version = first 16 base64url chars of SHA-256 of `state|role|custom_role_id|sorted inbox ids|sorted team ids` (ids comma-joined, empty custom role = empty string): `DurianOs::AccessSnapshot`.
- There are no personal-token endpoints: ORM's description has no `tokens` section. Each user's Chatwoot API token is rotated by removal and by end-sessions.
- Endpoints added for the connector (Platform token, `custom/app/controllers/platform/api/v1/durian_os_controller.rb`):
  - `GET /platform/api/v1/durian_os/status` returns `{"emergencyLogin":{"enabled":bool}}`;
  - `POST /platform/api/v1/durian_os/users/:id/end_sessions` clears `users.tokens`, rotates the API token, returns `{sessionsEnded, tokensRevoked}`.

### How sign-in works

- Opening ORM from Durian OS: Durian OS asks `GET /platform/api/v1/users/:id/login` for a one-time link and redirects to `/app/login?email=…&sso_auth_token=…`. The token is consumed with a single Redis `DEL`, so it works once (5 minutes, 256 bits). That page is sent `no-store` / `no-referrer`.
- `OS_SIGNIN_ONLY=on` (unset by default) makes Durian OS the only way in: password sign-in, MFA posts, password reset, sign-up (`/auth` registrations route), confirmation sign-in, Google/SAML (they redirect to `OS_HANDOFF_URL`, default `https://durianos.in/api/handoff/orm`) and the super admin console all answer `404`; `/app/login` without a token redirects to Durian OS (a one-minute cookie stops loops); the emergency user's API token is refused. Undo = unset it and restart `chatwoot-web` and `chatwoot-worker`.
- The emergency admin login (3.10) is off unless `OS_EMERGENCY_LOGIN_UNTIL` is a UTC time in the future and at most 24 hours ahead, with `OS_EMERGENCY_EMAIL` and `OS_EMERGENCY_PASSWORD_HASH` (bcrypt, cost ≥ 12) set. Only that email may use the password form (`/app/login?emergency=1`) and, with `OS_SIGNIN_ONLY` on, the super admin console. The password is checked against the hash, never the database; 5 failures per 15 minutes per address and 20 in total lock it until the setting changes. Sessions last one hour and end when the setting goes off. Every use is logged, audited and reported (`emergency_login.used`). While off, the emergency email gets `404`.
- `OS_MANAGED=on` (unset by default): Remove agent keeps the user row (no `delete_user_record`); service and emergency rows cannot be edited or removed on the agents page; `Agents::DestroyJob` skips a person who is a member again, and ends sessions and rotates the API token once the person has no account left.

### Change reports

`DurianOs::Reportable` hooks `AccountUser`, `InboxMember` and `TeamMember` (`after_commit`, account 1 only) and queues `DurianOs::ChangeReportJob`, which posts `person.created | person.changed | person.deactivated` (and `emergency_login.used`, `ping`) to `OS_REPORT_URL` signed with HMAC-SHA256 (`OS_REPORT_KEY`, 32 bytes base64url, and `OS_REPORT_KEY_ID`; the app refuses to start with a bad key). Retries: 1, 5, 15 minutes, then hourly for 24 hours. A ping is sent when Sidekiq starts and daily (`config/schedule.yml`). Nothing is sent unless all three variables are set. Reports never carry access data; Durian OS re-reads the person. There is no outbox table: a report lost before it is queued is caught by Durian OS's 10-minute check.

### Where the code is

- Everything lives in `custom/` and is loaded as `Custom::` modules by `prepend_mod_with` / `include_mod_with` (the same mechanism as `enterprise/`); `config/application.rb` adds `custom/app/**` and `custom/config/initializers`. Shared helpers are `DurianOs::*` under `custom/app/services`, `custom/app/jobs` and `custom/app/models`.
- When you change a controller or model that has a `Custom::` module, keep the module working, and run `bundle exec rspec spec/custom`.
- `script/durian_os_conformance.rb` checks a local server (never production): ORM serves no `/os/v2` (signed with a throwaway Ed25519 key), the ping report and its signature, the Platform endpoints, the one-time link, and the `OS_SIGNIN_ONLY` switch. `script/durian_os_conformance_seed.rb` prepares a local database for it.

### One-time operator steps (not code)

Set `ENABLE_ACCOUNT_SIGNUP=false` in the `installation_configs` row and the environment, then `GlobalConfig.clear_cache`; set `ENABLE_GOOGLE_OAUTH_LOGIN` and `ENABLE_SAML_SSO_LOGIN` to false (keep `GOOGLE_OAUTH_CLIENT_ID` for Gmail inboxes); set `LOGOUT_REDIRECT_LINK=https://durianos.in`; set `DISABLE_USER_PROFILE_UPDATE=true`; bind Puma to 127.0.0.1 and fence `/platform/`. The order and the rest (Platform App, service and emergency users, accounts 2 and 3, super admins) are in appendix 10.8, "Deploying".
