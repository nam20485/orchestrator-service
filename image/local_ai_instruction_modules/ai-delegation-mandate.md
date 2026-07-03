# Delegation Mandate Enhancement

## Overview

This document provides mandatory delegation requirements that enhance the orchestrate-dynamic-workflow assignment to force frequent delegation through explicit constraints and verification mechanisms.

## MANDATORY DELEGATION REQUIREMENTS

### Pre-Execution Rules

- **BEFORE executing ANY technical action**, the orchestrator MUST delegate to appropriate specialists
- **Maximum 3 direct actions** allowed per orchestrator (limited to: planning, coordination, approval)
- **ALL file operations, builds, tests, and infrastructure work MUST be delegated**
- **Orchestrator role**: Coordination and approval ONLY - direct execution FORBIDDEN

### Delegation Gates

- Each assignment phase **REQUIRES minimum 2 agent delegations**
- Document delegation decision matrix: Task Type → Required Agent Type
- Orchestrator must justify ANY direct execution in violation report
- **Minimum 75% delegation coverage** required for workflow success

### Agent-Task Mapping Matrix

| Task Category     | Primary Agent          | Secondary Agent    | Justification Required        |
| ----------------- | ---------------------- | ------------------ | ----------------------------- |
| Repository Setup  | `developer`            | `github-expert`    | If tools unavailable          |
| GitHub Operations | `github-expert`        | `developer`        | If org-level changes required |
| Project Planning  | `product-manager`      | `planner`          | Never                         |
| Code Structure    | `developer`            | —                  | If specialized needs          |
| Documentation     | `documentation-expert` | `developer`        | If expert unavailable         |
| Testing           | `qa-test-engineer`     | `developer`        | If QA unavailable             |
| Security          | `security-expert`      | `developer`        | Never                         |

### Verification Checkpoints

- **After every 2nd action**: orchestrator must report "Delegation compliance: X/Y tasks delegated"
- **Mid-workflow checkpoint**: Delegation coverage must be ≥60%
- **Final report**: Must include delegation coverage metrics
- **Workflow fails** if delegation coverage < 75%

### Delegation Decision Documentation

For each potential task, orchestrator must document:

```markdown
Task: [Description]
Agent Selected: [Agent Type]
Rationale: [Why this agent]
Alternative Considered: [Other agents evaluated]
Direct Execution Justification: [Only if not delegated - must be tool limitation]
```

## Enhanced Dynamic Workflow Syntax

### Modified Script Section

```markdown
## Script

### Delegation Requirements (MANDATORY)

Each assignment MUST be delegated to specialized agents **-OR-** broken down by task type according to section #Delegation Strategies:

- `init-existing-repository` → delegate to `developer` AND `github-expert`
- `create-app-plan` → delegate to `product-manager` AND `planner`
- `create-project-structure` → delegate to `developer` AND `github-expert`

### Delegation Strategies

1. Entire Assignment Delegation: Use Task tool to assign entire assignment to specialist agent
2. Partial Task Delegation: Break down assignment into tasks and delegate each to appropriate agents

- Choose which tasks to delegate based on agent expertise and availability
- Consider potential bottlenecks and reallocate tasks as needed
- Monitor delegated tasks and provide guidance without direct execution
- Choose delegation approach based on assignment complexity and agent capabilities
```

### Delegation Tasks (Examples) with Agent Types

| Task                       | Primary Agents                         | Supporting Agents                       | Notes                                                       |
| -------------------------- | -------------------------------------- | --------------------------------------- | ----------------------------------------------------------- |
| `init-existing-repository` | `developer`            | `github-expert`                    | Configure remotes, branches, and automation scripts.        |
| `create-app-plan`          | `product-manager`, `planner` | `documentation-expert`         | Define roadmap, milestones, and success metrics.            |
| `create-project-structure` | `developer`            | `github-expert`, `qa-test-engineer` | Scaffold services, CI/CD, and baseline tests.               |
| `update-documentation`     | `documentation-expert` | `product-manager`, `developer`   | Capture decisions, runbooks, and onboarding guides.         |
| `expand-test-suite`        | `qa-test-engineer`     | `developer`                      | Add regression, integration, and edge-case coverage.        |
| `security-audit`           | `security-expert`      | `developer`                      | Review threat models, secrets hygiene, and dependency risk. |

### Execution Rules

- Orchestrator role: Coordination and approval ONLY
- Direct execution by orchestrator: FORBIDDEN except for delegation and reporting
- Minimum 2 agents per assignment phase
- Document all delegation decisions with rationale

### Assignment Execution Pattern

For each `$assignment_name` in `$assignments`, you will:

1.  **ANALYZE**: assignment requirements and identify required agent types
2.  **DELEGATE**: to appropriate specialist agent(s) using Task tool
3.  **COORDINATE**: between multiple agents if required
4.  **MONITOR**: delegated work progress and provide guidance
5.  **REVIEW**: delegated work and approve/request changes
6.  **VERIFY**: all acceptance criteria are met
7.  **COLLECT MEMORY**: read each subagent's `## Memory Save Requests` list and persist those facts yourself using the memory-graph write tools (`add_observations` / `create_entities` / `create_relations`). You are the sole memory writer.
8.  **RECORD**: output as `#workflow.$assignment_name`
9.  **REPORT**: delegation compliance metrics

