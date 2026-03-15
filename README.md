# git4aiagents

**Git was designed for humans. Agents need something fundamentally different.**

---

## Why this exists

Everyone nowadays run engineering teams where agents now write more code than my engineers. The old workflow of -> checkout, branch, push, PR, wait for review, rebase, pray was designed for human speed. It's breaking right now.

PR review is already the bottleneck for any high-output team. A filesystem checkout is a terrible primitive when you have hundreds of agents writing code in parallel. Code has become high-velocity data, and we're still storing and synchronizing it like it's 2005.

Someone has to start writing down what comes after Git. This is that attempt. Probably wrong about some things. But the conversation needs to start.

## The core idea

**Store code in a database, not a filesystem.**

Treat the codebase as a live, transactional, event-sourced data system. Not a tree of files synchronized through patches and diffs.

Every write is a transaction. Agents acquire fine-grained locks at the function or block level and not the whole file. Changes are immediately visible to every other agent. No more rebasing a 78-commit branch against a target that moved while you were working.

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
| **Merge Conflict** | Artifact of disconnected state. Period. | Write-time resolution via fine-grained locks + CRDTs |
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

## Open questions (this is where we need help)

These are the things I genuinely don't have answers to yet:

- Can CRDTs/OT actually work at full codebase scale, or do we need a completely different concurrency model?
- What's the right granularity for locking: AST nodes? Semantic symbols? Lines? It matters a lot and the answer probably varies by language.
- How do cross-repo dependencies work in a database-native model?
- What does "revert" even mean when every mutation carries a reasoning chain?
- Is there a migration path from Git, or does this need a clean break? (I suspect clean break, but that's a massive adoption barrier.)
- How do you preserve offline work and forking, the things that made Git great for open source?
- What happens to blame, bisect, and the other Git primitives people actually depend on?

## How to contribute

- **Challenge the premises**: Open an issue arguing why Git is actually fine. Seriously. Steelman it.
- **Extend the architecture**: Submit proposals for specific layers. The Reasoning Layer especially needs deeper thinking.
- **Build prototypes**: A POC of any single layer would be incredibly valuable.
- **Share war stories**: How is agent-scale code actually breaking your workflow today? Real examples > theory.

## License

CC-BY-4.0: Use it, remix it, build on it.

---

*Started March 2026 by [Lokesh Basu](https://twitter.com/lcbasu). An open invitation to everyone building developer tools.*

**Website:** [lcbasu.github.io/git4aiagents](https://lcbasu.github.io/git4aiagents)
