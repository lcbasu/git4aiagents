# git4aiagents

**Git was designed for humans. Agents need something fundamentally different.**

---

## Why this exists

I hit this problem firsthand while building an open source agentic operating system. Eight repos, multiple Claude Code instances running in parallel. I spent more time resolving git conflicts and managing PR merges than on actual architecture decisions. The same pattern shows up everywhere. Teams across every place I've worked at recently are dealing with the exact same thing: agents writing code faster than humans can review it.

These agents are becoming autonomous. They will plan, write, test, and ship code without waiting for a human to type a prompt each time. That transition is happening right now. And Git is not ready for it.

## The real problem

Agents didn't create this problem. They just made it impossible to ignore.

Every developer has lived this. You open a PR. CI is green. No merge conflicts. You merge confidently. And you break production. Because someone else's PR changed a function signature or shifted what a return value means in something you depend on. Git told you everything was fine because **Git checks lines, not meaning.**

Look at the band-aids: CI pipelines, required status checks, "rebase on main" policies, merge queues. All exist because Git's merge model is semantically blind. At human scale (5-10 PRs a day), CI catches most collisions. At agent scale (100s of commits every few minutes), the band-aids collapse. You can't run full CI at that velocity. You can't have humans eyeballing every merge.

**Git has always been broken for code. Humans were just slow enough to work around it.** Branch-per-agent gives you thousands of divergent realities. PR review becomes a single-threaded bottleneck choking a massively parallel system. **Code is no longer something humans carefully craft. Code is high-velocity data.** It should be stored and validated like data.

If that sounds obvious, ask yourself: why is every tool in the market still built on top of Git?

## 14 foundational design decisions

These are the questions I went through before writing a single line of code. Every one affects the data model, and the data model is the one thing you can't change later without rebuilding everything.

### 1. What is the unit of work?

Git's commit bundles a snapshot of the entire repo. For agents that might change one line or refactor 40 files in 3 seconds, the granularity is always wrong.

**The answer is two units.** The **atomic unit** is the *semantic mutation*: a single meaningful change to a single semantic element. Not "line 47 changed" but "function `calculateTotal` signature changed from `(items: Item[]) -> int` to `(items: Item[], currency: Currency) -> Decimal`." Structural, not textual.

The **logical unit** is the *task*: a DAG of semantic mutations that together accomplish something. "Implement multi-currency support" = 15 mutations across 8 files. Tasks are what agents get assigned, humans review, and the system rolls back.

Why two levels: an agent's task might have 14 clean mutations and 1 conflicting one. Instead of rejecting the whole thing, the system pinpoints exactly which mutation conflicts. The agent fixes just that one.

**What Git gets wrong:** "fixed the bug AND ran the linter AND renamed that variable" as one commit. Need to revert just the bug fix? Good luck. Here, those are three separate mutations, independently addressable.

**Data model implication:** storage isn't files and lines. It's a graph of semantic elements with mutation history on each node. This is why database-native storage is the right call. You literally need graph queries and transactional semantics on this structure.

### 2. What is the coordination model?

**Serializable Snapshot Isolation (SSI) over a semantic dependency graph.**

1. **Task assignment.** System computes the *likely impact zone* from the dependency graph. An estimate, not a lock.
2. **Snapshot.** Agent gets a consistent snapshot of the impact zone + transitive dependencies. Cheap because you're querying a database, not cloning a repo.
3. **Work.** Agent works independently against its snapshot. No coordination overhead.
4. **Commit validation.** Three layers:
   - **Structural conflict.** Did another agent modify the same semantic element? Hard conflict, first-writer-wins.
   - **Interface contract.** Did any element I *depend on* change its interface? The dependency graph catches this instantly, no compile needed.
   - **Behavioral validation.** Does the combined state pass targeted tests? Only tests covering the changed subgraph, not the full suite.

