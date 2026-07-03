# Sequential-Thinking — detailed usage guide

Sequential-Thinking (`sequentialthinking`) externalizes reasoning into discrete, numbered thought steps that can build linearly *or* be revised, branched, and extended mid-stream. It is for dynamic, reflective problem-solving — not for generating one-shot answers.

## When to use it

- Use it for problems that benefit from structured reasoning: breaking down complex problems into steps, planning/design with room for revision, analysis that may need course correction, problems whose full scope is not clear initially, multi-step solutions, tasks needing context maintained over many steps, and filtering out irrelevant information.
- Do **not** use it for trivial, single-step tasks where a one-shot answer suffices.

## How to use it well

- Start with an initial `totalThoughts` estimate, but treat it as adjustable — you can revise it up or down as you progress.
- Let each thought build on the previous ones, but you are not locked into a linear path.
- Generate a hypothesis, then verify it within the chain; repeat until you reach a satisfactory answer.
- Express uncertainty explicitly when present, and ignore information irrelevant to the current step.

## Revision and branching

- **Revise** (`isRevision: true`, `revisesThought: <n>`) when questioning, course-correcting, or changing a previous decision. Feel free to question or revise previous thoughts.
- **Branch** (`branchFromThought: <n>`, `branchId: "<id>"`) to explore an alternative approach or assumption non-linearly while leaving the original line of reasoning intact.

## Adjusting and terminating

- Use `needsMoreThoughts: true` if you reach the planned end but realize more reasoning is required — don't hesitate to add more thoughts even at the "end".
- Only set `nextThoughtNeeded: false` when truly done and a satisfactory answer has been reached; provide a single, ideally correct answer as the final output.
- Control flow with `nextThoughtNeeded` — don't rely on the thought count alone (if `thoughtNumber` exceeds `totalThoughts`, the server auto-bumps `totalThoughts` to match).
