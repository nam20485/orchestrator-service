# **Research: Beads DAG Persistence in Kubernetes**

## **The Macro-Architecture Shift: Decider vs. Doer**

**The Goal**: Externalize DAG state so orchestrator pods can scale horizontally and remain stateless.

**The Constraint**: "Forking beads\_rust is unmaintainable."

Initially, we viewed this as a *storage* problem: "How do 10 stateless worker pods safely read/write to one SQLite file at the same time?"

If we look at your target architecture (which introduces **Redis** for EventStore), this is actually a *distribution* problem. We don't need 10 pods to read the SQLite file. We only need **one** pod to read the file, and 10 pods to do the work it assigns.

By separating the "Decider" (Graph Math) from the "Doers" (Agent Workspaces), we can solve this natively using the infrastructure you're already deploying.

Here are the paths, reframed with the whole system in mind.

## **Option 1: The Dispatcher/Worker Queue (Redis-Backed) \- HIGHLY RECOMMENDED**

Since you are already deploying Upstash Redis for your SSE EventStore, you can use it as a task queue.

You split the architecture into two distinct roles:

1. **The Dispatcher (Single Pod, Stateful):** Runs an endless loop. It holds the .beads.db on a persistent volume. It queries bvr \--robot-next. It pushes jobs to a **Redis Queue** and listens for completions to run br close.  
2. **The Workers (N Pods, 100% Stateless):** These are your horizontally scaled orchestrator-service pods. They pull from the Redis Queue, create ephemeral workspaces, do the agent work, and report back.

### **The "In-Flight" Retry Problem (Bridging Beads and Redis)**

**The Challenge:** beads handles failures elegantly: if a task fails, it never gets closed, so it remains "ready". However, if the Dispatcher constantly asks bvr for the next task while a worker is busy processing it, bvr will keep suggesting the exact same bead, resulting in duplicate jobs in the queue.

**The Solution:** The Dispatcher must track "In-Flight" beads using Redis TTL (Time-To-Live) keys to bridge the gap between Beads' state and the stateless workers.

1. **Claiming:** When the Dispatcher gets Bead 123 from bvr, it checks Redis if in\_flight:123 exists.  
2. **Locking:** If it doesn't exist, it sets in\_flight:123 with a TTL (e.g., 30 minutes) and pushes the job to the queue.  
3. **Skipping:** If bvr suggests Bead 123 on the next loop, the Dispatcher sees the in\_flight key and simply skips it, asking bvr for the *next* best bead instead.  
4. **Native Retry (The Magic):** If a worker pod crashes and fails to complete the job, it never sends the "done" message. After 30 minutes, the Redis in\_flight:123 key automatically expires. On the next loop, the Dispatcher sees it's no longer in flight, accepts bvr's suggestion, and safely re-queues the failed bead.

### **Pros**

* **Native to your Stack:** Reuses the Redis instance you already planned for EventStore.  
* **True Statelessness:** Worker pods are entirely decoupled from the DAG logic.  
* **Preserves Beads Retry Logic:** By using TTLs, you retain beads\_rust's natural ability to hold tasks open until they are truly resolved.  
* **No API Overhead:** Event-driven (push) is much more efficient than having N workers constantly polling an HTTP API.

### **Cons**

* **Stateful Dispatcher:** You still have one stateful pod (the Dispatcher). However, if it dies, workers currently processing jobs are unaffected, and it resumes safely upon k8s restart.

## **Option 2: "Beads-as-a-Service" (The API Wrapper)**

You turn the CLI tool into a dedicated microservice. Instead of a queue pushing work, the stateless worker pods constantly poll a central REST API (GET /next) to ask for work. When they finish, they make a POST /close/{bead\_id} request.

### **Pros**

* **Conceptually Simple:** Easy to write a FastAPI wrapper around CLI commands.  
* **Centralized State:** Eliminates concurrent write/lock issues.

### **Cons**

* **Race Conditions:** If 3 workers poll GET /next at the same exact second, they will all receive the same bead. You would *still* have to build the "In-Flight" locking mechanism mentioned in Option 1 into the API.  
* **Polling Overhead:** N worker pods constantly polling creates unnecessary HTTP traffic compared to a Redis queue.

## **Option 3: Pure Postgres Rewrite (The "Drop SQLite" Route)**

Abandon the beads\_rust CLI entirely and port its logic to Neon Postgres.

### **Pros**

* **Standard Infrastructure:** Everything lives natively in Postgres. No files to manage.

### **Cons**

* **Violates Constraints:** Replicating PageRank, betweenness centrality, and blocker ratio in raw SQL or Python violates the "do not fork/rewrite" rule and requires massive maintenance overhead.

## **Final Recommendation & Next Steps**

**Adopt Option 1 (The Dispatcher/Worker Queue using Redis).** By looking at your architecture holistically, we realize that beads\_rust doesn't need to be distributed—only the *work it generates* needs to be distributed. Since you are already adding Redis to webhook-receiver, leveraging it as a job queue provides a robust pattern. Adding a simple Redis TTL for "in-flight" tracking perfectly marries beads' native DAG retry logic with modern distributed worker queues.

### **Implementation Plan:**

1. **Redefine BeadsLoop:** Refactor it to be the "Dispatcher". It runs in a single replica pod, queries bvr, checks/sets in\_flight Redis keys, and pushes to the job queue.  
2. **Redefine Orchestrator:** Update the orchestrator-service to be a pure queue consumer. It pulls from Redis, creates the ephemeral /tmp/bead-\<id\> workspace, does the work, and publishes the result back to Redis.  
3. **Database Role:** Neon Postgres can still be used for application metadata, but .beads.db remains a robust local file attached only to the Dispatcher pod.