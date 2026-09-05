So to implement the next step in the orcestration, we need to match the `labels contains: "gh-issue-tracking:init-success")` label being applied to the main plan issue, and then find the next unimplemented story in order, then call `orchestrate-dynamic-workfow` with `implement-epic`

All three pieces you need to create this first step are listed below. You need to synthezised itewm 2 and 3 and place them into item 1's match case body.

*After that we will have to figure out how implement the next step in the orchestration, which (in the old system) is to match the `orchestration:epic-implemented` label being applied to the epic issue, and then call `orchestrate-dynamic-workfow` with `review-prs`. So we might need to add our label to the end of the implemetn-epic workflow to trigger our next step. But thats secondary to the main effort of implmenting step 1, descibed above.*

1. so for this case, where the issue tracking init skill completes successfully....

webhook_receiver/orchestration_prompt.jinja2.md:351-361

```
case (type = issues &&
        action = labeled &&
        labels contains: "gh-issue-tracking:init-success")
        {
          ## /gh-issue-tracking-init skill completed successfully — begin epic creation loop.
          ## Label-driven: matches on `gh-issue-tracking:init-success` regardless of title format.
          ## Human or delegating agent applies this label when the plan is reviewed and ready.

          - postStatusUpdate("🤖 Orchestrator matched `gh-issue-tracking:init-success` clause. Scanning for next unimplemented story issue item...")

          - postStatusUpdate("🤖 Orchestrator `gh-issue-tracking:init-success` clause. Finished (no implementation yet.)")
```

- 2 We need to find the next story in order in the its epic that is unimplemented, like this macth case body does...

webhook_receiver/orchestration_prompt.jinja2.md:133-160

```
case (type = issues &&
        action = labeled &&
        labels contains: "orchestration:epic-complete" &&
        labels contains: "epic")
        {
          ## Epic completion detected — find next unimplemented line item and create a new epic for it.
          ## One entire epic implementation sequence completed, start the next sequence

          - postStatusUpdate("🤖 Orchestrator matched `orchestration:epic-complete` + `epic` clause. Parsing completed epic and scanning for next line item...")
          - $completed = extract_epic_from_title(title)
          - if $completed is null or empty:
            - postStatusUpdate("❌ Could not parse epic identifier from title. Cannot determine next line item.")
            - comment on the issue with an error explaining the title could not be parsed, then skip to ##Final.
          - $next = find_next_unimplemented_line_item($completed.phase, $completed.line_item)
          - if $next is null:
            - postStatusUpdate("🏁 All line items are complete! The implementation plan is fully implemented. Closing this epic.")
            - close the current epic issue with a comment "All line items are complete. The implementation plan is fully implemented."
            - skip to ##Final.

          - postStatusUpdate("🤖 Next line item found: Phase " + $next.phase + ", Line Item " + $next.line_item + ". Creating next epic via `create-epic-v2`...")
          - /orchestrate-dynamic-workflow
              $workflow_name = create-epic-v2 { $phase = $next.phase, $line_item = $next.line_item }
          
          - if create-epic-v2 succeeds:
            - postStatusUpdate("✅ Next epic created for Phase " + $next.phase + " Line Item " + $next.line_item + ". Applying `orchestration:epic-ready` and closing this epic.")
            - apply label "orchestration:epic-ready" to the newly-created epic issue.
            - close the current epic issue with a short comment indicating it is complete and referencing the newly-created epic issue.
          - else → postStatusUpdate("❌ `create-epic-v2` failed for Phase " + $next.phase + " Line Item " + $next.line_item + ". See workflow run logs."), skip to ##Final.           
```

- 3 and then call orchestrate dynamic workflow with implemnt-epic, just like this match case does:

webhook_receiver/orchestration_prompt.jinja2.md:163-187

```
 case (type = issues &&
        action = labeled &&
        labels contains: "orchestration:epic-ready" &&        
        labels contains: "epic")
        {
          ## Epic implementation triggered — run 4-step orchestration sequence.
          ## Label-driven: matches on `orchestration:epic-ready` + `epic` label combination.
          ## Title is still parsed by extract_epic_from_title() for the epic identifier.

          - postStatusUpdate("🤖 Orchestrator matched `orchestration:epic-ready` + `epic` clause. Parsing epic from title...")
          - $created_epic = extract_epic_from_title(title)
          - if $created_epic is null or empty:
            - postStatusUpdate("❌ Could not parse epic identifier from issue title. Cannot proceed with implementation.")
            - comment on the issue with an error explaining the title could not be parsed, then skip to ##Final.

          ## Per-Epic 4-Step Orchestration Sequence
          ## Step 1: Implement the epic (code, tests, open PRs)
          - postStatusUpdate("🤖 Step 1/4: Starting `implement-epic` for epic: " + $created_epic)
          - /orchestrate-dynamic-workflow
               $workflow_name = implement-epic { $epic = $created_epic }
          - if implement-epic succeeds:
            - postStatusUpdate("✅ Step 1/4: `implement-epic` completed for: " + $created_epic + ". Applying `orchestration:epic-implemented` label.")
            - apply label "orchestration:epic-implemented" to the newly-created epic issue.
          - else → postStatusUpdate("❌ Step 1/4 `implement-epic` failed for: " + $created_epic + ". See workflow run logs."), skip to ##Final.      
        }
```
