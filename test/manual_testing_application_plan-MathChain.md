# MathChain – Complete Implementation

> **Purpose:** a deliberately small, made-up application plan used to manually test the
> orchestrator service flow: `/plan-to-beads` → BeadsLoop **selection** (DAG dependency
> resolution) → per-bead **execution**. It is NOT a plan for orchestrator-service itself.
>
> **How to run the manual test:**
> 1. Run the `/plan-to-beads` skill; it reads this canonical path (`plan_docs/application_plan.md`).
> 2. Observe the BeadsLoop drain the DAG in order:
>    **bead 1 (Contracts)** → **beads 2 & 3 (BasicOps + PowerOps, parallel-eligible)** →
>    **bead 4 (Client SDK)** → **bead 5 (Blazor)** → **bead 6 (Tests + Compose)**.
>    A working selector runs this order and unblocks 2 & 3 together; a broken one runs out
>    of order, skips, or stalls. Beads that fail when the .NET SDK is absent are an
>    observable signal, not a defect of the plan.

## Overview

MathChain is a small microservices system that exposes basic math operations over REST and
allows the results of one operation to be **chained** into another — e.g. computing the
square root of `a^2 + b^2` by feeding each operation's result into the next call. It
comprises a shared contracts library, two per-category math microservices, a typed HTTP
client SDK that makes chaining ergonomic, and a Blazor WebAssembly frontend. The system is
stateless and small enough to build and run in Docker Compose.

## Goals

- Provide composable math operations (basic arithmetic + power/root) as separate REST services.
- Enable client-side chaining of operation results across services via a typed SDK.
- Serve as a compact reference microservices app to exercise the Beads DAG loop end-to-end.

## Technology Stack

- Language: C# .NET 8.0 LTS
- UI Framework: Blazor WebAssembly
- Architecture: Microservices (per-operation-group services) + shared contracts + typed client SDK enabling client-side chaining
- Databases/Storage: none (stateless)
- Logging/Observability: ASP.NET Core logging (Serilog optional)
- Containerization/Infra: Docker, Docker Compose

## Application Features

- Basic arithmetic operations: add, subtract, multiply, divide
- Power operations: power, square, square-root
- Typed client SDK so callers chain results, e.g. `client.SqrtAsync(client.PowerAsync(a,2).Value + client.PowerAsync(b,2).Value)`
- Blazor WASM UI including a Pythagorean chaining demo (`sqrt(a^2 + b^2)`)
- Docker Compose orchestration of all services + frontend

## System Architecture

### Core Services

1. **MathChain.BasicOps** — arithmetic REST service (`:5001`): add/subtract/multiply/divide.
2. **MathChain.PowerOps** — power REST service (`:5002`): power/square/square-root.
3. **MathChain.Client** — typed `HttpClient` SDK over both services; consumer-side chaining.
4. **MathChain.Web** — Blazor WASM frontend; consumes `MathChain.Client`.

### Key Features (system-level)

- A single shared `OperationResult` contract makes every operation's output uniform, so one
  result's `.Value` feeds the next call (chaining).
- Edge cases (divide by zero, square root of a negative) return `Success=false` with an
  `Error` message — no unhandled exceptions.
- Chaining is client-side; no server-side API gateway required.

## Project Structure

```
mathchain/
├─ MathChain.sln
├─ docker-compose.yml
├─ global.json
├─ docs/
├─ src/
│  ├─ MathChain.Contracts/        (shared DTOs)
│  ├─ MathChain.BasicOps/         (arithmetic microservice :5001)
│  ├─ MathChain.PowerOps/         (power microservice :5002)
│  ├─ MathChain.Client/           (typed chaining HttpClient SDK)
│  └─ MathChain.Web/              (Blazor WASM frontend)
└─ tests/
   └─ MathChain.Tests/            (xUnit unit + integration)
```

---

## Implementation Plan

### Phase 1: Foundation & Shared Contracts