**Why not alternatives:** Locks serialize agents. CRDTs don't work because code merges aren't commutative (`addParameter(x)` and `removeFunction(x)` don't compose). OT misses semantic conflicts.

**Throughput math:** 1000 semantic elements, 20 agents, ~10 elements per task = roughly 19% overlap chance per commit. Detection is O(edges) not O(codebase). Near-linear scaling until the dependency graph is so interconnected that impact zones all overlap. At that point you have an architecture problem, not a tooling problem.

**Livelock risk:** when Layer 1 rejects a mutation, the agent redoes work. Two agents repeatedly conflicting = livelock. Fix: priority ordering (higher-priority task wins) + *impact zone advisories* (soft hints, not locks).

### 3. What is the consistency model?

**Strong consistency within a dependency subgraph, eventual consistency across independent subgraphs.**

If Agent A is on payments and Agent B is on onboarding with no shared dependencies, eventual consistency is fine. But if A changes a shared utility that B depends on, B needs to know *before it commits*. The dependency graph naturally partitions into clusters. Within a cluster, strong consistency. Across clusters, eventual consistency with conflict detection at commit time.

**Shared utilities edge case:** `utils.ts` is a high-centrality node. Don't make it a single serialization point. Decompose at function-level granularity. `utils.formatDate` and `utils.formatCurrency` are independent elements.

**Streaming validation:** instead of discovering conflicts at commit time (wasting minutes of work), the system continuously checks snapshot dependencies. If something changes mid-task, the agent gets an interrupt and adapts. A major advantage over Git where you only discover conflicts at merge time.

### 4. What does the reasoning trace look like?

This is the moat. Code that knows *why* it exists.

**Three tiers:**

**Tier 1, Structured metadata (always stored).** `intent`, `confidence`, `alternatives_considered`, `dependencies_read`, `constraints_applied`. About 200 bytes per mutation, ~288MB/day at scale. Trivial. This is your dashboard and audit trail.

**Tier 2, Structured rationale (non-trivial mutations only).** Why this approach, why not alternatives, explicit assumptions. ~100MB/day. What a human reads during review instead of guessing from a diff.

**Tier 3, Full reasoning dump (ephemeral, 7-day retention).** Raw LLM chain for forensics. ~49GB rolling. Cold storage.

**The killer query:** "Why does this function exist?" Today you grep commit messages, PR descriptions, Slack, Jira. Here, you traverse the reasoning trace: created by Task X, because of reasoning Y, depends on A and B, assumptions are C.

### 5. How do humans interact?

Most "AI-first" tools build for the machine and bolt on a human interface as an afterthought. Humans are the principal. Agents are the executors.

**Humans don't review code. Humans set policy and review outcomes.** At 1000+ mutations per minute, line-by-line review is dead. Humans operate at three levels:

**Level 1, Policy definition.** Machine-enforceable constraints checked on every mutation. "All API endpoints validate with Zod." "No changes to `auth` without human approval."

**Level 2, Task-level review.** Review completed tasks, not individual mutations. Presented as: semantic summary, reasoning traces, test results, confidence scores, assumptions.

**Level 3, Anomaly-driven intervention.** System surfaces risk: low confidence, too many retries, architectural violations, cascading dependency changes. Humans jump in when flagged, not on a fixed cadence.

**The UI is not a diff viewer.** It's an air traffic control dashboard. Humans direct traffic, not read every line.

### 6. What is the migration story?

Every potential user has an existing Git repo. If this requires a clean-room start, adoption is dead.

**The bridge:** ingest from and export to Git as an I/O adapter. Import via tree-sitter + LSP into semantic graph. Export as conventional Git commits with diffs and commit messages synthesized from reasoning traces. Teams adopt incrementally. Eventually, the new system *is* the source of record.

### 7. What is the failure and recovery model?

**Agent crash:** every mutation is individually recorded, so no "half-committed" state. Another agent picks up the task, reads existing mutations + reasoning traces, continues.

**Semantic regression:** system traces the causal chain from task to mutations to affected code paths. Rollback is targeted, not "revert the whole commit and the 5 after it."

**Cascading failure:** Agent A's change gets rolled back after agents B, C, D adapted to it. The dependency graph detects orphaned downstream mutations immediately and flags them for re-evaluation.

In Git, rollback is "reverse the diff." Here, rollback is "undo this decision and everything downstream of it."

### 8. What is the identity and trust model?

In Git, every commit is signed by a human with a GPG key. In this system, "claude-code-7a3f" made a change. Who controls that agent? What permissions does it have?

**Three layers:**

**Agent registration.** Every agent gets a cryptographic identity. Every mutation is signed. You can prove which agent instance produced every change. This matters for audit, compliance, and debugging.

**Capability scoping.** An agent doesn't get blanket write access. It gets a capability token per task: "you can write to /payments/*, read but not write /auth/*, cannot access /infrastructure/ at all." An agent working on "add multi-currency support" has no business touching the auth module. Capability scoping bounds the blast radius of a confused agent.

**Trust scoring.** The system builds a track record for each agent. Agents whose mutations pass validation, whose confidence scores correlate with quality, and whose changes rarely get rolled back earn higher trust. Low-trust agents get tighter scopes and lower conflict priority. This evolves automatically from the data.

### 9. What is the semantic parsing model?

The unit of work is a "semantic mutation" on a "semantic element." But how does the system know what the semantic elements are?

**tree-sitter for block-level parsing.** You don't need a full AST. You need to identify blocks (functions, classes, top-level declarations) and their boundaries. tree-sitter does this in milliseconds, supports 100+ languages, and gives you a uniform representation. The system stores a block index (which blocks exist, their type, name, byte range, dependencies) and block content (raw text, versioned). Mutations are "block X changed from content A to content B." Coordination operates at block level.

**Agent-declared scopes as supplementary signal.** Agents already know the semantic scope of their mutations ("I'm modifying function `calculateTotal`"). The system uses this declaration, verified against the actual diff at commit time.

**POC path:** agent-declared scopes + tree-sitter verification. For production: tree-sitter as primary, agent declarations as metadata.

### 10. What is the dependency graph model?

The coordination model (Q2) and consistency model (Q3) both depend on a dependency graph. How is it built?

**Multi-signal dependency inference:**
- **Explicit imports.** If file A imports from file B, A depends on B. Catches 80% of dependencies.
- **Symbol references.** Function X calls function Y, extracted via tree-sitter.
- **Agent-declared dependencies.** When an agent reads a block before mutating, it declares the read. Stored in the reasoning trace.
- **Test coverage mapping.** If test T exercises blocks X and Y, they're behaviorally coupled. Changes to X should re-validate Y.

The graph is append-only and probabilistic. False positives cause harmless re-validation. False negatives are caught by behavioral validation (tests). A large codebase (100K elements, 500K edges) fits in memory. Graph queries are milliseconds.

### 11. What is the task decomposition model?

Who creates tasks? How are they decomposed? How are they assigned?

**Human creates a high-level objective.** "Add multi-currency support to checkout."

**Planner agent decomposes into a task DAG.** Reads the objective, analyzes the codebase via semantic + dependency graphs, produces ordered tasks with dependencies. Tasks 1 and 4 might run in parallel. Task 3 waits for Task 2. This is where the 1000x efficiency comes from: all independent tasks run in parallel, with agents picking up work the instant dependencies are satisfied.

**The planner is the most underrated component.** A bad planner creates tasks that are too large (high conflict probability), too small (coordination overhead exceeds benefit), or poorly decomposed (circular dependencies, missing steps).

**POC path:** human defines tasks manually. The system's value is still demonstrated by parallel execution, conflict resolution, and reasoning traces.

### 12. What is the observability model?

At 1000+ mutations per minute, you need to know what's happening without drowning in data.

**Three surfaces:**
- **System health (for operators).** Agent count, mutation rate, conflict rate, validation pass rate, lock contention, latency. Alert on anomalies.
- **Task progress (for stakeholders).** Task DAG visualization, completion status, confidence scores. Non-technical humans can understand it.
- **Decision audit (for debugging).** Full reasoning graph traversal. Why does the code look like this? The forensic view.

**Event streaming, not polling.** Every mutation emits an event. Dashboard, agents, and observability all subscribe to the same event bus.

### 13. What is the testing model?

In Git, testing is external (push, CI runs, wait). Here, testing is internal, part of the mutation commit pipeline.

**Three tiers:**
- **Syntactic validation (every mutation, <100ms).** Does it parse? Pass lint? tree-sitter + linters. Reject before commit.
- **Targeted unit tests (every task completion, <30s).** Only tests covering affected blocks via test coverage mapping. Not the full suite.
- **Integration tests (periodic, minutes).** Full suite on a cadence. Results identify which recent tasks touched failing code paths.

**Testing is a resource allocation problem.** Finite compute, so prioritize high-risk changes: low confidence scores, high dependency count, critical modules, low-trust agents.

### 14. What is the versioning and release model?

Git has tags and branches for releases. What's the equivalent in a continuous, branchless system?

**Named snapshots with semantic guarantees.** A "release" is a point-in-time snapshot of the semantic graph where all tests pass, no policy violations, no open flags. The system computes this automatically.

**Environments as snapshot pointers.** Production runs snapshot X. Staging runs the latest validated snapshot. Promoting is moving a pointer, not deploying code.

**Rollback is pointer movement + targeted mutation reversal.** And every release carries full provenance: which tasks, which agents, what reasoning, what tests validated it. "What's in this release and why" is a single query.

## Summary

| # | Question | Core decision |
|---|---|---|
| 1 | Unit of work | Semantic mutation + task DAG |
| 2 | Coordination | SSI over dependency graph |
| 3 | Consistency | Strong within clusters, eventual across |
| 4 | Reasoning trace | 3-tier: metadata, rationale, full dump |
| 5 | Human interaction | Policy + task review + anomaly intervention |
| 6 | Migration | Git bridge: import/export adapter |
| 7 | Failure recovery | Dependency-aware targeted rollback |
| 8 | Identity and trust | Crypto identity + capability scoping + trust scoring |
| 9 | Semantic parsing | tree-sitter blocks + agent declarations |
| 10 | Dependency graph | Multi-signal: imports, refs, agent behavior, tests |
| 11 | Task decomposition | Planner agent + scheduler + task DAG |
| 12 | Observability | Event streaming to 3 surfaces |
| 13 | Testing | 3-tier: syntactic, targeted unit, integration |
| 14 | Versioning | Named snapshots with semantic guarantees |

Questions 1-7 define the core data model. Questions 8-14 define the operational model that makes it usable at scale. For the POC, you need solid answers to 1, 2, 4, 9, and 12. Everything else can be stubbed or deferred.

## Open questions

- How do cross-repo dependencies work in a database-native model?
- How do you preserve offline work and forking, the things that made Git great for open source?
- What's the right garbage collection policy for Tier 3 reasoning traces?
- How do you handle livelock at scale beyond priority ordering and impact zone advisories?
- What does testing look like when the test suite itself is being modified by agents concurrently?

## How to contribute

I need people who have felt this pain to help shape the solution.

- **Challenge the premises**: Steelman Git. If I'm wrong, I want to know now.
- **Build prototypes**: A working POC of any single layer would be incredibly valuable.
- **Share war stories**: How is agent-scale code breaking your workflow today?

## License

CC-BY-4.0: Use it, remix it, build on it.

---

*Started March 2026 by [Lokesh Basu](https://twitter.com/lcbasu). An open invitation to everyone building developer tools.*

**Website:** [lcbasu.github.io/git4aiagents](https://lcbasu.github.io/git4aiagents)
