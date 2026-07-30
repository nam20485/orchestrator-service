1. `gh auth status` denined- add to allowlist

2. didnt save the session id for the first delegate task- error stating the task_id must start with ses- add some inctrucitons to idirect how to use it for the agent?

3. GLM 5.2 model looks like its being used (by subagent maybe?) we should be using GLM 5

```log
orchestratorservice-1  | timestamp=2026-07-30T19:29:47.224Z level=INFO run=96e18280 message=stream providerID=zai-coding-plan modelID=glm-5.2 session.id=ses_04b7fab40ffeYXYxotjyTaTmYG small=false agent=developer mode=subagent
orchestratorservice-1  | timestamp=2026-07-30T19:29:47.237Z level=INFO run=96e18280 message="llm runtime selected" llm.runtime=ai-sdk llm.provider=zai-coding-plan llm.model=glm-5.2
```

1. orchestrator-discovered issue:

```
The orchestrator also discovered a real skill gap during Phase 1: link-sub-issue.ps1 and set-dependency.ps1 do their REST idempotency probe (checking if the link already exists) before the -DryRun guard, so they can't be DryRun-tested with synthetic numbers — they'd 404. It logged this as a SKILL GAP in memory for fixing later.
```

1. so there is no way to log or trace what the subagent is actually doing, besides the idle timer updates and the few tool calls that surface?
