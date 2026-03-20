# git4aiagents

**Git was designed for humans. Agents need something fundamentally different.**

---

## Why this exists

I hit this problem firsthand while building an open source agentic operating system. Eight repos, multiple Claude Code instances running in parallel, all building the product at the same time. I spent more time resolving git conflicts and managing PR merges than I did on actual architecture decisions. The same pattern shows up everywhere I look. Teams across every place I've worked at recently are dealing with the exact same thing: agents writing code faster than humans can review it.

Right now, most teams use tools like Kiro and Claude Code where a human prompts the agent and reviews what it produces. That's already creating problems. But here's what keeps me up at night: these agents are becoming autonomous. They will plan, write, test, and ship code without waiting for a human to type a prompt each time. That transition is not theoretical. It's happening right now.

The old workflow (checkout, branch, push, PR, wait for review, rebase, pray) was built for human speed. At machine speed, it doesn't slow down gracefully. It shatters. The review bottleneck alone creates so much churn that I had to step back and ask a much more basic question: what if the problem isn't the workflow? What if the problem is Git itself?

Someone has to start writing down what comes after Git. This is that attempt. Probably wrong about some things. But the conversation needs to start.

## The real problem (it's not just agents)

Here's what made me realize this goes way deeper than "Git doesn't work for agents."

Agents didn't create this problem. They just made it impossible to ignore.

Every developer has lived this. You open a PR. CI is green. No merge conflicts. You merge confidently. And you break production. Because someone else's PR, also green, also conflict-free, changed a function signature or shifted what a return value means in something you depend on. Git told you everything was fine because **Git checks lines, not meaning.**

Take a step back and look at the whole ecosystem of band-aids we've built around this one flaw. CI pipelines. Required status checks. "Rebase on main before merge" policies. Integration branches. Merge queues. Every single one of these exists because Git's merge model is semantically blind and everyone knows it. We've just normalized the pain. When a senior dev "knows" which files to check after a merge, that's a human doing semantic dependency resolution in their head because the tool refuses to do it.

The difference with agents is purely scale. A human team merges maybe 5-10 PRs a day, and the odds of a semantic collision are low enough that CI catches most of it. But when 20 agents are committing every few minutes, the combinatorial explosion of potential semantic conflicts makes the band-aid approach fall apart. You can't run full CI on every commit at that velocity. And you definitely can't have a human reviewer eyeballing each merge for subtle dependency breaks. I've tried. The churn burns teams out.

So the framing shifts. This isn't "Git is broken for agents." It's something more fundamental: **Git has always been broken for code. Humans were just slow enough to work around it.** Agents expose the flaw that was always there. Version control that treats code as text was always the wrong abstraction. We tolerated it because at human velocity, the failure rate was manageable.

## The collapse at scale

Think about what Git actually assumes. One person. One machine. One local copy of the codebase. You work on it. You push it. Someone reviews it. That was elegant when the bottleneck was how fast a human could think and type.

Now picture what I see every day. Dozens of agents modifying the same codebase at the same time. You don't get "merge conflicts." You get chaos. **Branch-per-agent gives you thousands of divergent realities.** Filesystem locks are too blunt. And PR review? That's a single-threaded bottleneck choking a massively parallel system. The fastest engineer becomes the slowest reviewer. I've watched brilliant engineers spend their entire day reviewing agent-generated PRs instead of thinking about architecture and product. That's not engineering. That's bureaucracy.

Here's the thing most people haven't internalized yet: **code is no longer something humans carefully craft and contemplate. Code is high-velocity data.** It should be stored, synchronized, and validated like data. Write atomicity, real-time subscriptions, conflict resolution, and continuous validation baked into the storage layer. Not bolted on through CI pipelines that run 20 minutes after the fact.

If that sounds obvious, ask yourself: why is every tool in the market still built on top of Git?

## The core idea

**Store code in a database, not a filesystem.**

This is the core idea. Treat the codebase as a live, transactional, event-sourced data system. Not a tree of files synchronized through patches and diffs.

Every write is a transaction. An agent acquires a fine-grained lock at the function level or block level, not the whole file, makes its change, and that change is immediately visible to every other agent. No more "push and pray." No more rebasing a 78-commit branch against a target that moved while you were working.

