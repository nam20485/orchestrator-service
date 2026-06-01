# Development Instructions

This file provides guidance to chat clients and agents when working with code in this repository.

## Shell Environment

Default shell: **Linux bash** (WSL Ubuntu 24.04). PowerShell (pwsh) is also frequently used.

- Default to bash for terminal commands
- Use pwsh when running `.ps1` scripts or when PowerShell-specific features are needed
- Detect the current shell before running commands: `echo $0` (bash) or `$PSVersionTable` (pwsh)

## Common Development Commands

### GitHub Automation Scripts

```bash
# bash
pwsh ./scripts/import-labels.ps1
pwsh ./scripts/create-milestones.ps1
```

```powershell
# pwsh
./scripts/import-labels.ps1
./scripts/create-milestones.ps1
```

## Architecture Overview

### AI-Powered Template System

This repository is a **template for AI-assisted application development**. The architecture is built around:

1. **Remote Canonical Instructions**: Core AI instruction modules live in `nam20485/agent-instructions` repository
2. **Local Instruction Modules**: Local files in `local_ai_instruction_modules/` that reference and extend remote instructions
3. **Automation Layer**: Repository operations performed through MCP tools, `gh` CLI, terminal commands, scripts, and GitHub API
4. **Workflow Orchestration**: Dynamic workflows resolved from remote canonical sources

### Tool Preference for GitHub Operations

1. **MCP GitHub Tools** (`mcp_github_*` functions) - Use first
2. **VS Code GitHub Integration** (`run_vscode_command`) - Fallback
3. **Terminal GitHub CLI** (`gh` commands) - Last resort only
4. **Manual GitHub Web Interface** - **PROHIBITED**

### Sequential Thinking, Memory, and Gemini Tools (MANDATORY)
**Use these MCP tools for ALL complex tasks:**

1. **Sequential Thinking Tool** (`mcp_sequential_thinking_*`) - **ALWAYS USE** for:
   - Breaking down complex problems into steps
   - Planning multi-stage implementations
   - Analyzing dependencies and relationships
   - Creating systematic approaches to tasks
   - Debugging and troubleshooting workflows

2. **Memory Tool** (`mcp_memory_*`) - **ALWAYS USE** for:
   - Storing important context between tasks
   - Tracking project-specific patterns and conventions
   - Remembering user preferences and decisions
   - Maintaining state across workflow stages
   - Caching frequently referenced information

3. **Gemini Tool** (`mcp_gemini_*`) - **USE FOR CONTEXT CONSERVATION**:
   - Reading and analyzing large codebases (1M token context)
   - Processing extensive documentation or logs
   - Analyzing multiple files simultaneously
   - Conserving Claude's context window for other tasks
   - Delegating large-scale code comprehension tasks

### Key Architectural Patterns

**Remote-Local Instruction Split**:

- Remote canonical repository (`nam20485/agent-instructions`) contains authoritative workflow definitions
- Local `local_ai_instruction_modules/` contains workspace-specific references and configuration
- Never use local mirrors for workflow derivation
- When beginning a workflow, read all relevant instructions (local and remote) before planning or acting

### Core System Components

**Workflow Assignment System**:

- Assignments resolved from `nam20485/agent-instructions/ai_instruction_modules/ai-workflow-assignments/`
- Dynamic workflows in `nam20485/agent-instructions/ai_instruction_modules/ai-workflow-assignments/dynamic-workflows/`
- Always use RAW URLs when fetching remote workflow files
- Read all `ai_instruction_modules` before planning or acting

**Tool Configuration**:

- Use dynamic tool discovery to identify available capabilities
- Tool availability varies by environment — discover at runtime rather than relying on static lists

## Development Environment

- **.NET SDK**: 10.0.100 (pinned in `global.json`)
- **Shell**: bash (WSL Ubuntu 24.04) primary, pwsh also used
- **PowerShell**: 7+ for cross-platform script execution

## Critical Development Rules

1. **Shell Detection**: Check current shell (bash vs pwsh) before running commands
2. **Remote Authority**: Only use remote canonical repository files for workflow definitions
3. **Tool Priority**: Use MCP GitHub tools first, `gh` CLI as fallback, GitHub API when needed
