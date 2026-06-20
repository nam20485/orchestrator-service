import subprocess
import json
import logging
import os

logger = logging.getLogger(__name__)

def get_ready_beads(repo_path: str) -> list:
    """Queries bvr for all unblocked tasks."""
    try:
        result = subprocess.run(
            ["bvr", "--robot-ready", "--json"],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        if not result.stdout.strip():
            return []
        return json.loads(result.stdout)
    except Exception as e:
        logger.error(f"Failed to query bvr: {e}")
        return []

def evaluate_graph_state(beads: list) -> dict | None:
    """Selects the highest priority unblocked task."""
    if not beads:
        return None
    return sorted(beads, key=lambda b: b.get('priority', 999))[0]

def check_bead_status(repo_path: str, bead_id: str) -> str:
    """The Ultimate Source of Truth: checks if the bead was actually closed."""
    try:
        result = subprocess.run(
            ["br", "status", bead_id],
            cwd=repo_path, capture_output=True, text=True, check=True
        )
        return result.stdout.strip().lower()
    except subprocess.CalledProcessError:
        return "unknown"

def spawn_agent(repo_path: str, bead: dict, retry_count: int, previous_logs: str = "") -> tuple[bool, str]:
    """Injects a prompt into the currently running orchestration-service."""
    bead_id = bead.get('id')
    title = bead.get('title')
    description = bead.get('description', '')
    
    prompt = f"You have been assigned Bead {bead_id}: {title}.\n\nContext:\n{description}\n"
    
    if previous_logs:
        prompt += f"\n\n🛑 WARNING: Your previous attempt failed with the following errors 🛑\n{previous_logs}\n\nAnalyze these errors, fix the code, and run `br close {bead_id}`."
    else:
        prompt += f"\n\nWhen completed and ALL tests pass, you MUST run: `br close {bead_id}`."

    logger.info(f"Injecting prompt for {bead_id} into service (Attempt {retry_count + 1}).")
    
    try:
        prompt_script = os.path.join("scripts", "prompt.ps1")
        cmd = ["pwsh", "-NonInteractive", "-File", prompt_script, "-Prompt", prompt]
        
        result = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
        logger.info(f"Prompt script for {bead_id} exited with code {result.returncode}")
        
        current_status = check_bead_status(repo_path, bead_id)
        if "open" in current_status or current_status == "unknown":
            logger.warning(f"Bead {bead_id} is still open. Agent failed its contract.")
            return False, (result.stderr if result.stderr.strip() else result.stdout)
            
        return True, ""
        
    except Exception as e:
        logger.error(f"Error executing prompt script for {bead_id}: {e}")
        return False, str(e)

def run_loop(repo_path: str):
    """
    Finite loop: Runs until the graph is empty or completely blocked.
    Invoked by runner.py whenever a webhook arrives.
    """
    logger.info(f"Waking up Ralph Loop for workspace: {repo_path}")
    retry_state = {}
    MAX_RETRIES = 3

    while True:
        ready_beads = get_ready_beads(repo_path)
        next_bead = evaluate_graph_state(ready_beads)
        
        if not next_bead:
            logger.info("No ready beads found. Graph is complete or blocked. Exiting loop.")
            break # Exit the loop so the background thread completes
            
        bead_id = next_bead['id']
        
        if bead_id not in retry_state:
            retry_state[bead_id] = {'count': 0, 'logs': ""}
            
        current_retries = retry_state[bead_id]['count']
        
        if current_retries >= MAX_RETRIES:
            logger.error(f"🚨 Bead {bead_id} exceeded max retries. Halting loop to await human intervention.")
            break # Stop loop if we hit a hard failure wall

        # Execute task
        success, logs = spawn_agent(repo_path, next_bead, current_retries, retry_state[bead_id]['logs'])
        
        if success:
            logger.info(f"✅ Successfully completed {bead_id}.")
            del retry_state[bead_id]
        else:
            logger.error(f"❌ Agent failed to complete {bead_id}.")
            retry_state[bead_id]['count'] += 1
            retry_state[bead_id]['logs'] = logs[-3000:] # Keep log context bounded