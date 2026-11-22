# Ubuntu USB runner and Cloudflare compute — KMCP-V5-PRODUCTION

Owner node: the ordinary Ubuntu desktop user (the screenshot shows `siango`).
Inputs: persistent Ubuntu USB/home, repository administrator GitHub login, Cloudflare account login.
Outputs: reusable systemd GitHub runner, pinned Worker project and optional Container deployment tools.
No production authority, database, DNS, Tunnel, SSH or existing MCP service is changed by runner installation.

## Ubuntu installation

Run these commands in a normal desktop-user terminal. Do not run `sudo bash`.
Use this PR's `feature/kmcp-portable-circuits-20261122` branch until merged.

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --depth 1 --branch feature/kmcp-portable-circuits-20261122 \
  https://github.com/siango/KnowingMindDashboard.git "$HOME/kmcp-host-setup"
cd "$HOME/kmcp-host-setup"
bash scripts/host/install-tools.sh
export PATH="$HOME/.local/bin:$PATH"
gh auth login --hostname github.com
bash scripts/host/install-runner.sh
```

If that checkout already exists, use it and inspect its current branch before fetching updates.
The GitHub login needs administration permission for the target repository's runner API.
The installer downloads the current official GitHub runner release for x64/arm64 and verifies
its published asset SHA256 digest before extraction. Missing digest fails closed.
Registration input uses `ACTIONS_RUNNER_INPUT_TOKEN`, matching the official runner source
`actions/runner/src/Runner.Listener/CommandSettings.cs`; the short-lived token is never printed
or placed in the command line. The long-lived GitHub credential remains managed by `gh`.

The script finds matching `.runner` files under the user's home (maximum depth 6).
For a runner outside that home, explicitly choose its original directory:

```bash
REPO=siango/KnowingMindDashboard RUNNER_DIR=/persistent/path/actions-runner \
  bash scripts/host/install-runner.sh
```

Default new directory: `$HOME/KnowingMind/runners/github`.
Default name: `<hostname>-usb`; default custom labels: `km-fleet,nonprod,kmcp`.
Existing registration/labels are preserved. It never uses `--replace` or removes another runner.
If a duplicate name exists remotely but no local credentials are found, recover that runner's
directory or explicitly choose a new `RUNNER_NAME`. Multiple local matches require `RUNNER_DIR`.
Registration does not install Python project dependencies or enable a production writer.

USB must contain an installed/persistent Ubuntu filesystem. `overlay`, `tmpfs` and `ramfs`
locations are rejected; choose an ext4 persistent mount for `RUNNER_DIR` when using a Live USB.
Keep Ubuntu powered and awake; a runner cannot accept work while the machine sleeps/offline.
No inbound port is needed for the runner itself. A temporary SSH Access banner timeout in
the screenshot does not prove the runner is unavailable; the later login succeeded.

Check availability at:
https://github.com/siango/KnowingMindDashboard/settings/actions/runners

```bash
gh api repos/siango/KnowingMindDashboard/actions/runners \
  --jq '.runners[] | {name,status,busy}'
```

An existing workflow must select this runner, for example `runs-on: [self-hosted, Linux, kmcp]`.
The existing portable package test workflow stays on GitHub-hosted compute. Only trusted
owner-controlled jobs should select the persistent host; do not route untrusted fork code there.
Do not claim host readiness until GitHub reports `online` and an actual selected job completes.

## Cloudflare Workers — stateless KMCP edge

The new `cloudflare/kmcp-edge` project is optional. It forwards only `/mcp` to a configured
HTTPS KMCP origin, streams response bodies, and preserves MCP session headers.
It has no task store, scheduler or write-authority implementation. The existing origin remains
the SSOT and enforces its original ACL. `/health` proves only that the edge is responding;
it explicitly reports `originChecked: false`.

This configuration uses a single trusted operator identity at the origin. Give EDGE_TOKEN
only to callers authorized for that origin identity. It does not implement per-user OAuth or
provide public multi-user authentication. `ORIGIN_AUTHORIZATION` must match the existing
origin's authentication format; do not guess an API key or widen its current permissions.

Existing Worker sources found in `siango/KnowingMindApp` include
`cloudflare_workers/knowingmind-line-webhook` and `apps/dhamma-web-channel-edge`.
Do not overwrite either with this proxy. If `kmcp-edge` already exists in the account, inspect
its source/deployments and choose the intended existing project before deploying.

```bash
cd "$HOME/kmcp-host-setup"
bash scripts/host/cloudflare.sh cloudflare/kmcp-edge login
bash scripts/host/cloudflare.sh cloudflare/kmcp-edge check
cd cloudflare/kmcp-edge
# Enter values at the CLI secret prompts; never commit or paste them in chat.
./node_modules/.bin/wrangler secret put EDGE_TOKEN
./node_modules/.bin/wrangler secret put ORIGIN_URL
./node_modules/.bin/wrangler secret put ORIGIN_AUTHORIZATION
# Only if the existing origin uses Cloudflare Access service-token policy:
./node_modules/.bin/wrangler secret put CF_ACCESS_CLIENT_ID
./node_modules/.bin/wrangler secret put CF_ACCESS_CLIENT_SECRET
cd ../..
bash scripts/host/cloudflare.sh cloudflare/kmcp-edge deploy
```

`ORIGIN_URL` must be the complete existing HTTPS endpoint ending exactly in `/mcp`, such as
`https://mcp.knowingmind.app/mcp` **only if that is the verified current origin**.
The Access policy must accept that service token; the script does not create or bypass policies.
Set `CLOUDFLARE_ACCOUNT_ID` explicitly if login has multiple accounts. The account used must
match the intended deployment. No credentials/account IDs are included in the project.
`secret put` can provision a new Worker before its first deploy; it is an account mutation.
Run only `check` if you want local bundle validation without publishing.
The supplied config creates no DNS route and binds no D1, Queue or Durable Object.