- [ ] 1.1. **Create MathChain.Contracts shared DTO library**
  - **Context:** Every service and the client SDK share the same request/response types so an
    operation's numeric result can feed into another operation (chaining). This is the
    foundation every other bead depends on.
  - **Acceptance Criteria:**
    - Create class library `src/MathChain.Contracts/MathChain.Contracts.csproj`.
    - Define records: `UnaryOperationRequest(double Value)`,
      `BinaryOperationRequest(double Left, double Right)`,
      `OperationResult(double Value, string Operation, bool Success, string? Error)`.
    - Create solution `MathChain.sln` at repo root with this project added.
    - `dotnet build` succeeds with no warnings-as-errors failures.
  - **Validation:** `dotnet build MathChain.sln`
  - **Priority:** 1
  - **Dependencies:** none

### Phase 2: Core Math Microservices

- [ ] 2.1. **Implement MathChain.BasicOps microservice**
  - **Context:** HTTP service exposing basic arithmetic so results can be chained downstream.
    Depends on the shared contracts.
  - **Acceptance Criteria:**
    - ASP.NET Core minimal API at `src/MathChain.BasicOps/MathChain.BasicOps.csproj`,
      listening on `:5001` (configurable).
    - Endpoints `POST /add`, `/subtract`, `/multiply`, `/divide` accepting
      `BinaryOperationRequest` and returning `OperationResult`.
    - Division by zero returns `Success=false` with an `Error` message (no exception/crash).
    - References `MathChain.Contracts`; `dotnet build` succeeds.
  - **Validation:** `dotnet build src/MathChain.BasicOps`
  - **Priority:** 2
  - **Dependencies:** 1.1

- [ ] 2.2. **Implement MathChain.PowerOps microservice**
  - **Context:** HTTP service exposing exponent/power operations for chaining. Depends on the
    shared contracts and is independent of (parallel-eligible with) BasicOps.
  - **Acceptance Criteria:**
    - ASP.NET Core minimal API at `src/MathChain.PowerOps/MathChain.PowerOps.csproj`,
      listening on `:5002` (configurable).
    - Endpoints `POST /power` (`Left^Right`), `POST /square` (`Value^2`),
      `POST /sqrt` (`sqrt(Value)`), each accepting the matching request type and returning
      `OperationResult`.
    - Square root of a negative returns `Success=false` with an `Error` message.
    - References `MathChain.Contracts`; `dotnet build` succeeds.
  - **Validation:** `dotnet build src/MathChain.PowerOps`
  - **Priority:** 2
  - **Dependencies:** 1.1

### Phase 3: Chaining SDK

- [ ] 3.1. **Implement MathChain.Client typed chaining SDK**
  - **Context:** A typed `HttpClient` wrapper over both services so callers chain results,
    e.g. `client.SqrtAsync(client.PowerAsync(a,2).Value + client.PowerAsync(b,2).Value)`.
    Depends on both services' endpoints/contracts.
  - **Acceptance Criteria:**
    - Class library `src/MathChain.Client/MathChain.Client.csproj` referencing
      `MathChain.Contracts`.
    - Interface `IMathChainClient` with methods `AddAsync`, `SubtractAsync`,
      `MultiplyAsync`, `DivideAsync`, `PowerAsync`, `SquareAsync`, `SqrtAsync` — each
      returning `OperationResult`.
    - Implementation `MathChainClient(HttpClient)` calls BasicOps (`:5001`) and PowerOps
      (`:5002`) via JSON.
    - Service base URLs configurable (options/`IConfiguration`/constants).
    - `dotnet build` succeeds.
  - **Validation:** `dotnet build src/MathChain.Client`
  - **Priority:** 3
  - **Dependencies:** 2.1, 2.2

### Phase 4: Frontend

