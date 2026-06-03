# Issues needing addressing

## List

### I1

exsint gtracing implmentation is stripping everything away.

Need to only strip away seletive lines, e.g.

`service=bus type=message.part.delta publishing` ==>

```
INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +1ms service=bus type=message.part.delta publishing
orchestratorservice-1  | INFO  2026-06-03T16:49:15 +0ms service=bus type=message.part.delta publishing
```

### I2

Need to print out the output of the prompt (output of `opencode run --attach "prompt foo far fee fum").

When I prompt manually using `prompt.ps1` i get a large amount of output while the prompt run is happening. Much more than form the serve-side during the same run.

Need to dispaly the out put of the webhook_reciever's prompt.ps1 call (it will show up in the docker service container's output much like the orchestrationservice's output, so just need to capture it and display)

### I3 Need tracing output of the webhook event reception lifecycle

- webhook event receieved and payload
- prompt after assembly before its handed to service

So less than successful runs can be traced and diagnosed

