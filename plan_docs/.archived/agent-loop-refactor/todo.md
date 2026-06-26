# TODO

##

### T1: update .*ignore files

.dockerignore
.gitignore

### T2: Add test coverage to least covered areas

- increase  test coverage where possible w/o non-trivial change
- add exclusions where appropriate
- are b HTML & PR comment report geneerators inclu9ded?

### T3: 3-stage Integration Tests

- create integration tests for each of the "stages"
- try to test at the inter-stage input/output levels, e.g.

1. planner (natural languagae -> appl plan)
2. bead-generator (app_plan -> bead graph)
3. bead-harvester loop (beads -> results)
4. orchestrator-service (bead prompt)

... or similar (you get get the idea- break the stages where it makes sense)

- test from the latest stage, backwards to the first stage, so that you can have confidence that all stages behind the current one are working)
- testing the failure/retry logic functionality is important, it may be complicated so we had better find the issues now