Every mutation is replayable. This is the part that excites me most. Humans couldn't externalize their thought process while coding. They just wrote code and left a commit message. Agents can capture everything: the reasoning, the alternatives they considered, the context they consumed, the confidence level. Your version history stops being a flat diff log and becomes a complete decision graph. That was literally never possible before.

Review happens continuously, not at a gate. Real-time validation (lint, format, type-check, test, security scan) runs on every single transaction. Not in some CI pipeline that fires 20 minutes later. Review shifts from "human sitting in the critical path" to "continuous automated validation with human oversight on the things that actually matter." Engineers stop being bottlenecks and start being governors.

## The 9 Principles

1. **Code is data, not files**: The codebase is a structured, queryable, transactional data store. Files are a projection, not the truth.
2. **Write-level atomicity**: Every mutation is an atomic transaction with ACID guarantees. No partial states. No broken intermediaries.
3. **Fine-grained coordination**: Lock at the function or symbol level, not the file. Agents negotiate access through the storage layer, not through merge resolution after the fact.
4. **Real-time subscriptions**: When agent A modifies a function signature, agent B, working on a caller,  knows instantly and can adapt.
5. **Continuous validation**: Lint, format, typecheck, test on every transaction. Not in a CI pipeline 20 minutes later.
6. **Captured reasoning**: Every mutation carries the agent's intent, alternatives considered, context consumed, confidence level. The version history becomes a decision graph, not a diff log. This was never possible with humans.
7. **Policy, not process**: Humans define rules. The system enforces them. Continuously. The human role shifts from reviewer to governor.
8. **Full replayability**: Any codebase state, including the reasoning that produced it, can be reconstructed at any point. Time-travel debugging across the entire project history, including thought processes.
9. **Human-legible views**: The underlying store is agent-optimized. Humans interact through projections: IDE plugins, dashboards, NL queries against the codebase.

## What dies

| Concept | Why it dies | What replaces it |
|---|---|---|
| **Branch** | Exists because workers are disconnected. When agents share a transactional store, branching becomes optional isolation, not the default. | Shared store + optional isolation for experimentation |
| **Pull Request** | Batch review of accumulated work. Unnecessary when every mutation is validated in real-time with full reasoning captured. | Continuous validation + policy enforcement |
| **Merge Conflict** | Artifact of disconnected state. Period. | Write-time resolution via SSI + semantic dependency graph |
| **Local Checkout** | There is no "local." Agents read/write directly. Humans get views. | Direct store access + projection layer for humans |

## Strawman architecture

```
┌─────────────────────────────────────────────┐
│  Projection Layer  (IDE, dashboards, NL UI) │
├─────────────────────────────────────────────┤
│  Policy Layer      (human-defined rules)    │
├─────────────────────────────────────────────┤
│  Reasoning Layer   (intent, context, trace) │
├─────────────────────────────────────────────┤
│  Validation Layer  (lint→fmt→type→test→sec) │
├─────────────────────────────────────────────┤
│  Coordination Layer (locks, OT/CRDTs, sub)  │
├─────────────────────────────────────────────┤
│  Storage Layer     (Postgres / purpose-built)│
└─────────────────────────────────────────────┘
```

## Git vs. what comes next

| Dimension | Git (2005-2025) | git4aiagents (2026+) |
|---|---|---|
| Storage | Filesystem + DAG of snapshots | Transactional database |
| Unit of work | Commit (batch of diffs) | Transaction (atomic mutation) |
| Coordination | Branch + merge | SSI + semantic dependency graph |
| Conflict resolution | After the fact (merge/rebase) | At write-time (real-time) |
| Validation | CI pipeline (minutes later) | Continuous (per-transaction) |
| Review | Human gate (PR) | Continuous automated + policy |
| History | Diff log + commit messages | Decision graph + reasoning traces |
| Human role | Writer + reviewer | Governor + architect |
| Optimized for | 1-20 humans | 10-1000 agents + humans |

## Foundational design decisions

These are the questions I went through before writing a single line of code. Every one of them affects the data model, and the data model is the one thing you can't change later without rebuilding everything. Get these wrong and you're starting from scratch.

### 1. What is the unit of work?

