# Issues needing addressing

## List

### I1

existing github tracing implmentation is stripping everything away.

Need to only strip away seletive lines, e.g.

`service=bus type=message.part.delta publishing` ==>

```sh
INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
```

Create new implementaiton for this with very small blacklist of lines to strip away. (or keep existing and remove most entries from the blacklist)

- We can add more entries to the blacklist over time as we identify more lines to strip away.
- Validate it works by running the code and checking the output.

### I2

Need to print out the output of the prompt (output of `opencode run --attach "prompt foo far fee fum").

When I prompt manually using `prompt.ps1` i get a large amount of output while the prompt run is happening. Much more than form the serve-side during the same run.

Need to dispaly the out put of the webhook_reciever's prompt.ps1 call (it will show up in the docker service container's output much like the orchestrationservice's output, so just need to capture it and display)

### I3 Need tracing output of the webhook event reception lifecycle

- webhook event receieved and payload
- prompt after assembly before its handed to service

So less than successful runs can be traced and diagnosed

### I4

k8s reousrces for deployment to cluster- use IaC to make it provider/on-prem agnodtic and so dont have to maintan 10 reousrces files- easy deploy

### I5 - Idle timeout

- detection (> x s since activity timer was reset)
- kill the run if timer fires

- makee sure subagent delegation activity resets timer (otherwise timer wil fire while subagent delegations are executing)

### I6 - Compose `OPENCODE_SERVER_PASSWORD` enforcement

`compose.yaml` passes `OPENCODE_SERVER_PASSWORD` through from host env but does not enforce it. The plan.md (`plan_docs/plan.md`) acceptance criteria requires `docker compose up` to fail fast if the variable is unset.

Fix: change the compose environment line to use required-variable syntax:

```yaml
- OPENCODE_SERVER_PASSWORD=${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD is required}
```

- Validate by running `docker compose up` without the variable set and confirming it fails with a clear error message.
- Also verify `test/test-compose-config.sh` still passes after the change.

### I7 - Client script env var URL resolution

Neither `scripts/prompt.ps1` nor `scripts/attach.ps1` reads `OPENCODE_SERVER_URL` from the environment. `prompt.ps1` uses a PowerShell parameter default (`http://localhost:4099`); `attach.ps1` hardcodes the URL entirely.

Per plan.md, both scripts should resolve the server URL from:

1. `OPENCODE_SERVER_URL`, if set.
2. Otherwise `OPENCODE_HOST` and `OPENCODE_PORT`.
3. Otherwise default to `http://localhost:4099`.

Fix:

- `prompt.ps1`: set `$ServerUrl` default from `$env:OPENCODE_SERVER_URL` (with `OPENCODE_HOST`/`OPENCODE_PORT` fallback) instead of the hardcoded default.
- `attach.ps1`: add the same URL resolution logic and pass the resolved URL to `opencode attach`.

- Validate by running each script with `OPENCODE_SERVER_URL` set and confirming the resolved URL is used.
- Validate by running each script without any env vars and confirming the default `http://localhost:4099` is used.