- [ ] 4.1. **Implement MathChain.Web Blazor WASM frontend**
  - **Context:** Browser UI that consumes the typed client SDK to perform operations and
    demonstrate chaining. Depends on the client SDK.
  - **Acceptance Criteria:**
    - Blazor WASM app at `src/MathChain.Web/MathChain.Web.csproj` referencing
      `MathChain.Client`.
    - UI to choose an operation + operands and display the returned `OperationResult`.
    - A "Pythagorean" demo computing `sqrt(a^2 + b^2)` via chained client calls and showing
      the intermediate results.
    - `dotnet build` succeeds.
  - **Validation:** `dotnet build src/MathChain.Web`
  - **Priority:** 4
  - **Dependencies:** 3.1

### Phase 5: Testing & Orchestration

- [ ] 5.1. **Add tests and Docker Compose orchestration**
  - **Context:** Verify individual services, the chaining client, and run everything together.
    Depends on all prior beads.
  - **Acceptance Criteria:**
    - xUnit project `tests/MathChain.Tests/MathChain.Tests.csproj`.
    - Unit tests for BasicOps and PowerOps endpoints (via `WebApplicationFactory`).
    - One integration test asserting
      `client.SqrtAsync(client.PowerAsync(3,2).Value + client.PowerAsync(4,2).Value).Value ≈ 5`.
    - `docker-compose.yml` running BasicOps (`:5001`), PowerOps (`:5002`), and
      `MathChain.Web`; CORS configured so the WASM client may call the services.
    - `dotnet test` passes; `docker compose config` validates.
  - **Validation:** `dotnet test MathChain.sln`
  - **Priority:** 5
  - **Dependencies:** 2.1, 2.2, 3.1, 4.1

---

## Mandatory Requirements Implementation

### Testing & Quality Assurance

- [ ] Unit tests — coverage target: 80%+ for BasicOps, PowerOps, and Client
- [ ] Integration test for the Pythagorean chaining path
- [ ] `docker compose config` validates the orchestration file

### Documentation & UX

- [ ] README with build/run instructions and the chaining example
- [ ] Inline XML docs on public `IMathChainClient` methods

### Build & Distribution

- [ ] `MathChain.sln` builds all projects via `dotnet build`
- [ ] Docker Compose brings up all services + frontend

### Infrastructure & DevOps

- [ ] CI workflow (build + test) if added to a GitHub repo — pin any Actions by full SHA

---

## Acceptance Criteria

- [ ] All projects build via `dotnet build MathChain.sln`
- [ ] Chaining works: `sqrt(power(a,2) + power(b,2))` returns the correct result
- [ ] Division by zero and square root of a negative return `Success=false` gracefully
- [ ] Blazor UI renders and demonstrates the Pythagorean chain
- [ ] `dotnet test` is green; `docker compose up` runs all services

## Risk Mitigation Strategies

| Risk | Mitigation |
|------|------------|
| .NET SDK absent in the bead execution environment | Install the SDK in the worktree, or expect the build validation to fail — observe loop behavior |
| Port conflict on `:5001` / `:5002` | Make ports configurable via `appsettings.json` / environment |
| Blazor WASM blocked by CORS when calling services | Configure permissive CORS on both services for the frontend origin |

## Success Metrics

- All 6 beads drain in dependency order with beads 2 & 3 unblocked together
- End-to-end chaining returns correct results (`sqrt(3^2+4^2) = 5`)
- All services run under Docker Compose without manual intervention

## Repository Branch

Target branch for implementation: `main` (or `feature/mathchain`)

## Implementation Notes

- This plan is intentionally small to manually validate the BeadsLoop **selection** and
  **execution**. Some beads may fail if the .NET SDK is not present in the workspace — that
  is an observable signal of the loop's behavior, not a defect in this plan.
- Chaining is client-side (results flow through `MathChain.Client`); no server-side gateway
  is required.
- The shared `OperationResult` contract is the single enabler of chaining — every operation
  returns the same shape, so any `.Value` can become the next operation's input.