Git's "commit" is a snapshot of the entire repo at a point in time. That made sense when a human would spend 30 minutes making a coherent set of changes, write a message, and push. For agents, this is absurd. An agent might change one line in one function as part of a larger task, or refactor 40 files in 3 seconds. The commit granularity is either too coarse or too fine.

**The answer is two units, not one.**

The **atomic unit** is the *semantic mutation*. A single meaningful change to a single semantic element. "Changed the return type of `calculateTotal` from `int` to `Decimal`." "Added parameter `currency` to `formatPrice`." "Created new function `validateCurrency`." Each mutation is a node in a graph. It has a type (create, modify, delete, rename, move), a target (the semantic element, whether that's a function, type, interface, constant, or import), and a diff that's structural, not textual. You're not storing "line 47 changed from X to Y." You're storing "function `calculateTotal` signature changed from `(items: Item[]) -> int` to `(items: Item[], currency: Currency) -> Decimal`."

The **logical unit** is the *task*. A directed acyclic graph of semantic mutations that together accomplish something. "Implement multi-currency support" is a task composed of 15 mutations across 8 files. The task is what agents get assigned, what humans review, and what gets rolled back if something breaks. It's roughly analogous to a PR, but it's a first-class entity in the data model, not a diff between two branch tips.

Why two levels matters: the system can accept or reject individual mutations based on semantic conflicts, while humans reason about tasks. An agent's task might have 14 clean mutations and 1 conflicting one. Instead of rejecting the whole "PR," the system pinpoints exactly which mutation conflicts and why. The agent can fix just that one.

**The thing Git gets catastrophically wrong:** a commit bundles unrelated changes into one blob. Developers commit "fixed the bug AND ran the linter AND renamed that variable" as one unit. When you need to revert just the bug fix, good luck. In this system, those are three separate mutations inside one task, independently addressable.

**Data model implication:** your storage isn't files and lines. It's a graph of semantic elements with mutation history on each node. A "function" node has a full history of every mutation, who (which agent) made it, why (linked to task + reasoning trace), and what it depends on. This is why database-native storage is the right call. You literally need graph queries and transactional semantics on this structure.

### 2. What is the coordination model?

**Serializable Snapshot Isolation (SSI) over a semantic dependency graph.**

Here's how it actually works step by step:

**Step 1, Task assignment.** An agent gets a task ("add multi-currency support"). The system computes the *likely impact zone*, the set of semantic elements the agent will probably touch, based on the task description and the dependency graph. This is an estimate, not a lock.

**Step 2, Snapshot.** The agent gets a consistent snapshot of the impact zone plus its transitive dependencies. Not the whole repo, just the subgraph it needs. This is cheap because you're querying a database, not cloning a repo.

**Step 3, Work.** The agent works against its snapshot. It generates semantic mutations. No coordination overhead during this phase. The agent is fully independent.

**Step 4, Commit validation.** When the agent submits its mutations, the system runs three-layer validation:

- **Layer 1, Structural conflict.** Did another agent modify the same semantic element since our snapshot? If agent A and agent B both rewrote `calculateTotal`, that's a hard conflict. One wins (first-writer-wins or priority-based), the other gets rejected with full context of what changed.
- **Layer 2, Interface contract.** Did any semantic element I *depend on* change its interface since my snapshot? If I'm calling `formatPrice(amount)` but another agent changed it to `formatPrice(amount, locale)`, my code is broken even though I never touched `formatPrice`. The dependency graph catches this instantly. No need to compile or run tests to detect it.
- **Layer 3, Behavioral validation.** Does the combined state pass relevant tests? This is the expensive one. You can't run it on every mutation, so it runs on task completion or on a cadence. The key insight: because you have the dependency graph, you can run *targeted* tests. Only the tests that cover the changed subgraph and its dependents. Not the full suite.

**Why this beats every alternative:**

- Locks would serialize agents. If Agent A locks the "payments" module, agents B through F sit idle even though they're touching unrelated functions within that module.
- CRDTs can't work because code merges aren't commutative. `addParameter(x)` and `removeFunction(x)` don't compose. Order matters, and semantic validity matters.
- OT could work at the character level but you'd spend more compute resolving transforms than writing code, and you'd still miss semantic conflicts.

**The throughput math:** if your system has 1000 semantic elements and 20 agents, and each agent's task touches about 10 elements on average, the probability of structural conflict on any given commit is low (roughly `(10/1000) * 19`, about 19% chance of touching at least one overlapping element). Interface conflicts are slightly more likely because of transitive dependencies, but the dependency graph makes detection O(edges) not O(codebase). You get near-linear scaling with agent count until the codebase's dependency graph becomes so interconnected that most agents' impact zones overlap. At that point you have an architecture problem, not a tooling problem.

**Open question worth flagging:** what happens when Layer 1 rejects a mutation? The agent needs to redo work. If you're naive about this, you get livelock, two agents repeatedly conflicting and redoing. The fix is a combination of priority ordering (agent working on higher-priority task wins) and *impact zone advisories*, soft signals that say "heads up, Agent B is also working in this area." Not locks, but hints that let an agent's planner decide whether to wait or proceed optimistically.

### 3. What is the consistency model?

The knee-jerk answer is "strong consistency, obviously, code has to be correct." But that's wrong at the scale we're targeting.

**The right model: strong consistency within a dependency subgraph, eventual consistency across independent subgraphs.**

Think of it this way. If Agent A is working on payments and Agent B is working on the onboarding flow, and those two subgraphs share no semantic dependencies, they don't need to see each other's changes in real-time. Eventual consistency is fine. They're working in effectively different codebases that happen to live in the same repo.

But if Agent A changes a shared utility function that Agent B depends on, Agent B needs to know *before it commits*, not after. Within connected components of the dependency graph, you need strong consistency. Specifically, you need the snapshot isolation guarantee that when B commits, the system checks whether B's dependencies have changed since B's snapshot.

**This maps to a real database concept: partitioned consistency.** Your semantic dependency graph naturally partitions into clusters of tightly-coupled code (a module, a service, a feature area) connected by thinner edges (shared interfaces, common utilities). Within a cluster, strong consistency. Across clusters, eventual consistency with conflict detection at commit time.

**The practical architecture:** each cluster has a serialization point (think of it as a lightweight transaction coordinator). Mutations within a cluster are serialized. Mutations across clusters are validated at commit time using the interface contract check from Layer 2. This gives you the throughput of eventual consistency (agents in different clusters never block each other) with the correctness of strong consistency (agents in the same cluster see each other's changes).

**The tricky edge case: shared utilities.** Every codebase has a `utils.ts` or a `common` module that everything depends on. In the dependency graph, this is a high-centrality node, lots of things point to it. If you make this a single serialization point, it becomes a bottleneck. The fix: decompose shared utilities at a finer granularity in the semantic graph. `utils.formatDate` and `utils.formatCurrency` are independent semantic elements, not one "utils" module. Two agents can modify different utility functions concurrently because the graph tracks dependencies at the function level, not the file level.

**The failure mode to design for:** an agent works for 5 minutes on a task, generates 20 mutations, and at commit time discovers that a dependency changed 4 minutes ago. 20 mutations of wasted work. The mitigation is *streaming validation*. As the agent works, the system continuously checks whether the agent's snapshot dependencies are still valid. If something changes, the agent gets an interrupt: "heads up, `formatPrice` signature just changed, here's the new interface." The agent can adapt mid-task instead of discovering the conflict at the end. This is a major advantage over Git where you only discover conflicts when you try to merge.

### 4. What does the reasoning trace look like?

This is the moat. And honestly, this is the part that got me most excited when I started thinking about all of this. Every other part of this system is hard engineering. The reasoning trace is the thing that makes it genuinely new. Code that knows *why* it exists.

**The problem with "just store the LLM's reasoning":** an agent producing 100 mutations per minute, each with a paragraph of reasoning, generates gigabytes of unstructured text per day. Nobody will read it. It's unsearchable in any useful way. And most of it is noise.

**The structure: three tiers.**

**Tier 1, Structured metadata (always stored, machine-readable).** Compact and queryable.
- `intent`: enum like `implement_feature`, `fix_bug`, `refactor`, `optimize`, `adapt_to_dependency_change`
- `task_id`: link to the parent task
- `confidence`: float, how certain the agent was this was the right change
- `alternatives_considered`: count of other approaches evaluated
- `dependencies_read`: which semantic elements the agent examined before making this change
- `constraints_applied`: which rules/policies/patterns influenced the decision

**Tier 2, Structured rationale (stored for non-trivial mutations, human-readable but compact).**
- `why_this_approach`: 1-2 sentences. "Used Decimal instead of float because currency arithmetic requires exact precision."
- `why_not_alternatives`: 1-2 sentences per rejected alternative. "Considered BigNumber library but it adds 50KB to bundle size for a feature that only needs basic arithmetic."
- `assumptions`: explicit list. "Assumes all currencies have exactly 2 decimal places. This is wrong for JPY and BHD, flagged as follow-up task."

**Tier 3, Full reasoning dump (stored ephemerally, garbage-collected after N days).**
- The raw LLM reasoning chain. Useful when something goes wrong and you need to understand the agent's full thought process. Not useful day-to-day.

**Why three tiers:** Tier 1 is what the system queries. "Show me all mutations where an agent had confidence below 0.7." "Show me all mutations that were `adapt_to_dependency_change`, those are the ones most likely to be wrong." "Show me everything that read from `calculateTotal` in the last hour." This is your dashboard, your audit trail, your debugging surface.

Tier 2 is what a human reads during review. Instead of reading a diff and guessing why the agent made a choice, the human reads a concise rationale. This is the thing that makes agent-written code actually reviewable.

Tier 3 is your escape hatch for when things go really wrong and you need forensics.

**Storage math:** Tier 1 is about 200 bytes per mutation. At 1000 mutations per minute across all agents, that's 200KB/min, roughly 12MB/hour, roughly 288MB/day. Trivial. Tier 2 is about 500 bytes per mutation but only stored for roughly 30% of mutations (trivial ones like import additions skip it). About 100MB/day. Still trivial. Tier 3 is about 5KB per mutation, stored for 7 days. Roughly 7GB/day, roughly 49GB rolling. Significant but manageable, and you can put it in cold storage.

**The killer query this enables:** "Why does this function exist?" No human codebase can answer that today. You grep through commit messages, PR descriptions, Slack threads, Jira tickets. In this system, you traverse the reasoning trace: this function was created by Task X, because of reasoning Y, it depends on A and B, and its assumptions are C. That's transformative for onboarding, debugging, and refactoring.

### 5. How do humans interact?

This is the one I feel most strongly about because I live it every single day. This is where most "AI-first" tools fail. They build for the machine and bolt on a human interface as an afterthought. But humans are the principal. Agents are the executors. The governance model has to be human-first.

**The mental model shift: humans don't review code. Humans set policy and review outcomes.**

I've seen what happens when you try to keep humans in the review loop at agent scale, both in my own work and across teams everywhere I've been recently. People burn out. The review queue becomes a graveyard. Good engineers start spending 100% of their time reading agent-generated diffs instead of thinking about architecture, product, or strategy. That's not a workflow problem. That's a fundamental mismatch between the volume of work being produced and the capacity of human attention.

At 1000+ mutations per minute, line-by-line code review is dead. A human can't review that volume and shouldn't try. Instead, humans operate at three levels:

**Level 1, Policy definition.** Before any agent writes code, humans define constraints. "All API endpoints must validate input with Zod schemas." "No new dependencies over 100KB without approval." "The payments module must maintain 95% test coverage." "No changes to the `auth` module without human approval." These aren't code review comments. They're machine-enforceable rules that the system checks on every mutation.

**Level 2, Task-level review.** Humans review completed tasks, not individual mutations. A task like "implement multi-currency support" gets presented as: here's what changed (semantic summary, not a diff), here's why (aggregated reasoning traces), here are the test results, here are the confidence scores, here are the assumptions. The human approves the task, requests changes (which generates a new sub-task for an agent), or rejects it. This is the equivalent of PR review but at the right abstraction level.

**Level 3, Anomaly-driven intervention.** The system surfaces things that look wrong: mutations with low confidence scores, tasks that required unusually many retries (suggesting the agent was struggling), patterns that violate architectural principles even if they pass tests, clusters of `adapt_to_dependency_change` mutations (suggesting a cascading change that might be going off the rails). Humans jump in when the system flags risk, not on a fixed review cadence.

**The UI is not a diff viewer.** It's a dashboard that shows active tasks, agent utilization, conflict rates, test health, policy violations, and an attention queue of things that need human eyes. Think of it as an air traffic control screen, not a GitHub PR page. Humans are directing traffic, not reading every line.

**The escalation model matters:** every policy should define what happens when it's violated. Some are hard blocks (auth module changes require human approval, agent literally cannot proceed). Some are soft warnings (dependency size exceeded, agent can proceed but it's flagged for review). Some are async (test coverage dipped below threshold, create a follow-up task to fix it, don't block the current task). Getting this granularity right is what makes the system usable instead of either too permissive or too rigid.

### 6. What is the migration and interop story?

You can't ignore this. Every potential user has an existing Git repo. If your system requires a clean-room start, adoption is dead.

**The bridge:** the system needs to ingest from and export to Git. Not as a core abstraction, but as an I/O adapter. Import a Git repo, parse it into a semantic graph (tree-sitter + LSP give you the AST, you build the dependency graph from there). Export from the system, generate a conventional Git commit with a readable diff and a commit message synthesized from the reasoning traces. This means teams can adopt incrementally: agents work in the new system, but the "source of record" for compliance/legal/existing-CI purposes is still a Git repo that gets updated via the bridge.

**The long-term play:** once teams see the benefit (semantic conflict detection, reasoning traces, task-level review) they stop looking at the Git export. It becomes an artifact for backward compatibility, like how companies still generate PDF invoices even though everything is digital. Eventually, the new system *is* the source of record.

### 7. What is the failure and recovery model?

In a system running at this velocity, things will go wrong constantly. Here's how to handle it.

**Agent-level failure:** an agent crashes mid-task. Because every mutation is individually recorded in the database, there's no "half-committed" state. The completed mutations are valid. The task is marked incomplete. Another agent can pick it up, read the existing mutations and reasoning traces, and continue from where it stopped. This is impossibly hard in Git. If a developer disappears mid-branch, someone else has to read the diff, reverse-engineer the intent, and finish the work.

**Semantic regression:** an agent's changes pass all three validation layers but still cause a production issue. The system can identify the causal chain: this task introduced these mutations, which affected these semantic elements, which are exercised by these code paths. Rollback is targeted. Revert the specific mutations, not "revert the whole commit and also the 5 commits that came after it because Git made them all sequential."

**Cascading failure:** Agent A makes a change, agents B, C, and D adapt to it, and then A's change is rolled back. Now B, C, and D's adaptations are orphaned. They're adapting to something that no longer exists. The dependency graph detects this immediately: A's rollback invalidates downstream mutations that have `adapt_to_dependency_change` intent linked to A's mutation. Those get flagged for re-evaluation automatically.

**The key insight:** all of this recovery logic is only possible because you stored the semantic graph and reasoning traces. In Git, rollback is "reverse the diff." In this system, rollback is "undo this decision and everything downstream of it." That's the difference between text-aware and semantics-aware version control.

---

These seven questions form the design spec. Get alignment on these before writing code, because every single one of them affects the data model, and the data model is the one thing you can't change later without rebuilding everything.

## Open questions (where we still need help)

- How do cross-repo dependencies work in a database-native model?
- How do you preserve offline work and forking, the things that made Git great for open source?
- What's the right garbage collection policy for Tier 3 reasoning traces?
- How do you handle the livelock problem at scale beyond priority ordering and impact zone advisories?
- What does testing look like when the test suite itself is being modified by agents concurrently?

## How to contribute

I need people who have felt this pain to help shape the solution.

- **Challenge the premises**: Open an issue arguing why Git is actually fine. Seriously. Steelman it. If I'm wrong, I want to know now.
- **Extend the architecture**: Submit proposals for specific layers. The Reasoning Layer especially needs deeper thinking.
- **Build prototypes**: A working POC of any single layer would be incredibly valuable. Code talks louder than manifestos.
- **Share war stories**: How is agent-scale code actually breaking your workflow today? Real examples from real teams are worth more than theory.

## License

CC-BY-4.0: Use it, remix it, build on it.

---

*Started March 2026 by [Lokesh Basu](https://twitter.com/lcbasu). An open invitation to everyone building developer tools.*

**Website:** [lcbasu.github.io/git4aiagents](https://lcbasu.github.io/git4aiagents)
