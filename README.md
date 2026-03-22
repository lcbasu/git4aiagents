# g4a - the reasoning layer for AI-written code

Git stores what changed. The reasoning behind it is lost. g4a captures it.

Add g4a to your existing project. Change nothing about your workflow. Unlock 10x for both humans and AI agents.

---

## The problem

Every AI coding agent thinks before it writes. Claude Code reads 20 files, considers 3 approaches, rejects 2, tests edge cases, and picks the best path. Cursor explores your codebase, reasons about patterns, and adapts. Codex plans multi-file changes before executing them.

Then at commit time, all of that thinking is thrown away.

This happens with every agent, every commit, every tool. The reasoning is generated, used once, and discarded. The most valuable artifact of the development process - the WHY behind every decision - is lost forever.

## What this causes

**PR review time increased 91%.** Teams using AI merge 98% more PRs that are 154% larger, but review time increased 91%. Reviewers stare at diffs and reverse-engineer intent. "Why did the agent change the auth middleware order?" has no answer. They have to guess, or ask, or just approve and hope.
> Source: [Faros AI, "AI Productivity Paradox Report" 2025](https://www.faros.ai/blog/ai-software-engineering) - 10,000+ developers across 1,255 teams.

**46% of developers actively distrust AI-written code.** Not because the code is bad - because they can't see the reasoning. Only 3% highly trust it. Trust requires transparency. When the thinking is invisible, distrust is rational.
> Source: [Stack Overflow 2025 Developer Survey](https://survey.stackoverflow.co/2025/ai) - 49,000+ respondents across 177 countries.

**66% waste time on "almost right" AI code.** The biggest frustration: AI solutions that are close but not quite. Debugging AI-generated code is more time-consuming than writing it yourself - because the reasoning behind it is invisible.
> Source: [Stack Overflow 2025 Developer Survey](https://survey.stackoverflow.co/2025/ai) - 49,000+ respondents.

**Teams don't benefit.** 70% of developers say AI agents boost their personal productivity. Only 17% say agents help team collaboration. That's a 4x gap. Reasoning lives in private sessions and dies there. Your teammate's agent made a smart architectural choice yesterday. Nobody on the team knows why. The next agent that touches that code will undo it.
> Source: [Stack Overflow 2025 Developer Survey, AI Agents section](https://survey.stackoverflow.co/2025/ai).

**AI agents start from zero, every time.** "Each Claude Code session begins with a fresh context window." Every agent re-discovers the same things. Every rejected alternative is re-explored. Every deliberate tradeoff is invisible. The codebase has no memory of the decisions that shaped it.
> Source: [Anthropic, Claude Code Memory docs](https://docs.anthropic.com/en/docs/claude-code/memory).

**Salesforce already hit this wall.** They rebuilt their entire review infrastructure because "traditional pull request review assumes reviewers can reconstruct intent by scanning diffs sequentially" - and that assumption broke under AI code volume. Code grew 30%, PRs expanded beyond 20 files and 1,000+ lines, and review latency rose quarter over quarter.
> Source: [Salesforce Engineering Blog, January 2026](https://engineering.salesforce.com/scaling-code-reviews-adapting-to-a-surge-in-ai-generated-code/).

## A real example of what this looks like

Your agent refactors the payment processing module. The commit says:

> refactor: Update payment calculation to use Decimal

Here's what the reviewer sees in the PR: 8 files changed across checkout, billing, refunds, and settlement. Float replaced with Decimal everywhere. Some rounding logic changed. Tests updated.

The reviewer has no idea why. Was this a performance issue? A precision bug? A compliance requirement? Did the agent test edge cases? What about the batch settlement job that runs nightly - did the agent even know about it?

The reviewer spends 25 minutes reading 8 diffs, checking if the rounding changes are correct, wondering if the settlement job handles Decimal, and ultimately approves because the tests pass and they're behind on 14 other PRs.

Three weeks later, the nightly settlement job is off by $0.03 on a batch of 10,000 transactions. That's $300 missing. The finance team escalates. Someone looks at git blame, finds the refactor commit, reads "Update payment calculation to use Decimal," and now has to figure out: what was the agent thinking? Why did it change the rounding mode in settlement? Did it know about the nightly job? Nobody knows. The agent session is gone.

**With g4a, the reasoning record for that commit would show:**

- **Intent:** Switch from float to Decimal for currency precision because batch settlements accumulate floating-point errors. After 500 operations on a test dataset, float arithmetic drifted by $0.03 vs Decimal which was exact.
- **Exploration:** Read checkout.py, billing.py, refunds.py, settlement.py. Found 14 call sites. Ran test with 10,000 simulated transactions - float accumulated $0.03 error, Decimal was exact. Checked batch_settlement_job.py - it calls calculate_total() which now returns Decimal.
- **Alternatives considered:** (1) Keep float + round at the end - rejected because error accumulates across batch operations. (2) Use integer cents - rejected because existing APIs expect decimal format and migration would touch 23 files. (3) Decimal everywhere - chosen, cleanest migration path, 8 files.
- **Risk assessment:** batch_settlement_job.py calls calculate_total() which now returns Decimal instead of float. The job's comparison operators and database writes were tested and work correctly with Decimal. However, the job's CSV export on line 47 uses f-string formatting that may truncate Decimal - flagged as LOW confidence.
- **Confidence:** 0.85 overall, 0.6 on CSV export formatting in settlement job

The reviewer reads this in 3 minutes. They see the agent tested with 10,000 transactions. They see it found 14 call sites. They see the LOW confidence flag on CSV export - they check that one line, find the formatting issue, fix it, and approve. Instead of 25 minutes of guessing, it's 3 minutes of reading and one targeted check.

Three weeks later, the settlement job works perfectly. The $0.03 error that would have appeared never happens. The reasoning is in the repo for the next person - or the next agent - who touches that code.

---

## How g4a helps humans

**PR reviews go from guessing to reading.** Instead of reverse-engineering intent from diffs, reviewers read the agent's actual reasoning. Intent, confidence, alternatives, what was tested, what was flagged as risky. A 25-minute review becomes a 3-minute review with higher confidence. Multiply that across every PR in your team.

**Debugging has a trail.** When something breaks, `g4a why settlement_job` gives you the complete decision history. Every change, every agent that touched it, what they intended, what they were worried about. No more reading diffs and guessing. No more "who changed this and why?"

**Trust is earned, not assumed.** Every AI-generated change carries a confidence score and a full explanation. Low-confidence changes get extra scrutiny. High-confidence changes with thorough exploration can be approved faster. Trust becomes data-driven, not gut-feel.

**Onboarding is instant.** A new developer joins the team. Instead of spending weeks figuring out why the codebase looks the way it does, they run `g4a why <module>` and read the decision history. "Why is this Decimal and not float?" - there's an answer. "Why does the middleware run in this order?" - there's an answer. The codebase documents itself.

## How g4a helps AI agents

**Every future agent will have context.** The `.g4a/` directory is institutional memory. A new agent reads it and immediately knows: this was changed from float to Decimal because of precision drift in batch settlements. The nightly job's CSV export was flagged as a risk. Integer cents was considered and rejected because it would touch 23 files. No re-exploration. No accidentally reverting a deliberate choice.

**Agents stop duplicating work.** Without g4a, every agent re-discovers the same things. Agent A explored the codebase and learned that the payment module has a tricky dependency on the settlement job. Agent B comes along the next day and has to discover that from scratch. With g4a, Agent B reads Agent A's exploration trail and starts from where Agent A left off.

**Agent coordination becomes possible.** When multiple agents work on the same codebase, they need shared context to avoid conflicts. g4a provides the foundation: captured reasoning that any agent can read before modifying shared code. As agent-to-agent coordination matures, the reasoning is already there.

**Your codebase compounds intelligence.** Every decision, every rejected alternative, every risk assessment - accumulated over months and years. The longer g4a runs, the more context every future agent has. Your codebase goes from being a collection of files to being a documented history of decisions.

---

## The solution

g4a captures the reasoning that AI agents already produce and stores it alongside the code in git. Add it to your existing project. Change nothing about your workflow. The reasoning layer starts working immediately.

**How it works:**

1. You install g4a once: `pip install g4a` or `brew install lcbasu/g4a/g4a`, then `g4a init`
2. You use your AI coding agent normally - nothing changes
3. g4a silently captures the agent's reasoning as it works
4. The reasoning is stored in `.g4a/` inside your repo
5. When you push to GitHub/GitLab/Bitbucket, the reasoning travels with the code

**What you can then do:**

- `g4a log` - see recent commits with the reasoning behind each one
- `g4a why process_payment` - get the full decision trail for any function
- `g4a show HEAD` - see a commit's diff side-by-side with the reasoning that produced it
- `g4a web` - open a visual report in your browser

---

## Designed for all AI coding agents

g4a is not tied to any single agent. It's a universal reasoning layer that works with every tool that writes code.

**Claude Code (launching first):** Deepest integration. Claude Code already saves full session transcripts with every file read, every tool call, every reasoning statement. g4a parses these transcripts directly - no API call needed, no workflow change. The reasoning is already there. g4a just captures it before it's lost.

**Every other agent (Cursor, Codex, Copilot, Windsurf, Aider, custom agents):** g4a installs a standard git post-commit hook. When any agent commits code, g4a reads the diff and surrounding context, then uses AI to infer the most likely reasoning. The result is labeled "inferred" (vs "captured" for agents with direct integration). Inferred reasoning is weaker than captured, but dramatically better than nothing.

**Custom agents and frameworks:** The reasoning record schema is the same regardless of which agent produced it. LangChain, CrewAI, custom pipelines - any tool that writes code through git can have its reasoning captured. The storage format is open, documented, and designed for future agents that don't exist yet.

---

## How it stores data

g4a stores reasoning in a `.g4a/` directory inside your repo. No external server. No account. No hosting.

- **One file per commit** (`.g4a/commits/{sha}.g4a`) - the reasoning behind that specific change
- **One file per session** (`.g4a/sessions/{id}.g4a`) - the full interaction chain including exploration, dead ends, and corrections
- **Binary format** - CBOR ([IETF RFC 8949](https://www.rfc-editor.org/rfc/rfc8949)) + zstd compression. An internet standard, not a proprietary format. Git sees "binary files differ" in diffs. The reasoning is only readable through g4a tools.
- **Secret masking** - credentials, API keys, passwords, and tokens are automatically detected and masked before any data hits disk. Irreversible by design.
- **Compact** - roughly 10-50 KB per commit compressed. 1,000 commits = 10-50 MB. Well within every hosting platform's limits.
- **Self-describing schema** - `.g4a/schema.json` in the repo tells any future tool how to parse the records

Clone the repo, you get the reasoning. Fork it, reasoning forks too. Delete `.g4a/`, you still have a perfectly valid git repo. g4a is additive. Always.

---

## Get started

```
# install (pick one)
pip install g4a
# or
brew install lcbasu/g4a/g4a

# then
cd your-project
g4a init
# use your AI coding agent normally
# reasoning is captured automatically
g4a log          # see commits with reasoning
g4a why <term>   # decision trail for any function
g4a web          # visual side-by-side report
```

---

## Sources

Every claim on this page is backed by primary research:

| Claim | Source | Link |
|-------|--------|------|
| PR review time up 91%, 98% more PRs, 154% larger PRs, 9% more bugs/dev | Faros AI, "AI Productivity Paradox Report" 2025 (10,000+ devs, 1,255 teams) | [faros.ai](https://www.faros.ai/blog/ai-software-engineering) |
| 46% distrust AI code, only 3% highly trust | Stack Overflow 2025 Developer Survey (49,000+ respondents, 177 countries) | [survey.stackoverflow.co](https://survey.stackoverflow.co/2025/ai) |
| 66% frustrated by "almost right" AI code | Stack Overflow 2025 Developer Survey | [survey.stackoverflow.co](https://survey.stackoverflow.co/2025/ai) |
| 70% individual gain, 17% team collaboration gain | Stack Overflow 2025 Developer Survey, AI Agents section | [survey.stackoverflow.co](https://survey.stackoverflow.co/2025/ai) |
| "Each session begins with a fresh context window" | Anthropic, Claude Code Memory documentation | [docs.anthropic.com](https://docs.anthropic.com/en/docs/claude-code/memory) |
| Salesforce rebuilt review infra, "reconstruct intent by scanning diffs" quote, 30% code growth, PRs beyond 20 files / 1,000+ lines | Salesforce Engineering Blog, January 2026 | [engineering.salesforce.com](https://engineering.salesforce.com/scaling-code-reviews-adapting-to-a-surge-in-ai-generated-code/) |
| CBOR binary format (IETF standard) | IETF RFC 8949 | [rfc-editor.org](https://www.rfc-editor.org/rfc/rfc8949) |

---

## About

g4a is open source (CC-BY-4.0). Started March 2026 by [Lokesh Basu](https://twitter.com/lcbasu).

Git stores what changed. g4a stores why. 10x from reasoning alone. The path to 1000x starts here.
