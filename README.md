# git4aiagents

**Git was designed for humans. Agents need something fundamentally different.**

---

## Why this exists

I was building an open source agentic operating system. Eight repos. Multiple Claude Code instances running in parallel. And I was spending more time fighting Git than building the product. Merge conflicts. PR bottlenecks. Rebasing nightmares. The same story everywhere I looked.

Then it hit me. This isn't an agent problem. This is a Git problem. Agents just made it impossible to ignore.

## The real problem

Every developer has lived this. Green CI. No merge conflicts. You merge confidently. Production breaks. Because someone else changed what a function returns, and Git never noticed. **Git checks lines, not meaning.**

We built an entire industry of band-aids around this: CI pipelines, merge queues, status checks, "rebase on main" policies. All because Git is semantically blind. At human speed, the failure rate was low enough to tolerate. At agent speed, it collapses.

**Git has always been broken for code. Humans were just slow enough to work around it.**

If that sounds obvious, ask yourself: why is every tool in the market still built on top of Git?

## 14 questions I had to answer

I sat down and asked myself: if I were building version control from scratch for a world where 100s of agents write code simultaneously, what would I need to get right?

**1. What is the unit of work?** Not a commit (too coarse for one-line fixes, too fine for 40-file refactors). Two units: the *semantic mutation* (one meaningful change to one function/type/interface) and the *task* (a group of mutations that accomplish something together). Git bundles "fixed the bug AND ran the linter AND renamed that variable" into one commit. Here, those are separate mutations you can revert independently.

**2. How do agents coordinate?** Serializable Snapshot Isolation over a semantic dependency graph. Each agent gets a snapshot, works independently, and at commit time the system checks three things: did anyone touch the same element? Did any dependency change its interface? Do the tests still pass? No locks. No branches. No merge conflicts.

**3. What consistency model?** Strong within a dependency cluster, eventual across independent ones. Two agents working on unrelated modules don't need to see each other's changes in real-time. Two agents touching the same utility function do. The dependency graph handles the partitioning automatically.

**4. How do you capture reasoning?** Three tiers. Tier 1: structured metadata on every mutation (intent, confidence, dependencies read). Tier 2: human-readable rationale on non-trivial changes ("Used Decimal instead of float because currency arithmetic requires exact precision"). Tier 3: full LLM reasoning chain, garbage-collected after 7 days. The killer query this enables: "Why does this function exist?" No codebase can answer that today.

**5. How do humans fit in?** They don't review code. They set policy ("no changes to auth without approval", "all API endpoints validate with Zod") and review outcomes (completed tasks, not individual mutations). The UI is air traffic control, not a diff viewer.

**6. How do you migrate from Git?** A bridge. Import repos into the semantic graph via tree-sitter. Export back as conventional Git commits. Teams adopt incrementally. Eventually the new system becomes the source of record.

**7. How do you recover from failure?** Every mutation is individually recorded. Agent crashes mid-task? Another picks it up using the existing mutations and reasoning traces. Bad change shipped? Rollback is "undo this decision and everything downstream of it," not "reverse the diff and hope nothing depended on it."

**8. How do you trust agents?** Cryptographic identity per agent. Capability scoping per task (this agent can write to /payments but not /auth). Trust scoring over time: agents whose changes pass validation and rarely get rolled back earn more autonomy. Bad agents get constrained automatically.

**9. How do you parse code semantically?** tree-sitter for block-level parsing across 100+ languages. The system stores a block index (which functions/classes/types exist, their boundaries, their dependencies) and block content (versioned text). Agents also declare the scope of their mutations, verified at commit time.

**10. How do you track dependencies?** Four signals: explicit imports, symbol references via tree-sitter, agent-declared reads (stored in reasoning traces), and test coverage mapping. The graph is probabilistic. False positives cause harmless re-validation. False negatives get caught by tests.

**11. How do you decompose work?** Human states an objective ("add multi-currency support"). Planner agent breaks it into a task DAG with dependencies. Scheduler assigns tasks to agents as dependencies clear. Independent tasks run in parallel. This is where the 1000x efficiency comes from.

**12. How do you observe everything?** Three surfaces: system health metrics (for operators), task progress DAG (for stakeholders), decision audit trail (for debugging). All powered by event streaming. Every mutation emits an event. Dashboard, agents, and observability all subscribe.

**13. How do you test at this velocity?** Three tiers: syntactic validation on every mutation (<100ms, does it even parse?), targeted unit tests on every completed task (<30s, only tests covering affected code), full integration tests on a cadence. Prioritize compute on high-risk changes: low confidence, critical modules, low-trust agents.

**14. How do you version and release?** Named snapshots with semantic guarantees. A "release" is a point where all tests pass, no policy violations, no open flags. Environments are snapshot pointers. Promoting staging to production is moving a pointer. Every release carries full provenance: which tasks, which agents, what reasoning, what validated it.

## At a glance

| # | Question | Answer |
|---|---|---|
| 1 | Unit of work | Semantic mutation + task DAG |
| 2 | Coordination | SSI over dependency graph |
| 3 | Consistency | Strong within clusters, eventual across |
| 4 | Reasoning | 3-tier: metadata, rationale, full dump |
| 5 | Humans | Policy + task review + anomaly flags |
| 6 | Migration | Git bridge (import/export adapter) |
| 7 | Recovery | Dependency-aware targeted rollback |
| 8 | Trust | Crypto identity + capabilities + scoring |
| 9 | Parsing | tree-sitter + agent declarations |
| 10 | Dependencies | Multi-signal graph (imports, refs, tests) |
| 11 | Planning | Planner agent + task DAG + scheduler |
| 12 | Observability | Event streaming to 3 surfaces |
| 13 | Testing | 3-tier: syntax, targeted, integration |
| 14 | Releases | Named snapshots with guarantees |


## What's still open

- Cross-repo dependencies in a database-native model?
- Offline work and forking (the things that made Git great for open source)?
- Livelock at scale beyond priority ordering?
- Testing when the test suite itself is being modified concurrently?

## Help build this

Steelman Git. Build a POC of any single layer. Share how agent-scale code is breaking your workflow today.

CC-BY-4.0

---

*Started March 2026 by [Lokesh Basu](https://twitter.com/lcbasu)*

**Website:** [lcbasu.github.io/git4aiagents](https://lcbasu.github.io/git4aiagents)
