# **Detailed Agent Loop Development Plan (Agent Handoff Document)**

**TARGET AGENT INSTRUCTIONS:** You are tasked with a critical architectural refactor. You will migrate the orchestrator-service from a probabilistic, procedural markdown-driven workflow to a deterministic, three-tier software factory architecture utilizing the Beads ecosystem (br and bvr).

This document serves as your single source of truth. It contains the conceptual architectural design, the step-by-step implementation plan, and the exact code blocks required for the new files. It is heavily context-aware of the existing orchestrator-service codebase and leverages existing internal tools (like scripts/prompt.ps1 and runner.py's streaming logic).

## **Executive Summary & Architectural Shift**

The current system relies on an LLM to manage state inside massive Markdown files, which degrades context and breaks parallel workflows. We are moving to a three-tier abstraction pipeline:

| Phase | Actor | Input | Action | Output |
| :---- | :---- | :---- | :---- | :---- |
| **1\. Ideation** | /perfect-idea (PM Agent) | A loose human idea. | Interrogates constraints & architecture. | application_plan.md |
| **2\. Planning** | /plan-to-beads (Scrum Agent) | application_plan.md | Derives Epics/Tasks/ACs and maps DAG dependencies. | .beads/ Graph DAG |
| **3\. Execution** | ralph\_loop.py (Engineers) | .beads/ Graph DAG | Spawns isolated agents to write code, test, and close beads. | Working Software |

* **Atomic State:** Tasks become atomic JSON objects managed in a Directed Acyclic Graph (DAG) inside a local .beads/ directory.  
* **Project Isolation:** Achieved by passing the target repository path to the existing scripts/prompt.ps1 via the \-Workspace argument.  
* **Synchronous Webhook Dispatch:** The webhook receiver executes the immediate webhook prompt synchronously, and *then* triggers the graph loop to drain any unblocked beads.

## **Phase 1: Infrastructure & Toolchain Preparation**

The orchestrator requires access to the br (database editor) and bvr (graph analysis) Rust binaries locally and in its webhook container.

### **1.1 Update Local Development Setup**

**Target File:** orchestrator-service/scripts/install-dev-tools.ps1

Place this logic inside the existing try block, right before the final success message:

    \# Verify Rust is installed before attempting to compile  
    if (-not (Get-Command cargo \-ErrorAction SilentlyContinue)) {  
        Write-Warning 'Cargo is not installed. Please install Rust via \[https://rustup.rs/\](https://rustup.rs/) to compile the Beads ecosystem.'  
    } else {  
        Write-Host 'Compiling and installing Beads ecosystem (this may take a few minutes)...' \-ForegroundColor Cyan  
        cargo install \--git \[https://github.com/Dicklesworthstone/beads\_rust.git\](https://github.com/Dicklesworthstone/beads\_rust.git)  
        cargo install \--git \[https://github.com/Dicklesworthstone/beads\_viewer\_rust.git\](https://github.com/Dicklesworthstone/beads\_viewer\_rust.git)  
        Write-Host 'Beads ecosystem installed successfully.' \-ForegroundColor Green  
    }

### **1.2 Update Webhook Container (Multi-Stage Build)**

**Target File:** orchestrator-service/Dockerfile.webhook

Compile the Rust binaries in a builder stage to keep the Python image lean.

\# \--- Stage 1: Rust Builder \---  
FROM rust:1.77-slim AS rust-builder  
WORKDIR /build  
RUN apt-get update && apt-get install \-y git pkg-config libssl-dev  
RUN cargo install \--git \[https://github.com/Dicklesworthstone/beads\_rust.git\](https://github.com/Dicklesworthstone/beads\_rust.git)  
RUN cargo install \--git \[https://github.com/Dicklesworthstone/beads\_viewer\_rust.git\](https://github.com/Dicklesworthstone/beads\_viewer\_rust.git)

\# \--- Stage 2: Final Python/UV Image \---  
FROM debian:trixie-20260518-slim  
\# ... \[Keep your existing apt-get and powershell installations here\] ...

\# Copy compiled Beads binaries from the builder stage into the final image  
COPY \--from=rust-builder /usr/local/cargo/bin/br /usr/local/bin/br  
COPY \--from=rust-builder /usr/local/cargo/bin/bvr /usr/local/bin/bvr

\# ... \[Keep your existing uv sync and ENV setup here\] ...

## **Phase 2: Upstream Ideation & Requirements Gathering**

We must eliminate old granular templates and centralize planning into a master application plan, generated interactively.

### **2.1 Clean Up Old Workflows**

Permanently delete the following legacy Markdown workflow templates:

* plan\_docs/epic-implementation-workflow.md  
* plan\_docs/epic-planning-workflow.md  
* plan\_docs/phase-epic-plan-template.md  
* plan\_docs/epic-plan-and-implmentation-workflow.md

### **2.2 Create the Idea Perfector Skill**

**Target File:** orchestrator-service/.agents/skills/perfect-idea/SKILL.md (Create new)

\---  
name: perfect-idea  
description: "Acts as a Staff Engineer and Product Manager. Interrogates a loose app idea via conversation, resolves architectural ambiguities, and ultimately generates a formal, highly detailed \`application_plan.md\`."  
\---

\<objective\>  
Transform a human's loose application idea into a rigorous, formal \`application_plan.md\` document through an interactive interrogation process.  
\</objective\>

\<inputs\>  
\- \`$seed\_idea\`: The initial description of the app or feature the user wants to build.  
\</inputs\>

\<instructions\>  
You are a Staff Engineer and Technical Product Manager. Your job is to extract a bulletproof software architecture plan from the user. 

Do NOT immediately generate the final plan. You must operate in two distinct phases.

\#\#\# Phase 1: Interrogation (Interactive)  
1\. Read the user's \`$seed\_idea\` (or the ongoing conversation history).  
2\. Identify architectural gaps, missing constraints, or vague requirements.  
3\. Ask the user 3 to 5 highly specific, numbered questions to resolve these gaps.  
4\. Wait for the user to reply. Iterate on this process until you are confident you have a complete mental model of the application.

\#\#\# Phase 2: Generation (File Output)  
Once the user has answered your questions and you have enough clarity:  
1\. Announce that you are generating the application plan.  
2\. Create/Update the file \`docs/application_plan.md\` (or \`plan\_docs/application_plan.md\`) using \`docs/application_plan_template.md\` as the structural template. Ensure Sections 1-6 are fully fleshed out, especially the "Development Roadmap" phase breakdowns containing Context, AC, and Validation.  
3\. Write this content to the file system.

\#\#\# Phase 3: Handoff  
After generating and saving the file, tell the user:  
\*"I have generated the formal application plan. Please review it. If it looks correct, reply with \`/plan-to-beads\` to convert this plan into an executable task graph."\*  
\</instructions\>

## **Phase 3: The Planner Skill (plan-to-beads)**

**Target File:** orchestrator-service/.agents/skills/plan-to-beads/SKILL.md (Create new)

This skill forces the LLM to parse the detailed Application Plan and translate it into a strict Bash script of br commands. Crucially, it extracts the Context, Acceptance Criteria, and Validation steps and packs them into the Bead's \--description field.

\---  
name: plan-to-beads  
description: "Converts a high-level human-readable Application Plan (Markdown) into a strict, machine-readable Directed Acyclic Graph (DAG) using the \`br\` (beads) CLI tool."  
\---

\<objective\>  
Translate an application plan into a graph of atomic execution tasks inside the \`.beads/\` directory. Pack the Acceptance Criteria into the Bead descriptions, and define strict blocking dependencies between tasks.  
\</objective\>

\<inputs\>  
\- \`$plan\_doc\`: Path to the high-level application plan.  
\</inputs\>

\<instructions\>  
You are an expert Technical Project Manager. Your job is to convert the provided Markdown plan into a \`beads\` execution graph.

Instead of writing a phase document, you will write and execute a single bash script containing \`br\` CLI commands.

\#\# Instructions  
1\. \*\*Initialize Beads:\*\* Start the script with \`br init\`.  
2\. \*\*Extract & Pack Descriptions:\*\* For every Task/Story in the plan, extract its Context, Acceptance Criteria, and Validation instructions into a Bash Heredoc variable.  
3\. \*\*Create Nodes:\*\* Use \`br create\` and pass the heredoc to the \`--description\` flag. Assign priorities (\`--priority 1\`, etc.). Save returned IDs to variables.  
4\. \*\*Define Dependencies:\*\* Use \`br dep add \<BLOCKED\_BEAD\> \<BLOCKING\_BEAD\>\` to map the exact order of execution.   
5\. \*\*Execute & Commit:\*\* Run the generated bash script. Once complete, run \`git add .beads/\` and commit the new graph state.

\#\# Bash Script Template Example  
    \#\!/bin/bash  
    br init

    EPIC\_FOUNDATION=$(br create "Phase 1: Foundation Setup" \--type epic \--priority 1 | grep \-oP 'br-\[a-f0-9\]+')

    TASK\_DB\_DESC=$(cat \<\< 'EOF'  
    Context: We need a Postgres schema for user data.  
    Acceptance Criteria:  
    1\. Create user table  
    2\. Add alembic migration  
    Validation: Run \`uv run pytest tests/test\_db.py\`  
    EOF  
    )  
    TASK\_DB=$(br create "Configure PostgreSQL Schema" \--description "$TASK\_DB\_DESC" \--type task \--priority 1 | grep \-oP 'br-\[a-f0-9\]+')

    \# The Epic requires the task to finish  
    br dep add $EPIC\_FOUNDATION $TASK\_DB

    \# Sync graph to disk safely  
    br sync \--flush-only

Once the script completes, the orchestrator loop will automatically detect the unblocked tasks and begin executing them.  
\</instructions\>

## **Phase 4: Implementing the Graph State Machine (ralph\_loop.py)**

This loop daemon queries the graph, evaluates task priority, and seamlessly injects execution prompts into the running OpenCode service via prompt.ps1.

**Target File:** orchestrator-service/webhook\_receiver/ralph\_loop.py (Create new)

import subprocess  
import json  
import time  
import logging  
import os  
import tempfile  
import threading  
from pathlib import Path

from webhook\_receiver.config import Settings  
from webhook\_receiver.runner import \_prompt\_script\_invocation, \_stream\_to\_logger\_and\_file

logger \= logging.getLogger(\_\_name\_\_)

def get\_ready\_beads(repo\_path: str) \-\> list:  
    """Queries bvr for all unblocked tasks in the specific workspace."""  
    try:  
        result \= subprocess.run(  
            \["bvr", "--robot-ready", "--json"\],  
            cwd=repo\_path, capture\_output=True, text=True, check=True  
        )  
        if not result.stdout.strip():  
            return \[\]  
        return json.loads(result.stdout)  
    except Exception as e:  
        logger.error(f"Failed to query bvr in {repo\_path}: {e}")  
        return \[\]

def evaluate\_graph\_state(beads: list) \-\> dict | None:  
    """Selects the highest priority unblocked task (lowest number \= highest priority)."""  
    if not beads:  
        return None  
    return sorted(beads, key=lambda b: b.get('priority', 999))\[0\]

def check\_bead\_status(repo\_path: str, bead\_id: str) \-\> str:  
    """The Ultimate Source of Truth: checks if the bead was actually closed via br."""  
    try:  
        result \= subprocess.run(  
            \["br", "status", bead\_id\],  
            cwd=repo\_path, capture\_output=True, text=True, check=True  
        )  
        return result.stdout.strip().lower()  
    except subprocess.CalledProcessError:  
        return "unknown"

def spawn\_agent(settings: Settings, bead: dict, retry\_count: int, previous\_logs: str \= "") \-\> tuple\[bool, str\]:  
    """Injects a task prompt into the running orchestrator using the standard prompt script."""  
    bead\_id \= bead.get('id')  
    title \= bead.get('title')  
    description \= bead.get('description', '')  
    repo\_path \= settings.workspace  
      
    prompt \= f"You have been assigned Bead {bead\_id}: {title}.\\n\\nContext & Requirements:\\n{description}\\n"  
      
    if previous\_logs:  
        prompt \+= f"\\n\\n🛑 WARNING: Your previous attempt failed. Review logs: 🛑\\n{previous\_logs}\\n\\nFix the code, ensure tests pass, and run \`br close {bead\_id}\`."  
    else:  
        prompt \+= f"\\n\\nWhen completed and ALL tests pass, you MUST run: \`br close {bead\_id}\`."

    logger.info(f"Injecting prompt for {bead\_id} into service (Attempt {retry\_count \+ 1}).")  
      
    log\_dir \= Path(tempfile.gettempdir()) / "orchestrator-webhook"  
    log\_dir.mkdir(parents=True, exist\_ok=True)

    fd, prompt\_name \= tempfile.mkstemp(prefix=f"bead-{bead\_id}-", suffix=".md", dir=log\_dir)  
    prompt\_path \= Path(prompt\_name)  
    with os.fdopen(fd, "w", encoding="utf-8") as fh:  
        fh.write(prompt)

    cmd \= \_prompt\_script\_invocation(settings, prompt\_path)  
      
    stdout\_path \= log\_dir / f"{prompt\_path.stem}.stdout"  
    stderr\_path \= log\_dir / f"{prompt\_path.stem}.stderr"  
    stdout\_file \= open(stdout\_path, "w", encoding="utf-8")  
    stderr\_file \= open(stderr\_path, "w", encoding="utf-8")

    try:  
        proc \= subprocess.Popen(  
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  
            start\_new\_session=True, text=True,  
        )

        t1 \= threading.Thread(target=\_stream\_to\_logger\_and\_file, args=(proc.stdout, stdout\_file, f"bead-{bead\_id}"), daemon=True)  
        t2 \= threading.Thread(target=\_stream\_to\_logger\_and\_file, args=(proc.stderr, stderr\_file, f"bead-{bead\_id}-err"), daemon=True)  
        t1.start()  
        t2.start()

        proc.wait()  
        t1.join()  
        t2.join()  
          
        current\_status \= check\_bead\_status(repo\_path, bead\_id)  
        if "open" in current\_status or current\_status \== "unknown":  
            logger.warning(f"Bead {bead\_id} is still open. Agent failed its contract.")  
            with open(stderr\_path, "r", encoding="utf-8") as f:  
                err\_logs \= f.read()  
            return False, err\_logs  
              
        return True, ""  
          
    except Exception as e:  
        logger.error(f"Error executing prompt script for {bead\_id}: {e}")  
        return False, str(e)

def run\_loop(settings: Settings):  
    """Finite loop: Runs until the graph is empty or completely blocked."""  
    repo\_path \= settings.workspace  
    logger.info(f"Waking up Ralph Loop for workspace: {repo\_path}")  
    retry\_state \= {}  
    MAX\_RETRIES \= 3

    while True:  
        ready\_beads \= get\_ready\_beads(repo\_path)  
        next\_bead \= evaluate\_graph\_state(ready\_beads)  
          
        if not next\_bead:  
            logger.info(f"No ready beads found in {repo\_path}. Graph complete or blocked. Exiting loop.")  
            break   
              
        bead\_id \= next\_bead\['id'\]  
        if bead\_id not in retry\_state:  
            retry\_state\[bead\_id\] \= {'count': 0, 'logs': ""}  
              
        current\_retries \= retry\_state\[bead\_id\]\['count'\]  
        if current\_retries \>= MAX\_RETRIES:  
            logger.error(f"🚨 Bead {bead\_id} exceeded max retries. Halting loop to await human intervention.")  
            break

        success, logs \= spawn\_agent(settings, next\_bead, current\_retries, retry\_state\[bead\_id\]\['logs'\])  
          
        if success:  
            logger.info(f"✅ Successfully completed {bead\_id}.")  
            del retry\_state\[bead\_id\]  
        else:  
            logger.error(f"❌ Agent failed to complete {bead\_id}.")  
            retry\_state\[bead\_id\]\['count'\] \+= 1  
            retry\_state\[bead\_id\]\['logs'\] \= logs\[-3000:\]   
              
        time.sleep(3)

## **Phase 5: Rewiring the Webhook Receiver (runner.py)**

Modify dispatch\_to\_opencode to **synchronously** execute the incoming GitHub webhook prompt (such as /perfect-idea or /plan-to-beads), and *then* automatically trigger the Ralph Loop to process any resulting beads.

**Target File:** orchestrator-service/webhook\_receiver/runner.py

Replace the dispatch\_to\_opencode function and add the lock mechanism at the top:

\# Add this import at the top  
from webhook\_receiver.ralph\_loop import run\_loop  
import threading

\_active\_loops: set\[str\] \= set()  
\_loop\_lock \= threading.Lock()

\# ... \[existing helper functions: \_base\_args, \_prompt\_script\_invocation, etc.\] ...

def dispatch\_to\_opencode(settings: Settings, prompt: str) \-\> None:  
    """Run the initial webhook prompt, then seamlessly drain the Beads graph."""  
    workspace \= settings.workspace  
      
    with \_loop\_lock:  
        if workspace in \_active\_loops:  
            logger.info(f"Agent loop already running for workspace={workspace}. Ignoring trigger.")  
            return  
        \_active\_loops.add(workspace)

    try:  
        log\_dir \= Path(tempfile.gettempdir()) / "orchestrator-webhook"  
        log\_dir.mkdir(parents=True, exist\_ok=True)

        fd, prompt\_name \= tempfile.mkstemp(prefix="prompt-", suffix=".md", dir=log\_dir)  
        prompt\_path \= Path(prompt\_name)  
        with os.fdopen(fd, "w", encoding="utf-8") as fh:  
            fh.write(prompt)

        cmd \= \_prompt\_script\_invocation(settings, prompt\_path)

        logger.info(f"Executing webhook prompt for workspace={workspace}")  
          
        stdout\_path \= log\_dir / f"{prompt\_path.stem}.stdout"  
        stderr\_path \= log\_dir / f"{prompt\_path.stem}.stderr"  
        stdout\_file \= open(stdout\_path, "w", encoding="utf-8")  
        stderr\_file \= open(stderr\_path, "w", encoding="utf-8")

        proc \= subprocess.Popen(  
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,  
            start\_new\_session=True, text=True,  
        )

        t1 \= threading.Thread(target=\_stream\_to\_logger\_and\_file, args=(proc.stdout, stdout\_file, "opencode"), daemon=True)  
        t2 \= threading.Thread(target=\_stream\_to\_logger\_and\_file, args=(proc.stderr, stderr\_file, "opencode-err"), daemon=True)  
        t1.start()  
        t2.start()  
          
        proc.wait()  
        t1.join()  
        t2.join()

        \# NOW relentlessly drain the graph  
        logger.info(f"Webhook prompt finished. Triggering Ralph Loop for workspace={workspace}")  
        run\_loop(settings)

    except Exception as e:  
        logger.error(f"Fatal error in orchestration for {workspace}: {e}", exc\_info=True)  
    finally:  
        with \_loop\_lock:  
            if workspace in \_active\_loops:  
                \_active\_loops.remove(workspace)  
            logger.info(f"Lock released for workspace={workspace}")

## **Phase 6: Update AI Development Instructions**

We must instill a rigid API contract so the OpenCode execution agents know exactly how to interact with the Beads database when they conclude their work.

**Target File:** orchestrator-service/image/local\_ai\_instruction\_modules/ai-development-instructions.md

Append this block to the bottom:

\#\# 🛑 TASK COMPLETION CONTRACT (CRITICAL) 🛑

You are a localized worker operating within a strict graph-based execution loop.   
You do NOT have authority over the broader project plan. 

When you have completed your assigned task, and ALL local tests pass, you must follow this exact sequence:  
1\. Commit your code: \`/safe-commit\`  
2\. Mark the graph node complete: \`br close \<YOUR\_ASSIGNED\_BEAD\_ID\>\`  
3\. Exit the environment cleanly.

Failure to execute \`br close\` will result in infinite retry loops and task failure. Do NOT attempt to complete blocked tasks.

## **Completion Verification**

1. Rebuild the orchestrator container (docker compose build) to compile Cargo packages.  
2. Trigger the orchestrator by commenting /perfect-idea or /plan-to-beads on a GitHub issue.  
3. Watch the logs (docker compose logs \-f webhook) to verify app.py receives the event, runner.py executes the initial skill prompt, and ralph\_loop.py seamlessly takes over to sequentially drain the newly generated .beads graph\!