## Memory Save Requests Hand-off Contract (MANDATORY)

The memory-graph store is **single-writer**: only the Orchestrator may call memory WRITE tools
(`create_entities`, `create_relations`, `add_observations`, `delete_*`). Concurrent writers
(the orchestrator session plus each subagent session each spawn their own server-memory process)
corrupt the `memory.jsonl` file.

To enforce single-writer without losing subagent contributions:

- **Every delegated subagent** is memory **READ-ONLY**. It MAY call `search_nodes`, `open_nodes`,
  and `read_graph` to load context, but MUST NOT call any memory write tool.
- Instead, each subagent MUST end its result with a `## Memory Save Requests` section listing any
  durable facts worth persisting. If there is nothing to persist, the section reads `(none)`.
- **The Orchestrator** reads each subagent's `## Memory Save Requests` list and persists those
  facts itself using the write tools.

Hand-off format example (subagent result):

```markdown
## Memory Save Requests
- Entity: project-foo | Type: microservice | Observation: "uses PostgreSQL 16 on host db.internal:5432"
- Add observation to issue-42: "root cause was missing index on users.email"
- Create relation: ServiceA depends-on ServiceB
```

The Orchestrator MUST include "Memory is READ-ONLY for you; do not call memory write tools.
Return facts to persist under `## Memory Save Requests`" in the delegation context it passes to
every subagent.

## Enforcement Mechanisms

### Automatic Delegation Triggers

- Any mention of file creation → MUST delegate to appropriate agent
- Any build/test operation → MUST delegate to `qa-test-engineer` or `developer`
- Any infrastructure setup → MUST delegate to `developer`
- Any documentation → MUST delegate to `documentation-expert`

### Violation Reporting

If direct execution occurs, orchestrator must immediately report:

```markdown
DELEGATION VIOLATION DETECTED
Task: [What was executed directly]
Reason: [Tool limitation/emergency/other]
Mitigation: [How to prevent in future]
Impact: [Effect on delegation coverage]
```

### Coverage Calculation

```markdown
Delegation Coverage = (Tasks Delegated / Total Tasks) × 100
Required Threshold: ≥75%
Target Threshold: ≥90%
```

## Integration with Orchestrator Assignment

### Enhanced Acceptance Criteria

Add to existing acceptance criteria:

8. **Delegation Coverage**: Minimum 75% of tasks delegated to specialist agents
9. **Agent Utilization**: At least 2 different agent types used per assignment
10. **Coordination Quality**: Evidence of successful agent coordination and integration

### Enhanced Run Report Schema

Add to existing Run Report:

```markdown
## Delegation Metrics

- Total Tasks: X
- Tasks Delegated: Y
- Delegation Coverage: Z%
- Agents Utilized: [List of agent types]
- Direct Execution Events: [List with justifications]
- Coordination Challenges: [Issues and resolutions]
```

## Implementation Instructions

### For Orchestrator Agents

1. **Read this document** before starting any dynamic workflow
2. **Plan delegation strategy** before executing any technical actions
3. **Use Task tool extensively** to delegate to appropriate agents
4. **Compile context** from agent instructions and workflow files to pass to agents when delegating
5. **Maintain delegation tracking** throughout execution
6. **Report delegation metrics** at checkpoints and completion

### For Dynamic Workflow Files

1. **Include delegation requirements** in Script section
2. **Specify required agent types** for each assignment
3. **Set delegation coverage targets** (minimum 75%)
4. **Document coordination requirements** between agents

### For Assignment Files

1. **Identify delegation points** in Detailed Steps
2. **Specify required agent capabilities** for each step
3. **Include delegation coverage** in Acceptance Criteria
4. **Document coordination handoffs** between agents

### Context Compilation for Delegation

When delegating tasks, compile and provide the following context to the delegated agent:

- **Assignment Overview**: Brief summary of the overall assignment and its objectives.
- **Specific Task Details**: Clear description of the specific task being delegated, including any relevant requirements or constraints.
- **Related Instructions**: Relevant sections from the agent instructions that pertain to the task.
- **Workflow Context**: Information about the current state of the workflow, including any dependencies or prior steps that impact the task.
- **Acceptance Criteria**: The specific criteria that must be met for the task to be considered complete.
- **Tools and Resources**: Any specific tools, repositories, or resources that the agent will need to complete the task.
- **Memory Constraint (MANDATORY)**: Tell every subagent: "Memory is READ-ONLY for you; do not call memory write tools (`create_entities`, `create_relations`, `add_observations`, `delete_*`). You MAY read via `search_nodes` / `open_nodes` / `read_graph`. Return any durable facts to persist under a `## Memory Save Requests` section in your result; the Orchestrator persists them."
- **Communication Protocols**: Preferred methods for updates, questions, and reporting progress.

## Monitoring and Compliance

### Real-Time Tracking

- Track delegation decisions as they occur
- Calculate running delegation coverage percentage
- Alert when coverage drops below thresholds
- Document all direct execution with justification

### Post-Execution Review

- Analyze delegation effectiveness
- Identify improvement opportunities
- Update agent-task mapping based on results
- Refine delegation strategies for future workflows

This delegation mandate ensures that orchestrator agents primarily focus on coordination and oversight while leveraging specialist agents for technical execution, leading to more distributed and specialized workflow execution.
