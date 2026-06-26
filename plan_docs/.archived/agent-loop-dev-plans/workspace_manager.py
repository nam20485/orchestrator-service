import subprocess
import os
import shutil
import logging

logger = logging.getLogger(__name__)

def create_agent_workspace(repo_path: str, bead_id: str) -> str:
    """
    Creates an isolated Git worktree for a specific task.
    This allows parallel agents to work without locking the main git index.
    """
    # Calculate an absolute path for the new worktree outside the main repo tree
    # to avoid nested repository confusion
    worktree_dir = os.path.abspath(os.path.join(repo_path, f"../worktrees/{bead_id}"))
    branch_name = f"task/{bead_id}"

    # Ensure the parent worktrees directory exists
    os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

    logger.info(f"Creating workspace for {bead_id} at {worktree_dir} on branch {branch_name}")

    # 1. Create a new branch off main (check if it exists first to handle retries cleanly)
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", branch_name], 
            cwd=repo_path, check=True, capture_output=True
        )
        logger.info(f"Branch {branch_name} already exists. Re-using.")
    except subprocess.CalledProcessError:
        # Branch doesn't exist, create it from main
        subprocess.run(["git", "branch", branch_name, "main"], cwd=repo_path, check=True)

    # 2. Attach a new worktree to that branch
    if not os.path.exists(worktree_dir):
        subprocess.run(["git", "worktree", "add", worktree_dir, branch_name], cwd=repo_path, check=True)
    else:
        logger.info(f"Worktree directory {worktree_dir} already exists.")
    
    return worktree_dir

def cleanup_agent_workspace(repo_path: str, bead_id: str, success: bool):
    """
    Tears down the worktree. Pushes branch to origin if successful, 
    nukes the branch to start fresh if it failed.
    """
    worktree_dir = os.path.abspath(os.path.join(repo_path, f"../worktrees/{bead_id}"))
    branch_name = f"task/{bead_id}"

    logger.info(f"Cleaning up workspace for {bead_id}. Success status: {success}")

    # 1. Remove the worktree definition and files
    if os.path.exists(worktree_dir):
        subprocess.run(["git", "worktree", "remove", "-f", worktree_dir], cwd=repo_path)

    if success:
        # Push successful branch up so it can be merged (or picked up by CI)
        logger.info(f"Pushing successful branch {branch_name} to origin.")
        subprocess.run(["git", "push", "origin", branch_name], cwd=repo_path)
    else:
        # Nuke the failed branch locally so the next retry starts fresh from main
        logger.warning(f"Deleting failed branch {branch_name}.")
        subprocess.run(["git", "branch", "-D", branch_name], cwd=repo_path)