# **Agentic Requirements Gathering: The "Idea Perfector"**

## **1\. The Vision**

The most advanced autonomous coding systems do not immediately write code from a single-sentence prompt. They begin with an interactive **Requirements Gathering** phase.

This document defines a new upstream workflow layer: the **Idea Perfector**. It acts as a Staff Engineer and Technical Product Manager. Its goal is to take a loose, unstructured idea from a human user, interrogate it to resolve ambiguities, and output a highly formal, gold-standard application_plan.md that can feed directly into the downstream plan-to-beads automation.

## **2\. The Abstraction Pipeline**

With this new layer, the complete software factory pipeline operates at three distinct levels of abstraction:

| Phase | Actor | Input | Action | Output |
| :---- | :---- | :---- | :---- | :---- |
| **1\. Ideation** | /perfect-idea (PM Agent) | A loose human idea & interactive chat. | Interrogates constraints, architecture, and edge cases. | application_plan.md |
| **2\. Planning** | /plan-to-beads (Scrum Agent) | application_plan.md | Derives Epics/Stories/ACs and maps dependencies. | .beads/ Graph DAG |
| **3\. Execution** | ralph\_loop.py (Engineers) | .beads/ Graph DAG | Spawns isolated agents to write code, test, and close beads. | Working Software |

## **3\. The Interactive Workflow**

The Idea Perfector is designed to be highly conversational. It does not output the plan immediately.

1. **The Trigger:** The user runs the skill with a seed idea:  
   *User: "I want to build a webhook receiver in Python that listens to GitHub and updates a local database."*  
2. **The Interrogation:** The agent responds with targeted questions:  
   *Agent: "Great idea. Before I draft the plan, I need to clarify: 1\. Which database (SQLite, Postgres)? 2\. Do we need strict X-Hub-Signature validation? 3\. Are there rate-limiting concerns?"*  
3. **The Resolution:** The user answers the questions in a back-and-forth chat.  
4. **The Generation:** Once the agent determines it has enough architectural clarity, it generates the formal application_plan.md file (matching the Foxtrot54 gold standard) and saves it to the repo.  
5. **The Handoff:** The agent concludes by asking the user to review the generated file and type /plan-to-beads when they are ready to commence development.

## **4\. The Skill Implementation**

To achieve this, we create a new skill in the Orchestrator service.

**Target File:** .agents/skills/perfect-idea/SKILL.md

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
2\. Identify architectural gaps, missing constraints, or vague requirements. (e.g., "What is the specific tech stack?", "How are we handling authentication?", "What are the scaling constraints?")  
3\. Ask the user 3 to 5 highly specific, numbered questions to resolve these gaps.  
4\. Wait for the user to reply. Iterate on this process until you are confident you have a complete mental model of the application's architecture, phases, and high-level deliverables.

\#\#\# Phase 2: Generation (File Output)  
Once the user has answered your questions and you have enough clarity:  
1\. Announce that you are generating the application plan.  
2\. Read the structural template at \`docs/application_plan_template.md\`. This defines the exact section structure your output must follow.  
3\. Generate the formal \`application_plan.md\` by filling in every section of the template with the specific, detailed information gathered during the interrogation phase. Do NOT leave any template sections as placeholder text — replace all brackets and placeholders with concrete, project-specific content.  
4\. Write the completed, filled-in document to \`plan\_docs/application_plan.md\`.

\#\#\# Phase 3: Handoff  
After generating and saving the file, tell the user:  
\*"I have generated the formal application plan at \`plan\_docs/application_plan.md\`. Please review it. If it looks correct, reply with \`/plan-to-beads\` to convert this plan into an executable task graph."\*  
\</instructions\>

## **5\. Why this Separation Matters**

By keeping the **Idea Perfector** separate from the **Plan-to-Beads** generator, we achieve a critical human-in-the-loop safety gate:

1. The LLM can wildly hallucinate during the brainstorming/ideation chat.  
2. The user forces the LLM to write down its final conclusions in a human-readable Markdown file (application_plan.md).  
3. The user gets to **read and manually edit** that Markdown file, correcting any bad assumptions.  
4. Only *after* the human approves the architecture does the system lock it into the rigid mathematical graph (.beads/) for autonomous execution.