`cloudflare.sh` uses a project's committed package-lock and local Wrangler. For an existing
project lacking a lockfile, generate and review its lockfile in that project's own repository
before using this helper; it deliberately refuses an unpinned dependency installation.
The helper's `deploy` executes the project's configured deployment; review its bindings first.

## Cloudflare Containers — Linux compute

Workers & Pages runs edge code; Containers provides a separate Linux application runtime.
Neither is a ready-made persistent GitHub runner VM. Do not install a GitHub runner into
ordinary Workers, and do not treat an idle Container as an always-on runner host.
Workers for Platforms is for platform-managed tenant Workers; it is not required here.

Install Docker only if the host does not already have an available Docker runtime:

```bash
cd "$HOME/kmcp-host-setup"
bash scripts/host/install-docker.sh
# Log out/in once if newly added to the docker group.
docker info
```

The installer reuses Docker if available; it stops on an inaccessible existing daemon or
conflicting runtime packages rather than removing them. New installation uses Docker's
official Ubuntu apt repository. Membership in the docker group grants host administration.

For a **Container-enabled app with an HTTP server and reviewed Wrangler configuration**:

```bash
bash scripts/host/cloudflare.sh /path/to/container-project containers-check
bash scripts/host/cloudflare.sh /path/to/container-project containers-deploy
```

The second command publishes the Worker and Container and can incur account usage charges.
It requires an account with the applicable Containers entitlement and sufficient quota.
No Container deployment or billing-plan change is performed by this repository update.
Wrangler builds local Dockerfile images through Docker; allow provisioning time before checking
the actual Container endpoint, then inspect `wrangler containers list` and deployment logs.

**Current KMCP Dockerfile is a stdio CLI, not an HTTP Container application.** These scripts
do not claim it is Cloudflare-ready. A Container port requires an HTTP adapter, existing trusted
authority integration, health/readiness, persistent state strategy and actual deployment tests.
Keep KMCP/KMCW execution on the Ubuntu host for this installation; the optional Worker is
the transport edge. This avoids silently migrating the queue or creating a second SSOT.

## Evidence and validation

```bash
for script in scripts/host/*.sh; do bash -n "$script"; done
python3 -m unittest discover -s scripts/host/tests -v
cd cloudflare/kmcp-edge
npm ci
npm test
npm run check
```

Eight runner fixture cases execute the real installer: fresh/repeated install, active service
reuse, missing-service repair, wrong-repository protection, remote duplicate refusal,
ephemeral storage refusal, checksum mismatch and invalid repository rejection.
Seven Worker tests cover configuration/authentication/path/method gates, bad origin rejection,
stream/session forwarding and credential replacement, redirects/network failure.
Fixtures do not register a runner or deploy to Cloudflare. CI `kmcp-host-installers` runs on
GitHub-hosted Ubuntu without AV15, Cloudflare credentials or production database access.
Local `wrangler deploy --dry-run` validates the actual bundle; it does not verify origin Access
policy, model availability, write authority, machine reboot persistence or production health.

Official references:
- https://docs.github.com/en/actions/how-tos/manage-runners/self-hosted-runners/configure-the-application
- https://docs.github.com/en/rest/actions/self-hosted-runners
- https://github.com/actions/runner/blob/main/src/Runner.Listener/CommandSettings.cs
- https://developers.cloudflare.com/workers/wrangler/commands/workers/
- https://developers.cloudflare.com/containers/get-started/
- https://docs.docker.com/engine/install/ubuntu/
