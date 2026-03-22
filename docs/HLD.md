# g4a - High-Level Design

**Version:** 1.0
**Date:** 2026-03-22
**Author:** Lokesh Basu
**Status:** Final draft, pre-POC

---

## Table of contents

1. [Design principles](#1-design-principles)
2. [Architecture overview](#2-architecture-overview)
3. [Component design](#3-component-design)
4. [Data flow](#4-data-flow)
5. [Data model](#5-data-model)
6. [Storage engine](#6-storage-engine)
7. [Capture engine](#7-capture-engine)
8. [Secret masking pipeline](#8-secret-masking-pipeline)
9. [Query engine](#9-query-engine)
10. [CLI design](#10-cli-design)
11. [Web reporter](#11-web-reporter)
12. [Latency budget](#12-latency-budget)
13. [Security model](#13-security-model)
14. [Error handling](#14-error-handling)
15. [Testing strategy](#15-testing-strategy)
16. [Packaging and distribution](#16-packaging-and-distribution)
17. [Future extensions](#17-future-extensions)

---

## 1. Design principles

Three non-negotiable constraints that override every other decision:

### 1.1 Zero-latency capture

g4a must **never** add perceptible latency to the developer's workflow. The developer commits, pushes, and moves on. Reasoning capture happens entirely in the background. If capture fails, the commit still succeeds. g4a is invisible when working and visible only when queried.

**Hard rule:** The git post-commit hook must return in under 50ms. All real work happens in a detached background process.

### 1.2 Full security by default

Reasoning records contain the agent's thinking, which may include file contents, API responses, database schemas, and other sensitive context. g4a assumes everything is sensitive until proven otherwise.

**Hard rule:** Every byte of reasoning data written by g4a (to git notes, local index, or anywhere else) passes through the secret masking pipeline first. There is no bypass. No flag, no env var, no config option skips masking.

### 1.3 Zero-config usability

Two commands to start: `pip install g4a` (or `brew install g4a`) then `g4a init`. No account, no server, no API key, no config file, no YAML. g4a auto-detects which agent produced the commit and selects the right capture adapter. The developer never thinks about g4a until they need it.

**Hard rule:** `g4a init` completes in under 2 seconds and requires zero user input.

---

## 2. Architecture overview

**POC scope: Claude Code only.** The architecture is agent-agnostic by design - the adapter layer, schema, and storage are all decoupled from any specific agent. But the POC ships with one adapter: Claude Code. Other agents (Cursor, Copilot, Codex, etc.) will be added once the Claude Code version is stable and the schema has been validated in production. The adapter interface is documented so the community can contribute adapters.

```
+------------------------------------------------------------------+
|                         Developer workflow                        |
|  [AI Agent (Claude Code to start with)] --> [writes code] --> [git commit] --> [git push]  |
+------------------------------------------------------------------+
        |                                |
        |  Session transcript            |  Post-commit hook
        |  ~/.claude/projects/           |  (fires on every commit)
        |  {project}/{session}.jsonl     |
        v                                v
+------------------+          +--------------------+
|  Claude Code     |          |  Git Hook Shim     |
|  Adapter         |          |                    |
|                  |          |  Forks background   |
|  Parses JSONL    |          |  process, returns   |
|  transcripts     |          |  in < 50ms         |
|  directly        |          |                    |
+--------+---------+          +--------------------+
         |
         v
+--------+--------+
|  Reasoning      |
|  Extractor      |
|                 |
|  Normalizes to  |
|  unified schema |
+--------+--------+
         |
         v
+--------+--------+
|  Secret         |
|  Masking        |
|  Pipeline       |
|                 |
|  80+ regex      |
|  + entropy      |
|  + context      |
|  + path sanitize|
+--------+--------+
         |
         v
+--------+--------+
|  Storage        |
|  Engine         |
|                 |
|  CBOR ->        |
|  git notes      |
+--------+--------+
         |
         |  Retry queue
         |  (.git/g4a/pending.json)
         |
         +-------------+----------------+
         |             |                |
         v             v                v
   +-----------+ +-----------+ +-----------+
   | g4a log   | | g4a why   | | g4a web   |
   | g4a show  | | g4a query | |           |
   +-----------+ +-----------+ +-----------+
        CLI           CLI         Browser
```

### Component summary

| Component | Responsibility | Latency budget |
|-----------|---------------|----------------|
| Git hook shim | Detect commit, fork background process, return immediately | < 50ms |
| Claude Code adapter | Parse JSONL transcripts from `~/.claude/projects/` | < 500ms (background) |
| Reasoning extractor | Normalize captured reasoning to unified schema | < 100ms (background) |
| Secret masking pipeline | Scan and redact all sensitive data (80+ patterns) | < 200ms (background) |
| Storage engine | Serialize to CBOR, write as git note | < 100ms (background) |
| Retry queue | Track failed captures, drain on next success | < 50ms (background) |
| Query engine | Read note blobs, deserialize CBOR, search, rank | < 300ms (interactive) |
| CLI | Parse commands, render output | < 50ms overhead |
| Web reporter | Generate static HTML, open browser | < 1s |

**Total background capture time:** < 1s for Claude Code.
**Total interactive query time:** < 500ms for any query.

### Future adapters (post-POC)

The adapter interface (`capture/adapters/base.py`) defines a simple contract:

```python
class BaseAdapter:
    def can_handle(self, commit_sha: str) -> bool: ...
    def capture(self, commit_sha: str) -> RawCapture: ...
```

Future adapters will implement this interface:
- **Git hook inference adapter** - reads diff + context, calls an LLM to infer reasoning. Labeled "inferred" vs "captured".
- **Cursor adapter** - if Cursor exposes session data in the future
- **Codex / Copilot adapter** - same pattern
- **Custom agent adapter** - for LangChain, CrewAI, custom pipelines

The schema is designed to evolve: new fields are additive, never breaking. Older records remain readable.

---

## 3. Component design

### 3.1 Project structure

```
g4a/
  __init__.py
  __main__.py              # Entry point: python -m g4a
  cli/
    __init__.py
    main.py                # Click/Typer CLI app
    commands/
      init.py              # g4a init
      log.py               # g4a log
      show.py              # g4a show
      why.py               # g4a why
      web.py               # g4a web
      status.py            # g4a status
  capture/
    __init__.py
    detector.py            # Auto-detect which agent produced the commit
    hook_shim.py           # The actual post-commit hook script
    background.py          # Detached background process manager
    retry.py               # Retry queue management (.git/g4a/pending.json)
    adapters/
      __init__.py
      base.py              # Abstract adapter interface
      claude_code.py       # Claude Code transcript parser (POC)
      metadata.py          # Fallback: metadata-only capture
  extract/
    __init__.py
    extractor.py           # Normalize raw capture to CommitRecord + SessionRecord
    schema.py              # CommitRecord, SessionRecord, Event dataclasses + validation
  security/
    __init__.py
    masker.py              # Secret masking pipeline
    patterns.py            # Regex patterns for known secret formats
    entropy.py             # Shannon entropy detector for unknown secrets
  storage/
    __init__.py
    engine.py              # Git notes read/write, .g4a/ schema/config
    codec.py               # CBOR serialization + git notes I/O
    index.py               # Search index (file-to-commits, function-to-commits)
  query/
    __init__.py
    engine.py              # Search across reasoning records
    ranker.py              # Relevance ranking for "g4a why" queries
  web/
    __init__.py
    reporter.py            # Generate HTML report
    templates/             # Jinja2 templates
      report.html
      commit.html
  hooks/
    post-commit            # Capture hook, installed by g4a init
    post-rewrite           # Rebase/amend SHA remapping hook
```

### 3.2 Dependency budget

Minimal dependencies keep install fast and attack surface small:

| Dependency | Purpose | Size |
|------------|---------|------|
| `cbor2` | CBOR serialization (RFC 8949) | 45 KB |
| ~~`zstandard`~~ | ~~zstd compression~~ | Removed - git handles compression natively |
| `click` | CLI framework | 300 KB |
| `rich` | Terminal formatting | 700 KB |
| `jinja2` | HTML template rendering (web report) | 500 KB |

**No LLM SDK required.** The POC uses only local file parsing (Claude Code JSONL transcripts). No network calls, no API keys, no LLM SDK. Future inference adapters may optionally use LLM APIs, but the core package will never require them.

**Total install size target:** < 5 MB.
**Install time target:** < 10 seconds on a cold pip cache.

---

## 4. Data flow

### 4.1 Capture flow (Claude Code - direct)

**Key insight:** Between two commits, a developer may exchange 100+ prompts with Claude Code. The agent reads dozens of files, considers multiple approaches, hits dead ends, backtracks, and explores alternatives - all before a single commit. A single session can produce multiple commits, or a single commit can come after hours of exploration. **g4a captures everything - the full session trace, not just a slice around the commit.**

```
1. Developer uses Claude Code normally
2. Claude Code writes session transcript to:
   ~/.claude/projects/{project-slug}/{session-id}.jsonl
   This transcript grows with every prompt, every thinking block,
   every tool call, every response - hundreds of entries.
3. Developer commits (or Claude Code commits for them)
4. Post-commit hook fires:
   a. Hook shim reads the commit SHA
   b. Fork a detached background process
   c. Hook returns immediately (< 50ms)
5. Background process:
   a. detector.py checks for Claude Code transcripts
      - Looks at ~/.claude/projects/ for the current project
      - Finds the active transcript (most recently modified .jsonl)
      - Falls back to metadata-only if no transcript found
   b. claude_code.py does TWO things:

      STEP 1 - Capture the full session as a git note:
      - Parse the ENTIRE .jsonl transcript from start to current point
      - Extract every event: user prompts, assistant thinking,
        tool calls, tool results, errors, corrections
      - This captures the full chain of reasoning including dead ends,
        backtracks, and multi-step explorations
      - Mask secrets across the entire session
      - Write as git note on the anchor SHA (refs/notes/g4a-sessions/<client_id>)
      - If the session note already exists (from a previous commit in
        the same session), append new events and rewrite the note
        (git delta-packs the diff efficiently)

      STEP 2 - Create a commit record as a git note:
      - Record which sessions contributed (with anchor_sha for lookup)
      - Record the event range within each session (msg_start to
        msg_end) so queries can find the relevant reasoning
      - Synthesize a summary: intent, alternatives, risks, confidence
      - Write as git note on this commit (refs/notes/g4a-commits/<client_id>)

   c. Updates .git/g4a/index.db (local search index)
```

**Why capture the full session, not just a window:**

A developer asks Claude Code to refactor payment processing. Over the next 45 minutes:

- Prompt 1-3: "Refactor payments to use Decimal" - agent reads 8 files
- Prompt 4-6: Agent tries approach A (integer cents), writes code, runs tests - tests fail
- Prompt 7-8: "That didn't work, the API expects decimal format" - agent backtracks
- Prompt 9-12: Agent tries approach B (Decimal everywhere), reads settlement job, finds CSV risk
- Prompt 13: Agent writes final code, runs tests - tests pass
- Prompt 14: Agent commits

If g4a only captured prompts 12-14 (the "window" around the commit), it would miss:
- The dead end with integer cents (prompt 4-6) - critical context for WHY Decimal was chosen
- The discovery of the settlement job dependency (prompt 9-12)
- The full exploration of 8 files (prompt 1-3)

The **session note** captures all of this. The **commit note** points into the session and provides a synthesized summary. Queries can drill into the full session when needed.

### 4.2 Capture flow (no Claude Code transcript found - metadata only)

If no Claude Code transcript is found (e.g. a manual commit, or an agent without an adapter yet), g4a still captures structural metadata:

```
- Commit SHA, timestamp, author
- Files changed, lines added/removed per file
- Commit message
- Whether the commit was likely AI-generated (heuristic: checks for
  "Co-Authored-By" trailer, common AI commit message patterns)
- source="metadata-only"
```

Even metadata-only records (stored as git notes) power `g4a log` and provide a timeline with no gaps. When additional adapters are added in the future, `g4a backfill` can re-process past commits.

### 4.3 Query flow

```
1. User runs: g4a why process_payment
2. CLI parses the query term
3. query/engine.py:
   a. Reads .git/g4a/index.db (local search index, rebuilt lazily)
   b. Finds all commits that mention "process_payment" in:
      - Files changed
      - Reasoning text (intent, exploration, alternatives)
      - Function names extracted from diffs
   c. Reads matching commit notes (scans all refs/notes/g4a-commits/* client namespaces)
   d. Deserializes CBOR payloads
   e. ranker.py scores results by:
      - Recency (newer = higher)
      - Relevance (exact function name match > file match > text match)
      - Source quality (captured > partial > metadata-only)
   f. Returns top N results (default 10)
4. CLI renders results with rich formatting
```

---

## 5. Data model

### 5.1 The timeline between commits

The most important insight for the data model: **between any two git commits, there is a rich timeline of events.** This timeline could be:

- **Hundreds of steps** when an agent explored, hit dead ends, backtracked, and iterated
- **Parallel branches** when 2+ agents worked on the same repo concurrently
- **Just two commit markers** when a human wrote code manually with no agent

g4a captures this timeline as a **directed acyclic graph (DAG)** of events, not a flat list. Each event has a timestamp, an actor (which agent or "human"), and edges to related events.

```
Commit A                                                    Commit B
  |                                                            |
  +-- [agent-1: claude-code session 542e]                      |
  |     |                                                      |
  |     +-- step 0: user prompt "refactor payments"            |
  |     +-- step 1: thinking (reads files)                     |
  |     +-- step 2: tool_call Read checkout.py                 |
  |     +-- step 3: tool_call Read billing.py                  |
  |     +-- ...                                                |
  |     +-- step 46: thinking "try integer cents"              |
  |     +-- step 47-65: dead end (integer cents)               |
  |     +-- step 66: test fails                                |
  |     +-- step 68: thinking "pivot to Decimal"               |
  |     +-- ...                                                |
  |     +-- step 120: git commit --> Commit B                  |
  |                                                            |
  +-- [agent-2: claude-code session b2b3]                      |
  |     |                                                      |
  |     +-- step 0: user prompt "update docs"                  |
  |     +-- step 1-8: reads and edits README                   |
  |     +-- step 9: git commit --> Commit C (different branch) |
  |                                                            |
  +-- [human: no agent session]                                |
        |                                                      |
        +-- (no steps captured, just the commit markers)       |
```

**Key design decisions:**

1. **Every commit gets a record,** even human-only commits with zero agent steps. The minimum record is two markers: "commit A ended here" and "commit B starts here." This ensures the timeline has no gaps.

2. **Multiple agents produce parallel branches.** When two Claude Code sessions run concurrently on the same repo, each session is a separate branch in the DAG. They converge at commits (which are serialized by git).

3. **The graph is queryable by both humans and AI agents.** Humans see a visual timeline. Agents read the structured data to understand what happened and why.

### 5.2 Schema

```python
# =========================================================================
# COMMIT RECORD - one per git commit
# The fast-access summary. Queries read these.
# =========================================================================

@dataclass
class CommitRecord:
    # Identity
    version: str                    # Schema version, e.g. "1.0"
    commit_sha: str                 # Git commit SHA
    parent_sha: Optional[str]       # Parent commit SHA (for DAG traversal)
    timestamp: str                  # ISO 8601 UTC

    # Which sessions contributed to this commit
    # Usually 1, but can be multiple if 2+ agents worked between
    # the previous commit and this one
    contributing_sessions: List[SessionLink]

    # Source
    source: str                     # "captured" | "partial" | "metadata-only"
                                    # captured: full reasoning from agent transcript
                                    # partial: agent reasoning covers some but not all changes
                                    # metadata-only: no agent transcript, just git metadata
    agents: List[str]               # ["claude-code"] or ["claude-code", "cursor"] etc.
    primary_agent: Optional[str]    # The agent that made the commit (if detectable)

    # What changed
    files_changed: List[FileChange]
    commit_message: str

    # Reasoning summary (synthesized from ALL contributing sessions)
    intent: Optional[str]
    exploration: Optional[str]
    alternatives: Optional[List[Alternative]]
    risks: Optional[List[Risk]]
    confidence: Optional[float]
    confidence_details: Optional[Dict[str, float]]

    # Context (aggregated across ALL contributing sessions)
    files_read: Optional[List[str]]
    tools_used: Optional[List[str]]
    tests_run: Optional[List[str]]
    errors_encountered: Optional[List[str]]
    dead_ends: Optional[List[str]]

    # Stats across all contributing sessions for this commit
    total_steps: int                # Total events between prev commit and this one
    total_user_prompts: int
    total_thinking_blocks: int
    total_agent_sessions: int       # How many agents contributed

    # Metadata
    capture_duration_ms: int
    record_size_bytes: int


@dataclass
class SessionLink:
    """Pointer from a commit record into a session trace."""
    session_id: str
    anchor_sha: str                 # Git SHA the session note is attached to
                                    # (the first commit made during this session)
    agent: str                      # "claude-code" | "unknown"
    msg_start: int                  # First event index in session for this commit
    msg_end: int                    # Last event index in session for this commit
    step_count: int                 # Number of events in this range
    # A commit may have multiple SessionLinks if multiple agents
    # contributed between the previous commit and this one.
    #
    # The pointer chain:
    #   CommitRecord.contributing_sessions[0].anchor_sha = "a1b2c3d"
    #   CommitRecord.contributing_sessions[0].msg_start = 0
    #   CommitRecord.contributing_sessions[0].msg_end = 120
    #     -> scan refs/notes/g4a-sessions/* for note on a1b2c3d
    #     -> Deserialize, read events[0:120]
    #
    # This lets queries start fast (read commit record only) and
    # drill down on demand (load session note when user asks for detail)


# =========================================================================
# SESSION RECORD - one per agent session
# The full trace. Detailed queries and drill-downs read these.
# =========================================================================

@dataclass
class SessionRecord:
    """Full session trace - captures EVERYTHING the agent did."""
    version: str
    session_id: str
    agent: str
    agent_version: Optional[str]
    model: Optional[str]
    started_at: str                 # ISO 8601 UTC
    last_captured_at: str           # Updated on each commit within this session

    # The full event stream (masked)
    events: List[Event]             # Every event in order
    commits_in_session: List[str]   # SHAs of all commits made during this session

    # Aggregate stats
    total_user_prompts: int
    total_thinking_blocks: int
    total_tool_calls: int
    total_files_read: int
    total_files_written: int
    total_errors: int


# =========================================================================
# EVENT - one step in the timeline
# The atomic unit of the DAG.
# =========================================================================

@dataclass
class Event:
    """One step in the reasoning timeline."""
    index: int                      # Position in the session (0-based)
    type: str                       # See EventType below
    timestamp: str                  # ISO 8601 UTC
    content: str                    # Masked content

    # Tool-specific fields
    tool_name: Optional[str]        # For tool_call/tool_result
    tool_input: Optional[dict]      # For tool_call: arguments
    tool_duration_ms: Optional[int] # How long the tool call took

    # Graph edges
    parent_event: Optional[int]     # Index of the event that caused this one
    # e.g., a tool_result's parent is the tool_call that triggered it
    # e.g., a thinking block's parent is the user_prompt it responds to
    # This builds the DAG within a session

    # Classification (helps visualization)
    is_dead_end: bool               # True if this event was part of an abandoned approach
    phase: Optional[str]            # "exploration" | "implementation" | "testing" | "debugging"
    # Phase is auto-detected from content patterns:
    #   Read/Grep/Glob -> exploration
    #   Edit/Write -> implementation
    #   Bash with test commands -> testing
    #   Bash after test failure -> debugging


class EventType:
    USER_PROMPT = "user_prompt"     # Developer typed something
    THINKING = "thinking"           # Agent's internal reasoning (extended thinking)
    TEXT = "text"                   # Agent's visible response
    TOOL_CALL = "tool_call"         # Agent invoked a tool
    TOOL_RESULT = "tool_result"     # Tool returned a result
    ERROR = "error"                 # Something went wrong
    COMMIT = "commit"               # A git commit was made (marks a boundary)
    SESSION_START = "session_start" # Agent session began
    SESSION_END = "session_end"     # Agent session ended


# =========================================================================
# TIMELINE - the unified view between two commits
# Built on-the-fly from commit records + session traces.
# This is what the CLI and UI render.
# =========================================================================

@dataclass
class Timeline:
    """The complete picture between two commits. Built at query time."""
    from_commit: str                # Parent commit SHA (or None for first commit)
    to_commit: str                  # This commit SHA
    timestamp_start: str            # Timestamp of parent commit
    timestamp_end: str              # Timestamp of this commit

    # Branches: one per agent session active in this range
    branches: List[TimelineBranch]

    # Summary stats
    total_events: int               # Across all branches
    total_agents: int               # How many agents were active
    has_parallel_work: bool         # True if 2+ agents overlapped in time


@dataclass
class TimelineBranch:
    """One agent's work between two commits."""
    session_id: str
    agent: str
    agent_version: Optional[str]
    events: List[Event]             # This agent's events in the time range
    phases: List[Phase]             # Grouped events by phase

    # Stats for this branch
    step_count: int
    user_prompts: int
    dead_end_count: int
    files_touched: List[str]


@dataclass
class Phase:
    """A group of related events within a branch."""
    name: str                       # "exploration" | "implementation" | "testing" | "debugging" | "dead_end"
    start_index: int
    end_index: int
    summary: Optional[str]          # One-line summary of what happened in this phase
    duration_ms: int


# =========================================================================
# SUPPORTING TYPES
# =========================================================================

@dataclass
class FileChange:
    path: str
    lines_added: int
    lines_removed: int
    change_type: str                # "modified" | "added" | "deleted" | "renamed"


@dataclass
class Alternative:
    description: str
    rejected_reason: Optional[str]  # null for the chosen alternative
    effort_estimate: Optional[str]


@dataclass
class Risk:
    description: str
    confidence: float               # 0.0-1.0
    file: Optional[str]
    line: Optional[int]
```

### 5.3 Why a DAG, not a flat list

**Scenario 1: Single agent, single commit** (most common)

```
Commit A ---- [agent-1: 47 steps] ---- Commit B
```

The timeline has one branch with 47 events. Simple.

**Scenario 2: Single agent, multiple commits in one session**

```
Commit A ---- [agent-1: steps 0-120] ---- Commit B
                                            |
              [agent-1: steps 121-147] ---- Commit C
```

Same session, two commit ranges. Each commit record has a `SessionLink` pointing to its range.

**Scenario 3: Two agents working concurrently**

```
Commit A ---- [agent-1: 85 steps, refactoring payments] ----+---- Commit B
         \                                                   /
          +-- [agent-2: 12 steps, updating docs] -----------+
```

Both agents worked between commit A and commit B. The timeline has two branches. The commit record has two `SessionLink` entries. The visualization shows them as parallel tracks.

**Scenario 4: Human-only commit (no agent)**

```
Commit A ---- (no agent steps) ---- Commit B
```

The timeline has zero branches and zero events. The commit record exists with `source="metadata-only"`, `total_steps=0`, `contributing_sessions=[]`. The visualization shows just the two commit markers with nothing between them. This is the minimum viable timeline entry.

**Scenario 5: Mixed - agent + human edits in same commit range**

```
Commit A ---- [agent-1: 30 steps] ---- (human edits files manually) ---- Commit B
```

g4a captures the 30 agent steps. The human edits have no trace (unless they used an agent). The commit record shows `total_steps=30` from one session, and the diff may include changes not covered by the agent's reasoning. The commit record notes this: `source="partial"` (agent reasoning covers some but not all changes).

### 5.4 How multiple agents are detected

When a commit is made, the post-commit hook:

1. Scans `~/.claude/projects/{project}/` for ALL `.jsonl` transcripts modified since the last capture watermark (`.git/g4a/last_capture_mtime`)
2. Each modified transcript is a separate agent session
3. All are captured as separate session traces
4. The commit record links to all of them via `contributing_sessions`

```python
def find_contributing_sessions(repo_root: str) -> List[str]:
    """Find all agent sessions modified since the last capture."""
    project_slug = repo_to_slug(repo_root)
    transcripts_dir = Path.home() / ".claude" / "projects" / project_slug
    watermark = read_capture_watermark()  # .git/g4a/last_capture_mtime

    sessions = []
    for jsonl in transcripts_dir.glob("*.jsonl"):
        if jsonl.stat().st_mtime > watermark:
            session_id = jsonl.stem
            sessions.append(session_id)

    return sessions  # Could be 0, 1, or many
```

**Implementation note:** The actual capture flow (Section 14.3) uses two functions: `find_transcript_with_settle` (Section 7.2) to locate the primary transcript containing the commit SHA, and `find_all_contributing_transcripts` (Section 7.4) for parallel sessions. The conceptual model above maps to those concrete APIs.

### 5.5 Schema versioning

The schema version is stored in every record. The `.g4a/schema.json` file in the repo defines the current schema. Older records are always readable - new fields are additive, never breaking. If a field is missing from an older record, queries treat it as `null`.

### 5.6 Self-describing format

`.g4a/schema.json` is a JSON Schema document that describes all record types (CommitRecord, SessionRecord, Event, etc.). Records live in git notes. Any future tool can extract the notes and parse them using the schema without g4a-specific knowledge. The schema is committed to the repo alongside configuration.

---

## 6. Storage engine

### 6.1 The three storage problems (and how we solve them)

External audit identified three critical architectural flaws in the original "everything in .g4a/ tracked by git" design:

| Problem | Impact | Solution |
|---------|--------|----------|
| **Dirty working tree** | post-commit hook writes .g4a/ files after commit, leaving untracked files that show in `git status` and get bundled into the next commit | **Git notes** for commit records (attached to SHA, never touch working tree) |
| **Binary merge conflicts** | Binary index.g4a tracked in git conflicts on every branch merge, breaks team collaboration | **Local-only index** in `.git/g4a/` (never tracked, rebuilt lazily) |
| **Git rewrites (rebase/amend/squash)** | SHA changes orphan reasoning files named after old SHAs | **post-rewrite hook** that remaps notes from old SHA to new SHA |

### 6.2 Storage architecture (revised)

g4a uses three storage locations, each chosen to avoid the problems above:

```
your-project/
  .g4a/                            # IN GIT (committed, shared)
    schema.json                    #   Schema definition
    config.json                    #   g4a configuration

  .git/
    refs/notes/g4a-commits/        # GIT NOTES (synced via fetch/push)
      {sha} -> CBOR commit record  #   Attached to exact commit SHA
                                   #   Never touches working tree
                                   #   Survives rebase via post-rewrite hook
    refs/notes/g4a-sessions/       # GIT NOTES (synced via fetch/push)
      {sha} -> CBOR session trace  #   Attached to the commit that triggered capture
                                   #   Shared across commits via session_id

    g4a/                           # LOCAL ONLY (never synced, never committed)
      client_id                    #   Stable client identifier for namespaced notes
      index.db                     #   Search index (rebuilt lazily from notes)
      last_capture_mtime           #   Monotonic watermark for transcript detection
      pending.json                 #   Retry queue
      permanent_failures.json      #   Commits that exhausted retries
      errors.log                   #   Error log
      capture.lock                 #   Concurrency lock for capture process
      cache/                       #   Deserialized record cache for faster repeated queries
```

**What goes where:**

| Data | Location | Synced? | Why |
|------|----------|---------|-----|
| Commit records | git notes (`refs/notes/g4a-commits`) | Yes, via fetch/push | Attached to exact SHA. No dirty tree. No merge conflicts. Survives rebase. |
| Session traces | git notes (`refs/notes/g4a-sessions`) | Yes, via fetch/push | Same benefits. Attached to the first commit that triggered session capture. |
| Schema + config | `.g4a/schema.json`, `.g4a/config.json` | Yes, committed | Small, rarely change, need to be visible in the repo. |
| Search index | `.git/g4a/index.db` | No, local only | Rebuilt lazily. No merge conflicts. No binary blobs in git history. |
| Operational state | `.git/g4a/pending.json`, `.git/g4a/errors.log` | No, local only | Machine-specific. Never leaks to remote. |

### 6.3 Why git notes

Git notes (`git notes`) attach arbitrary data to a commit **without modifying the commit's SHA and without touching the working tree.** This solves all three critical problems:

**The merge collision problem:** If two developers both create notes on a shared ref like `refs/notes/g4a-commits`, their local refs diverge. When one pushes, the other's push fails with a non-fast-forward rejection. This breaks the "invisible" promise.

**The solution: Per-client namespaced refs.** Each machine writes to its own notes namespace. No two machines ever write to the same ref, so pushes never collide.

```bash
# During g4a init, generate a stable client ID
# Uses git user.email + machine hostname hash for stability across sessions
CLIENT_ID=$(echo "$(git config user.email)@$(hostname)" | sha256sum | head -c 12)
# e.g., CLIENT_ID = "a3f8b2c1d4e5"

# g4a writes notes to client-specific refs:
git notes --ref=g4a-commits/$CLIENT_ID add -f \
  -C $(echo "$CBOR_BLOB" | git hash-object -w --stdin) a1b2c3d

# Reading scans ALL client namespaces:
for ref in $(git for-each-ref --format='%(refname)' refs/notes/g4a-commits/); do
  git notes --ref=$ref show a1b2c3d 2>/dev/null && break
done
```

**How `g4a init` configures note syncing:**

```bash
CLIENT_ID="a3f8b2c1d4e5"  # Generated once, stored in .git/g4a/client_id

# Fetch ALL clients' notes (wildcard - safe because each client pushes only their own)
git config --add remote.origin.fetch "+refs/notes/g4a-commits/*:refs/notes/g4a-commits/*"
git config --add remote.origin.fetch "+refs/notes/g4a-sessions/*:refs/notes/g4a-sessions/*"

# Push ONLY this client's notes (no collision possible)
git config --add remote.origin.push "HEAD"
git config --add remote.origin.push "refs/notes/g4a-commits/$CLIENT_ID:refs/notes/g4a-commits/$CLIENT_ID"
git config --add remote.origin.push "refs/notes/g4a-sessions/$CLIENT_ID:refs/notes/g4a-sessions/$CLIENT_ID"
```

**Why this eliminates merge conflicts:**
- Developer A pushes to `refs/notes/g4a-commits/a3f8b2c1d4e5`
- Developer B pushes to `refs/notes/g4a-commits/7d9e1f0a2b3c`
- These are different refs. No conflict possible. Ever.
- Force-fetching with `+` is safe because no one else pushes to your client ID
- Queries scan all `refs/notes/g4a-commits/*` namespaces to find notes for any commit

**Query merge rules when multiple clients have notes on the same commit:**

| Scenario | Merge behavior |
|----------|---------------|
| One `captured`, one `metadata-only` | Use the `captured` record, discard `metadata-only` |
| Two `captured` from different clients | Show both (different agents may have captured different sessions) |
| Two `captured` from same session_id | Deduplicate by session_id, keep the one with more events |
| Multiple `metadata-only` | Keep one (they're identical - same git metadata) |

Records are deduplicated by `(commit_sha, session_id)`. The query engine scans all `refs/notes/g4a-commits/*` and merges results per commit SHA.

After init, `git fetch` automatically pulls all developers' reasoning. `git push` automatically pushes this developer's reasoning. Single connection, no race conditions, no TTY issues, no merge conflicts.

**Client ID stability:** The client ID is generated once and stored in `.git/g4a/client_id`. Changing `user.email` or moving to a new machine creates a new client namespace. Old notes remain under the old client ref and are still readable by everyone. This is by design - it prevents one machine from overwriting another's notes.

**Git notes vs. working tree files:**

| Concern | Working tree (.g4a/) | Git notes |
|---------|---------------------|-----------|
| Dirty tree after commit | Yes (critical flaw) | No |
| Merge conflicts | Yes (binary files) | No (notes merge per-commit) |
| Survives rebase/amend | No (SHA changes) | Yes (with post-rewrite hook) |
| Visible in `git status` | Yes (noise) | No |
| Cloned by default | Yes | No (need fetch config) |
| GitHub web UI | Not readable (binary) | Not displayed (but fetchable) |
| Works with all git hosts | Yes | Yes (GitHub, GitLab, Bitbucket all support notes) |

**Trade-off:** Notes are not cloned by default. A fresh clone needs `g4a init` to configure note fetching, then `git fetch` to pull the reasoning. This is an extra step compared to "clone and it's there," but it avoids all three critical problems.

**Fresh clone flow:**

```
$ git clone git@github.com:team/project.git
$ cd project
$ g4a init         # Configures note fetch/push, installs hooks
$ git fetch        # Pulls all reasoning notes from remote
$ g4a log          # Reasoning is now available
```

If a developer runs `g4a log` before `git fetch`, g4a detects the empty state and prompts:

```
$ g4a log
  No reasoning data found. Run "git fetch" to pull reasoning from remote.
  (If this is a new repo, reasoning will appear after your first commit.)
```

Teams already using g4a can add `g4a init && git fetch` to their onboarding script or README.

### 6.4 Handling git rewrites (rebase, amend, squash)

Git provides a `post-rewrite` hook that fires after `git commit --amend` and `git rebase`. It receives `old-sha new-sha` pairs on stdin.

```bash
#!/bin/sh
# .git/hooks/post-rewrite - installed by g4a init
# Remaps reasoning notes from old SHAs to new SHAs after rebase/amend
# Scans ALL client namespaces - a developer may rebase commits whose
# reasoning was created on a different machine

REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

while read old_sha new_sha _rest; do
  # Scan all client namespaces for commit notes on the old SHA
  for ref in $(git for-each-ref --format='%(refname)' refs/notes/g4a-commits/); do
    if git notes --ref="$ref" show "$old_sha" >/dev/null 2>&1; then
      git notes --ref="$ref" copy "$old_sha" "$new_sha" 2>/dev/null
      git notes --ref="$ref" remove "$old_sha" 2>/dev/null
    fi
  done

  # Same for session notes (anchors may live on any client)
  for ref in $(git for-each-ref --format='%(refname)' refs/notes/g4a-sessions/); do
    if git notes --ref="$ref" show "$old_sha" >/dev/null 2>&1; then
      git notes --ref="$ref" copy "$old_sha" "$new_sha" 2>/dev/null
      git notes --ref="$ref" remove "$old_sha" 2>/dev/null
    fi
  done
done

# Rebuild local index (anchor SHAs may have changed)
cd "$REPO" && python3 -m g4a reindex --quiet 2>/dev/null &
```

This means reasoning **survives rebases, amends, and squashes** seamlessly. Both commit records and session traces are remapped. The local index is rebuilt in the background since anchor SHAs may have changed.

**Session note anchoring:** Each session trace is stored as a git note attached to the **first commit made during that session** (the "anchor SHA"). The `anchor_sha` is stored in every `SessionLink` so readers can look up the session note directly without scanning. When the anchor commit is rewritten, the post-rewrite hook remaps the session note to the new SHA, and `g4a reindex` updates the local `session_id -> anchor_sha` mapping.

### 6.5 File format: .g4a payloads

Each note payload (commit record or session trace) is:

```
[4 bytes: magic number "G4A\x01"]
[4 bytes: schema version as uint32]
[N bytes: CBOR payload (uncompressed)]
```

**Why no zstd compression:** Git's object store already compresses with zlib and delta-packs similar objects. If we compress with zstd before handing data to git, the bytes look random and git cannot delta-compress them. A 100 KB session appended 20 times would store 20 full copies (~2 MB) instead of one base + 19 tiny deltas (~120 KB). Letting git handle compression natively is vastly more storage-efficient over the lifecycle of the repo.

**Why CBOR (not JSON):**
- ~30% smaller than JSON for structured data
- Much faster to parse (binary format, no string escaping)
- Self-describing (no external schema needed to decode)
- IETF RFC 8949 standard

**Expected sizes (uncompressed CBOR, before git's native compression):**
- Simple commit record (1-3 files, basic reasoning): 3-8 KB
- Complex commit record (8+ files, detailed alternatives): 20-80 KB
- Full session trace (multi-hour Claude Code session): 200 KB - 1 MB

Git's zlib compression typically achieves 2-3x on CBOR. Git's delta packing achieves much more for session appends (only the diff between versions is stored).

### 6.6 Search index (local only)

The search index lives in `.git/g4a/index.db` and is **never tracked by git.** It is a custom binary format (sorted CBOR arrays, not SQLite), rebuilt lazily from git notes.

```
file_path    -> [commit_sha, commit_sha, ...]
function_name -> [commit_sha, commit_sha, ...]
keyword      -> [commit_sha, commit_sha, ...]
```

**Lazy rebuild strategy:**

```python
def ensure_index_fresh():
    """Rebuild index if stale. Runs on every query.

    Tracks the notes ref tree SHA, NOT HEAD. Git notes are detached
    from branch topology - a note added on feature-branch is visible
    from main. Tracking HEAD would miss notes from other branches.
    """
    last_indexed_tree = read_index_watermark()
    # Hash all client ref trees together to detect any change across all clients
    all_refs = sorted(git_for_each_ref("refs/notes/g4a-commits/"))
    current_tree = hash_of(all_refs)  # Changes when any client adds/removes a note

    if last_indexed_tree == current_tree:
        return  # Index is up to date

    # Scan all client namespaces for new/changed notes.
    # When multiple clients have notes for the same commit, the index
    # applies merge rules (Section 6.3): captured > partial > metadata-only.
    for client_ref in all_refs:
        for sha in git_notes_list(client_ref):
            note = git_notes_show(client_ref, sha)
            if note:
                record = deserialize(note)
                # Merge rules (Section 6.3): if the index already has a record
                # for this commit_sha, keep the one with higher source quality
                # (captured > partial > metadata-only). Same quality = keep first.
                add_to_index_with_merge(record)

    write_index_watermark(current_tree)
```

The index scans all `refs/notes/g4a-commits/*` client namespaces and tracks a combined hash. Notes from any developer on any branch are indexed. Branch switches don't invalidate the index. Indexing 50 new notes takes < 100ms.

**Corruption detection:** The index stores a SHA-256 checksum in its header. On load, the checksum is verified. If it fails (truncated write, disk error), the index is deleted and rebuilt from git notes on the next query. No data loss - git notes are the source of truth.

**Orphaned session detection and re-anchoring:**

During interactive rebase, a developer may drop a commit that served as a session anchor. The post-rewrite hook only handles remaps (old -> new), not deletions. `g4a reindex` handles this:

```python
def repair_orphaned_sessions():
    """Find session notes attached to dropped SHAs and re-anchor them."""
    for client_ref in list_notes_refs("g4a-sessions"):
        for sha, blob in list_notes(client_ref):
            if not git_object_exists(sha):
                # This SHA was dropped (e.g., interactive rebase deleted it)
                session = deserialize_session(blob)
                # Find the oldest surviving commit that references this session
                new_anchor = find_oldest_surviving_commit(session.session_id)
                if new_anchor:
                    git_notes_add(client_ref, new_anchor, blob)
                    git_notes_remove(client_ref, sha)
                    log(f"Re-anchored session {session.session_id} "
                        f"from dropped {sha[:7]} to {new_anchor[:7]}")
                else:
                    log(f"Session {session.session_id} has no surviving commits. "
                        f"Note preserved on orphan SHA for manual recovery.")
```

This runs during `g4a reindex` and also in the background after the post-rewrite hook fires.

### 6.7 Session trace append strategy

A long session may span multiple commits. Each commit triggers a full rewrite of the session note (read, append new events, write). Since we store uncompressed CBOR and let git handle compression, git's delta packing stores only the diff between the old and new version of the note - making appends very storage-efficient.

```python
def append_to_session(anchor_sha: str, session_id: str, new_events: List[Event]):
    """Append new events to an existing session note."""
    # Load existing session (or create new)
    client_id = read_client_id()  # From .git/g4a/client_id
    session_ref = f"g4a-sessions/{client_id}"

    existing_blob = git_notes_show(session_ref, anchor_sha)
    if existing_blob:
        session = cbor_decode(strip_header(existing_blob))
    else:
        session = {"version": "1.0", "session_id": session_id, "events": []}

    # Append new events
    for event in new_events:
        event.index = len(session["events"])
        session["events"].append(event)

    # Update stats
    session["last_captured_at"] = now()

    # Rewrite the note (git delta-packs the diff automatically)
    blob = g4a_header() + cbor_encode(session)
    git_notes_add(session_ref, anchor_sha, blob)
```

**Why full rewrite is fine:** Git's object store delta-packs similar blobs during `git gc` and `git push`. Appending 5 KB of events to a 200 KB session creates a new 205 KB blob, but git stores only the ~5 KB delta. Over 20 commits in a session, git stores ~200 KB base + 19 tiny deltas, not 20 full copies.

**Size limits:** If a session trace exceeds 1 MB (uncompressed CBOR), older `tool_result` content is pruned (replaced with `[PRUNED - see original transcript]`) while preserving all thinking, text, and tool_call events. The full content is always available in the original Claude Code transcript on the developer's machine.

### 6.8 Session note lookup

Session notes are keyed by git SHA (the anchor commit), not by `session_id`. To find a session note, the reader needs the `anchor_sha`. This is stored in two places:

1. **In every CommitRecord:** `SessionLink.anchor_sha` provides direct lookup
2. **In the local index:** `.git/g4a/index.db` maintains a `session_id -> anchor_sha` mapping, rebuilt lazily from commit notes

```python
def load_session(session_link: SessionLink) -> SessionRecord:
    """Load a session trace by scanning all client namespaces for the anchor SHA."""
    for ref in git_for_each_ref("refs/notes/g4a-sessions/"):
        blob = git_notes_show(ref, session_link.anchor_sha)
        if blob:
            return deserialize_session(blob)
    raise SessionNotFound(session_link.session_id)
```

For `g4a session <id>` (lookup by session_id without a commit context), the local index provides the mapping:

```python
def find_session_by_id(session_id: str) -> SessionRecord:
    """Look up session by ID via local index."""
    anchor_sha = index_lookup_session(session_id)
    if not anchor_sha:
        raise SessionNotFound(session_id)
    # Reuse load_session with a minimal SessionLink
    link = SessionLink(session_id=session_id, anchor_sha=anchor_sha,
                       agent="", msg_start=0, msg_end=0, step_count=0)
    return load_session(link)
```

---

## 7. Capture engine

### 7.1 Concurrency and locking

Multiple commits in quick succession (e.g., during a rebase, or two terminals committing simultaneously) could spawn multiple background capture processes. g4a uses a simple file lock to serialize capture:

```python
LOCK_FILE = ".git/g4a/capture.lock"

def acquire_lock(timeout_seconds=30) -> bool:
    """Acquire exclusive capture lock. Returns False if already held."""
    lock_path = Path(LOCK_FILE)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Atomic create - fails if file exists
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()}\n{time.time()}\n".encode())
        os.close(fd)
        return True
    except FileExistsError:
        # Lock exists - check if it's stale (holder crashed)
        try:
            content = lock_path.read_text()
            pid, timestamp = content.strip().split("\n")
            age = time.time() - float(timestamp)
            if age > timeout_seconds:
                # Stale lock - remove and retry
                lock_path.unlink()
                return acquire_lock(timeout_seconds)
        except Exception:
            lock_path.unlink(missing_ok=True)
            return acquire_lock(timeout_seconds)
        return False

def release_lock():
    Path(LOCK_FILE).unlink(missing_ok=True)
```

**Behavior under concurrent commits:**
- Commit A fires hook, acquires lock, starts capture
- Commit B fires hook 2 seconds later, lock is held, adds B to `.git/g4a/pending.json`
- B then rechecks the lock (it may have been released while B was writing to the queue):

```python
def capture_or_queue(commit_sha: str):
    if acquire_lock():
        try:
            run_capture(commit_sha)
        finally:
            release_lock()
    else:
        add_to_retry_queue(commit_sha)
        # Recheck: lock holder may have finished and drained queue
        # before our queue entry was visible
        time.sleep(0.1)
        if acquire_lock():
            try:
                drain_pending_retries()
            finally:
                release_lock()
```

- Result: both commits get reasoning records, no orphaned queue entries

**Mass rebase protection:** The `G4A_DISABLE=1` env var skips the hook entirely. For interactive rebases involving 50+ commits, the developer can run `G4A_DISABLE=1 git rebase -i` to avoid spawning 50 background processes. After the rebase, the `post-rewrite` hook remaps existing notes to new SHAs.

### 7.2 Transcript detection (settle period)

**The problem:** Claude Code may not have flushed the final tool result to the `.jsonl` transcript by the time the post-commit hook fires (< 50ms after commit). The transcript might be missing the `git commit` tool call that just happened.

**The solution:** The background capture process implements a "settle period" - if the expected commit SHA is not found in the transcript, wait and retry:

```python
def find_transcript_with_settle(commit_sha: str, repo_root: str,
                                 max_wait_ms=2000, poll_ms=200) -> Optional[Path]:
    """Find the transcript, with settle period for write buffering."""
    project_slug = repo_to_slug(repo_root)
    transcripts_dir = Path.home() / ".claude" / "projects" / project_slug

    # Strategy: use a monotonic watermark (wall clock time of last capture)
    # NOT the parent commit timestamp - that's the author time, which can be
    # months old if the developer branched from an old commit.
    # The watermark is stored in .git/g4a/last_capture_mtime
    watermark = read_capture_watermark()  # Returns 0.0 on first run

    for attempt in range(max_wait_ms // poll_ms):
        for jsonl in transcripts_dir.glob("*.jsonl"):
            if jsonl.stat().st_mtime > watermark:
                # Check if this transcript contains our commit SHA
                # Read from the END of the file (tail) for efficiency
                if transcript_contains_commit(jsonl, commit_sha):
                    return jsonl

        # Settle: transcript might not be flushed yet
        if attempt < (max_wait_ms // poll_ms) - 1:
            time.sleep(poll_ms / 1000)

    return None  # No transcript found after settle period
```

**When no transcript is found:** This happens when `find_transcript_with_settle` returns `None` (no transcript contained the commit SHA after 2 seconds of polling). The capture process differentiates "slow flush" from "human commit" using an activity heuristic:

```python
def should_retry_capture(repo_root: str) -> bool:
    """Was Claude Code recently active? If not, this is a human commit."""
    project_slug = repo_to_slug(repo_root)
    transcripts_dir = Path.home() / ".claude" / "projects" / project_slug
    if not transcripts_dir.exists():
        return False
    # Check if ANY transcript was modified in the last 5 minutes
    cutoff = time.time() - 300
    return any(f.stat().st_mtime > cutoff for f in transcripts_dir.glob("*.jsonl"))
```

- **Claude recently active (transcript modified < 5 min ago):** Likely a slow flush. Write metadata-only AND queue a retry. The retry will pick up the flushed transcript on the next commit.
- **Claude NOT recently active:** This is a human commit. Write metadata-only, do NOT queue a retry, do NOT log an error. Human commits are expected and normal - they should never pollute the error log or retry queue.

**Key design decisions:**
- Uses "modified since last capture" (monotonic watermark in `.git/g4a/last_capture_mtime`), not the parent commit timestamp which can be arbitrarily old on long-lived branches
- Reads transcript from the **tail** (last ~50 KB) to check for the commit SHA. The commit-producing event is always near the end at capture time (the hook fires immediately after commit). For retries on older commits, a full scan may be needed.
- Polls every 200ms for up to 2 seconds total (10 attempts). Fixed interval keeps the implementation simple.
- The watermark (`last_capture_mtime`) is updated immediately after a successful capture (whether full or metadata-only), before the capture process exits. This ensures the next commit only scans transcripts modified after this one.
- Falls back to metadata-only if transcript never appears

### 7.3 Project slug algorithm

The project slug maps a repo path to a Claude Code transcript directory. This must be stable and unambiguous:

```python
def repo_to_slug(repo_root: str) -> str:
    """Convert repo path to Claude Code project slug.

    Matches Claude Code's internal algorithm:
    - Absolute path with os.sep replaced by '-'
    - Leading separator becomes leading '-'

    Examples:
      /Users/lokesh/project  -> -Users-lokesh-project
      C:\\Users\\lokesh\\proj -> C-Users-lokesh-proj  (Windows)
      /home/lokesh/project   -> -home-lokesh-project  (Linux)
    """
    # Normalize path (resolve symlinks, remove trailing slash)
    normalized = str(Path(repo_root).resolve())
    # Replace all separators with '-'
    slug = normalized.replace(os.sep, "-")
    # On Windows, also replace drive colon
    slug = slug.replace(":", "")
    return slug
```

**Edge cases:**
- **Windows drive letters:** `C:\foo` becomes `C-foo` (colon stripped)
- **WSL paths:** `/mnt/c/foo` becomes `-mnt-c-foo` (standard Linux path handling)
- **Symlinks:** Resolved before slugging, so `/tmp/mylink -> /home/user/project` uses the real path
- **Multi-root workspaces:** Each repo has its own slug based on its own `.git` root. g4a operates per-repo.

### 7.4 Agent detection and multi-session capture (POC)

The capture process finds **all** transcripts modified since the last capture (watermark), not just the one containing the commit SHA. This supports the multi-agent scenario (two Claude Code windows working concurrently):

```python
def find_all_contributing_transcripts(commit_sha: str, repo_root: str) -> List[Path]:
    """Find all transcripts modified since the last capture."""
    project_slug = repo_to_slug(repo_root)
    transcripts_dir = Path.home() / ".claude" / "projects" / project_slug

    # Use monotonic watermark, not git parent timestamp.
    # Parent timestamp is author time (could be months old on old branches).
    # Watermark is wall clock time of last successful capture on this machine.
    watermark = read_capture_watermark()  # 0.0 on first run

    # Find all modified transcripts (multiple agents = multiple transcripts)
    modified = [
        jsonl for jsonl in transcripts_dir.glob("*.jsonl")
        if jsonl.stat().st_mtime > watermark
    ]

    if not modified:
        return []

    # The transcript containing the commit SHA is the "primary" one
    # Others are "contributing" (worked in parallel but didn't commit)
    # All are captured as separate sessions
    return modified
```

For each transcript found, g4a creates a separate SessionRecord and SessionLink. The commit record's `contributing_sessions` list has one entry per transcript. `primary_agent` is the agent whose transcript contains the commit SHA.

### 7.5 Claude Code adapter (deep dive)

This is the highest-value adapter. Claude Code's JSONL transcript contains everything g4a needs.

**Transcript location:**
```
~/.claude/projects/{project-slug}/{session-id}.jsonl
```

Where `{project-slug}` is the absolute path with `/` replaced by `-`, e.g.:
`-Users-lokeshbasu-Developer-git4aiagents`

**Transcript format (from direct inspection):**

Each line is a JSON object with a `type` field:

| Type | Content |
|------|---------|
| `user` | User messages and tool results |
| `assistant` | Agent responses: thinking blocks, text blocks, tool_use blocks |
| `system` | System messages (context loading) |
| `progress` | Streaming progress indicators |
| `file-history-snapshot` | File state snapshots for undo |

**What g4a extracts from assistant messages:**

```python
for message in transcript:
    if message["type"] == "assistant":
        for block in message["message"]["content"]:
            if block["type"] == "thinking":
                # Extended thinking - the richest reasoning source
                # Contains the agent's internal deliberation
                reasoning_chunks.append(block["thinking"])

            elif block["type"] == "text":
                # Visible reasoning - what the agent said to the user
                # Contains summaries, explanations, decisions
                visible_reasoning.append(block["text"])

            elif block["type"] == "tool_use":
                # Tool calls - what the agent did
                # name: "Read", "Edit", "Write", "Bash", "Grep", "Glob"
                # input: tool-specific arguments
                tool_calls.append({
                    "tool": block["name"],
                    "input": block["input"],
                    "timestamp": message["timestamp"]
                })
```

**What g4a extracts from user messages (tool results):**

```python
for message in transcript:
    if message["type"] == "user":
        for block in message["message"]["content"]:
            if block["type"] == "tool_result":
                # Tool results - what the agent learned
                # Contains file contents, command output, search results
                tool_results.append({
                    "tool_use_id": block["tool_use_id"],
                    "content": block["content"]  # May be truncated for size
                })
```

**Session capture (full trace):**

```python
def capture_session(transcript_path: str, session_id: str) -> SessionRecord:
    """Capture the ENTIRE session - every message, every dead end."""

    # Resolve session_id to anchor SHA via local index (.git/g4a/index.db)
    # For the first commit in a session, no anchor exists yet - returns None
    existing = load_existing_session_from_notes(session_id)
    last_captured_index = existing.last_index if existing else -1

    session = existing or SessionRecord(session_id=session_id)

    # Parse ALL new messages since last capture
    for i, line in enumerate(read_jsonl(transcript_path)):
        if i <= last_captured_index:
            continue  # Already captured in a prior commit

        msg = parse_message(line)
        if msg is None:
            continue  # Skip progress/snapshot messages

        # Mask secrets BEFORE adding to session
        msg.content = mask_secrets(msg.content)
        if msg.tool_input:
            msg.tool_input = mask_secrets_in_dict(msg.tool_input)

        # Smart truncation: keep first 1000 + last 1000 chars
        # This preserves both the context (start) and git's output (end)
        # which is critical for commit SHA detection in tool results
        if msg.type == "tool_result" and len(msg.content) > 2000:
            msg.content = (msg.content[:1000]
                          + "\n[TRUNCATED - full content in original transcript]\n"
                          + msg.content[-1000:])

        msg.index = len(session.events)
        session.events.append(msg)

    session.last_captured_at = now()
    return session
```

**Commit range detection:**

A session may contain multiple commits. Each commit record needs to know which messages in the session led to it. g4a finds the range by locating commit boundaries:

```python
def find_commit_range(session: SessionRecord, commit_sha: str) -> Tuple[int, int]:
    """Find the event range in the session that produced this commit."""

    # Find the event where this commit was made.
    # Git's stdout format: "[main a1b2c3d] commit message"
    # The abbreviated SHA length is controlled by core.abbrev (default: 7).
    # We match using a regex to handle any abbreviation length.
    SHA_PATTERN = re.compile(r'\[.*? ([0-9a-f]{7,40})\]')
    commit_evt_index = None

    for i, evt in enumerate(session.events):
        if (evt.type == "tool_call" and evt.tool_name == "Bash"
                and "git commit" in str(evt.tool_input)):
            # Check the next event (tool_result) for the SHA in git's output
            if i + 1 < len(session.events):
                result = session.events[i + 1]
                if result.type == "tool_result":
                    match = SHA_PATTERN.search(result.content)
                    if match and commit_sha.startswith(match.group(1)):
                        commit_evt_index = evt.index
                        break

    # Also check for "commit" type events (g4a marks these during capture)
    if commit_evt_index is None:
        for evt in reversed(session.events):
            if evt.type == "commit" and commit_sha[:7] in evt.content:
                commit_evt_index = evt.index
                break

    if commit_evt_index is None:
        # Commit wasn't made through a tool call we can find
        # (e.g. user committed manually) - use all events since last commit
        # last_commit_end_index: returns the event index of the last detected
        # commit boundary in this session, or -1 if none. Used to bound the
        # range for manual commits that don't appear in the transcript.
        return (last_commit_end_index(session, commit_sha) + 1,
                len(session.events) - 1)

    # Walk backward to find start of this unit of work
    # Start is whichever comes first:
    #   1. The message after the PREVIOUS commit in this session
    #   2. The first message in the session (if this is the first commit)
    previous_commits = [m.index for m in session.events
                        if m.type == "tool_call"
                        and m.tool_name == "Bash"
                        and "git commit" in str(m.tool_input)
                        and m.index < commit_evt_index]

    if previous_commits:
        start_index = previous_commits[-1] + 1  # Message after last commit
    else:
        start_index = 0  # First commit in session - include everything

    return (start_index, commit_evt_index)
```

**Why the full range matters:**

```
Session with 147 messages, 2 commits:

Messages 0-15:    User asks to refactor payments
Messages 16-45:   Agent reads 8 files, explores codebase
Messages 46-78:   Agent tries integer cents approach, tests fail, backtracks
Messages 79-110:  Agent tries Decimal approach, reads settlement job
Messages 111-120: Agent writes code, runs tests, commits (SHA: a1b2c3d)
                  ^^^ Commit 1 range: messages 0-120 (ALL of the above)

Messages 121-130: User asks to also update the reporting module
Messages 131-140: Agent reads report.py, updates it
Messages 141-147: Agent commits (SHA: e4f5g6h)
                  ^^^ Commit 2 range: messages 121-147

Both commit records link to the same session file.
Commit 1's range captures the dead end with integer cents.
Commit 2's range is short because the exploration was minimal.
```

**Reasoning synthesis (from the commit range):**

```python
def synthesize_reasoning(session: SessionRecord,
                         start: int, end: int) -> CommitRecord:
    """Synthesize a commit summary from ALL messages in the range."""

    messages = session.events[start:end + 1]

    # Collect ALL thinking blocks - this is where dead ends and
    # alternatives live
    all_thinking = [m.content for m in messages if m.type == "thinking"]

    # Collect ALL text blocks - agent's visible reasoning
    all_text = [m.content for m in messages if m.type == "text"]

    # Collect ALL tool calls - what the agent actually did
    all_tool_calls = [m for m in messages if m.type == "tool_call"]

    # Collect ALL user prompts - the developer's requests and corrections
    all_user_prompts = [m for m in messages if m.type == "user_prompt"]

    # Collect errors - things that went wrong and the agent recovered from
    all_errors = [m for m in messages if m.type == "error"]

    record = CommitRecord()

    # Intent: from thinking + text across the ENTIRE range
    record.intent = extract_intent(all_thinking + all_text)

    # Exploration: EVERY file read, not just the last few
    record.files_read = deduplicate([
        m.tool_input["file_path"] for m in all_tool_calls
        if m.tool_name == "Read" and "file_path" in (m.tool_input or {})
    ])

    record.tools_used = deduplicate([m.tool_name for m in all_tool_calls])

    # Alternatives: from thinking blocks across the full range
    # This is where dead ends (like integer cents) get captured
    record.alternatives = extract_alternatives(all_thinking)

    # Dead ends: approaches that were tried, produced code/tests, but
    # were then abandoned. Detected by finding Edit/Write tool calls
    # followed by reverts or different approaches
    record.dead_ends = detect_dead_ends(messages)  # Needs full event sequence for order

    # Risks: from thinking + text
    record.risks = extract_risks(all_thinking + all_text)

    # Confidence
    record.confidence = estimate_confidence(all_thinking + all_text)

    # Tests: every test command run, including ones that failed
    record.tests_run = [
        m.tool_input.get("command", "") for m in all_tool_calls
        if m.tool_name == "Bash"
        and is_test_command(m.tool_input.get("command", ""))
    ]

    # Errors encountered and recovered from
    record.errors_encountered = [m.content for m in all_errors]

    # Stats
    record.total_user_prompts = len(all_user_prompts)
    record.total_thinking_blocks = len(all_thinking)

    return record
```

### 7.6 Reasoning synthesis heuristics

The `extract_intent`, `extract_alternatives`, `extract_risks`, and `detect_dead_ends` functions are pattern-based heuristics. They will be tuned during POC, but here are the initial rules:

**extract_intent(thinking + text) -> str:**

```python
INTENT_PATTERNS = [
    r"(?:I need to|The goal is|This change will|We should|Let me)\s+(.+?)(?:\.|$)",
    r"(?:Intent|Purpose|Reason|Why):\s*(.+?)(?:\.|$)",
    r"(?:to fix|to add|to refactor|to update|to implement)\s+(.+?)(?:\.|$)",
]

def extract_intent(blocks: List[str]) -> str:
    # 1. Check the FIRST thinking block - usually states the goal
    # 2. Check the LAST text block before the commit - usually summarizes
    # 3. Fall back to the commit message if no patterns match
    # Combine into 1-3 sentences max
```

**extract_alternatives(thinking) -> List[Alternative]:**

```python
ALTERNATIVE_PATTERNS = [
    r"(?:Option|Approach|Alternative)\s*\d+[.:]\s*(.+)",
    r"(?:I could also|Another approach|We could)\s+(.+?)(?:\.|$)",
    r"(.+?)\s*[-:]\s*rejected\s+because\s+(.+)",
    r"(?:Let me try|I'll try)\s+(.+?)(?:\.|$)",  # Start of an approach
]

REJECTION_PATTERNS = [
    r"(?:rejected|abandoned|won't work|doesn't work|too .+)\s+because\s+(.+)",
    r"(?:This approach|That|It)\s+(?:failed|broke|caused)\s+(.+)",
]
```

**extract_risks(thinking + text) -> List[Risk]:**

```python
RISK_PATTERNS = [
    r"(?:risk|concern|worry|caution|careful|might break|may not|edge case)\s*[.:]\s*(.+)",
    r"(?:LOW|MEDIUM|HIGH)\s+confidence\s+(?:on|about|for)\s+(.+)",
    r"(?:not sure|uncertain|unclear)\s+(?:about|whether|if)\s+(.+)",
    r"(?:flagged|warning|note)\s*[.:]\s*(.+)",
]

def extract_risks(blocks: List[str]) -> List[Risk]:
    # Extract risk text
    # Assign confidence: explicit mentions ("LOW confidence") take priority
    # Otherwise, hedging language ("not sure") -> 0.5, "might" -> 0.6, etc.
```

**detect_dead_ends(events) -> List[str]:**

```python
def detect_dead_ends(events: List[Event]) -> List[str]:
    """Detect approaches that were tried and abandoned.

    Heuristic: a dead end is a sequence where:
    1. The agent starts implementing (Edit/Write calls)
    2. Tests fail or the agent explicitly abandons ("won't work", "let me try X instead")
    3. The agent then implements a DIFFERENT approach on the same files

    Specifically:
    - Edit file A -> Bash(test) -> FAIL -> Edit file A (different content) = dead end
    - Thinking "let me try X" -> Edit -> Thinking "X won't work, try Y" = dead end
    - Edit -> Edit -> Edit (same file, progressive refinement) = NOT a dead end
    """
    dead_ends = []
    # Walk events, track Edit sequences per file
    # If an Edit is followed by a test failure and then a different Edit
    # to the same file, mark the first sequence as dead end
    # Will be tuned during POC with real session data
```

**Phase auto-detection:**

```python
def classify_phase(event: Event) -> str:
    if event.type == "tool_call":
        if event.tool_name in ("Read", "Grep", "Glob"):
            return "exploration"
        if event.tool_name in ("Edit", "Write"):
            return "implementation"
        if event.tool_name == "Bash":
            cmd = event.tool_input.get("command", "")
            if any(t in cmd for t in ["pytest", "jest", "npm test", "cargo test", "go test"]):
                return "testing"
            return "implementation"  # Other bash commands
    if event.type == "thinking":
        return "exploration"  # Thinking is part of exploration
    return "exploration"  # Default
```

### 7.7 Large transcript handling

Claude Code transcripts can grow to 100MB+ for long sessions. g4a never loads the full file into memory:

```python
def read_transcript_tail(path: Path, max_bytes: int = 50_000) -> List[dict]:
    """Read the last N bytes of a JSONL file. Used for commit SHA detection."""
    size = path.stat().st_size
    offset = max(0, size - max_bytes)
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    # Skip partial first line (we seeked into the middle)
    lines = chunk.split(b"\n")
    if offset > 0:
        lines = lines[1:]  # First line is likely truncated
    return [json.loads(line) for line in lines if line.strip()]

def stream_transcript(path: Path, start_line: int = 0):
    """Stream JSONL lines without loading into memory."""
    with open(path, "r") as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue
            line = line.strip()
            if line:
                yield i, json.loads(line)
```

### 7.8 Human reasoning via commit trailer

When a human commits without an agent, they can optionally add reasoning via a git trailer:

```bash
git commit -m "fix: bypass cache for stale sessions

Reasoning: The cache TTL was set to 24h but sessions expire after 1h.
Users hitting the cache got stale session data. Bypassing the cache
entirely until we implement proper TTL alignment."
```

g4a detects the `Reasoning:` trailer and extracts it as the `intent` field in the commit record, upgrading it from `metadata-only` to `partial`.

### 7.9 Other agents (post-POC)

The POC ships with Claude Code only. The adapter interface is designed so new adapters can be added without changing any other component. See [Future adapters](#future-adapters-post-poc) in the architecture overview.

When no transcript is found and no `Reasoning:` trailer exists, g4a writes a metadata-only record so the timeline has no gaps.

---

## 8. Secret masking pipeline

The most security-critical component. Every string in the reasoning record passes through this pipeline before being written to disk. There is no way to disable it.

### 8.1 Pipeline stages

```
Raw reasoning text
        |
        v
  [Stage 0: Allowlist]
  Skip known safe high-entropy strings (git SHAs, UUIDs, SRI hashes)
        |
        v
  [Stage 1: Known patterns]
  Regex matching for 80+ known secret formats
        |
        v
  [Stage 2: Entropy detection]
  Shannon entropy analysis for unknown secrets (with context requirement)
        |
        v
  [Stage 3: Context-aware detection]
  Variable names, key-value pairs near sensitive keys
        |
        v
  [Stage 4: Path sanitization]
  Replace absolute paths with relative paths
        |
        v
  Masked reasoning text
```

### 8.2 Stage 0: Allowlist (prevent false positives)

High-entropy strings that are NOT secrets. These are skipped before entropy detection:

```python
ALLOWLIST_PATTERNS = [
    # Git SHAs - only when preceded by common git context words
    (r'(?<=commit )[0-9a-f]{40}\b', "git_sha"),
    (r'(?<=parent )[0-9a-f]{40}\b', "git_sha"),
    (r'(?<=tree )[0-9a-f]{40}\b', "git_sha"),
    (r'(?<=index )[0-9a-f]{7,40}\b', "git_sha"),
    # SHA-256 in lockfiles and SRI hashes
    (r'sha256-[A-Za-z0-9+/=]{40,}', "sri_hash"),
    (r'sha512-[A-Za-z0-9+/=]{40,}', "sri_hash"),
    # UUIDs
    (r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', "uuid"),
    # Semantic versions
    (r'\bv?\d+\.\d+\.\d+(?:-[\w.]+)?\b', "semver"),
]

def is_allowlisted(s: str, surrounding_text: str) -> bool:
    """Returns True if the string matches a known safe pattern.

    Runs patterns against the surrounding text (not just the bare token)
    because lookbehind patterns need context (e.g. 'commit ' before a SHA).
    """
    for pattern, label in ALLOWLIST_PATTERNS:
        match = re.search(pattern, surrounding_text)
        if match and match.group(0) in s:
            return True
    return False
```

This prevents git SHAs, UUIDs, and version strings from being masked, which would make reasoning records unreadable.

### 8.3 Stage 1: Known patterns

Regex patterns for known secret formats. This list is extensive by design - a false positive (masking a non-secret) is harmless, but a false negative (missing a real secret) is a security incident.

```python
PATTERNS = [
    # =========================================================================
    # AWS
    # =========================================================================
    (r'AKIA[0-9A-Z]{16}', "AWS_ACCESS_KEY_ID"),
    (r'ASIA[0-9A-Z]{16}', "AWS_TEMP_ACCESS_KEY"),        # STS temporary creds
    (r'(?i)aws_secret_access_key\s*[=:]\s*\S{40}', "AWS_SECRET_KEY"),
    (r'(?i)aws_session_token\s*[=:]\s*\S{100,}', "AWS_SESSION_TOKEN"),

    # =========================================================================
    # GCP
    # =========================================================================
    (r'AIza[0-9A-Za-z\-_]{35}', "GCP_API_KEY"),
    (r'"type"\s*:\s*"service_account"', "GCP_SERVICE_ACCOUNT_JSON"),
    (r'[0-9]+-[a-z0-9]{32}\.apps\.googleusercontent\.com', "GCP_OAUTH_CLIENT_ID"),
    (r'ya29\.[0-9A-Za-z\-_]+', "GCP_OAUTH_TOKEN"),

    # =========================================================================
    # Azure
    # =========================================================================
    (r'(?i)(DefaultEndpointsProtocol=https;AccountName=)\S+', "AZURE_STORAGE_CONNECTION"),
    (r'(?i)azure[_\-]?(?:storage|account)[_\-]?key\s*[=:]\s*[A-Za-z0-9+/=]{44,}', "AZURE_STORAGE_KEY"),
    (r'(?i)(?:client|tenant)_?(?:secret|id)\s*[=:]\s*[0-9a-f\-]{36}', "AZURE_AD_CREDENTIAL"),

    # =========================================================================
    # AI/LLM provider keys
    # =========================================================================
    (r'sk-ant-[a-zA-Z0-9\-]{80,}', "ANTHROPIC_API_KEY"),
    (r'sk-[a-zA-Z0-9]{20,}', "OPENAI_API_KEY"),
    (r'sk-proj-[a-zA-Z0-9\-]{40,}', "OPENAI_PROJECT_KEY"),
    (r'key-[a-zA-Z0-9]{32,}', "GENERIC_AI_API_KEY"),
    (r'(?i)(?:cohere|replicate|huggingface|hf)[_\-]?(?:api)?[_\-]?(?:key|token)\s*[=:]\s*\S{20,}', "AI_PROVIDER_KEY"),
    (r'hf_[a-zA-Z0-9]{34}', "HUGGINGFACE_TOKEN"),
    (r'r8_[a-zA-Z0-9]{20,}', "REPLICATE_TOKEN"),

    # =========================================================================
    # GitHub
    # =========================================================================
    (r'ghp_[a-zA-Z0-9]{36}', "GITHUB_PAT"),              # Personal access token
    (r'gho_[a-zA-Z0-9]{36}', "GITHUB_OAUTH_TOKEN"),
    (r'ghs_[a-zA-Z0-9]{36}', "GITHUB_APP_TOKEN"),         # App installation token
    (r'ghr_[a-zA-Z0-9]{36}', "GITHUB_REFRESH_TOKEN"),
    (r'github_pat_[a-zA-Z0-9_]{82}', "GITHUB_FINE_GRAINED_PAT"),

    # =========================================================================
    # GitLab
    # =========================================================================
    (r'glpat-[a-zA-Z0-9\-]{20,}', "GITLAB_PAT"),
    (r'glrt-[a-zA-Z0-9\-]{20,}', "GITLAB_RUNNER_TOKEN"),
    (r'gldt-[a-zA-Z0-9\-]{20,}', "GITLAB_DEPLOY_TOKEN"),
    (r'GR1348941[a-zA-Z0-9\-]{20,}', "GITLAB_RUNNER_REG_TOKEN"),

    # =========================================================================
    # Slack
    # =========================================================================
    (r'xoxb-[0-9]{10,}-[a-zA-Z0-9]{20,}', "SLACK_BOT_TOKEN"),
    (r'xoxp-[0-9]{10,}-[a-zA-Z0-9]{20,}', "SLACK_USER_TOKEN"),
    (r'xoxo-[0-9]{10,}-[a-zA-Z0-9]{20,}', "SLACK_OAUTH_TOKEN"),
    (r'xapp-[0-9]{1,}-[a-zA-Z0-9]{10,}-[0-9]{10,}-[a-zA-Z0-9]{30,}', "SLACK_APP_TOKEN"),
    (r'https://hooks\.slack\.com/services/T[a-zA-Z0-9]{8,}/B[a-zA-Z0-9]{8,}/[a-zA-Z0-9]{20,}', "SLACK_WEBHOOK"),

    # =========================================================================
    # Stripe
    # =========================================================================
    (r'sk_live_[a-zA-Z0-9]{24,}', "STRIPE_SECRET_KEY"),
    (r'sk_test_[a-zA-Z0-9]{24,}', "STRIPE_TEST_KEY"),
    (r'pk_live_[a-zA-Z0-9]{24,}', "STRIPE_PUBLISHABLE_KEY"),
    (r'rk_live_[a-zA-Z0-9]{24,}', "STRIPE_RESTRICTED_KEY"),
    (r'whsec_[a-zA-Z0-9]{32,}', "STRIPE_WEBHOOK_SECRET"),

    # =========================================================================
    # Database connection strings
    # =========================================================================
    (r'(?i)mongodb(\+srv)?:\/\/[^@\s]+@[^\s]+', "MONGODB_URI"),
    (r'(?i)postgres(ql)?:\/\/[^@\s]+@[^\s]+', "POSTGRES_URI"),
    (r'(?i)mysql:\/\/[^@\s]+@[^\s]+', "MYSQL_URI"),
    (r'(?i)redis(s)?:\/\/[^@\s]*:[^@\s]+@[^\s]+', "REDIS_URI"),
    (r'(?i)amqps?:\/\/[^@\s]+@[^\s]+', "AMQP_URI"),
    (r'(?i)Server=.+;Database=.+;User\s*Id=.+;Password=.+', "MSSQL_CONNECTION"),

    # =========================================================================
    # Other SaaS
    # =========================================================================
    (r'SG\.[a-zA-Z0-9\-]{22}\.[a-zA-Z0-9\-]{43}', "SENDGRID_API_KEY"),
    (r'(?i)twilio[_\-]?auth[_\-]?token\s*[=:]\s*[0-9a-f]{32}', "TWILIO_AUTH_TOKEN"),
    (r'sk_[a-f0-9]{32}', "MAILCHIMP_API_KEY"),            # ends with -us1 etc
    (r'(?i)(?:datadog|dd)[_\-]?api[_\-]?key\s*[=:]\s*[0-9a-f]{32}', "DATADOG_API_KEY"),
    (r'(?i)sentry[_\-]?dsn\s*[=:]\s*https:\/\/[a-f0-9]+@\S+', "SENTRY_DSN"),
    (r'sq0[a-z]{3}-[a-zA-Z0-9\-_]{22,}', "SQUARE_TOKEN"),
    (r'(?i)shopify[_\-]?(?:api|access)[_\-]?(?:key|token|secret)\s*[=:]\s*\S{20,}', "SHOPIFY_KEY"),
    (r'FLWSECK_TEST-[a-f0-9]{32}-X', "FLUTTERWAVE_SECRET"),
    (r'FLWPUBK_TEST-[a-f0-9]{32}-X', "FLUTTERWAVE_PUBLIC"),

    # =========================================================================
    # Passwords and generic secrets
    # =========================================================================
    (r'(?i)(password|passwd|pwd|pass)\s*[=:]\s*["\']?(\S{4,})["\']?', "PASSWORD"),
    (r'(?i)(secret|secret_key|secretkey)\s*[=:]\s*["\']?(\S{4,})["\']?', "SECRET"),
    (r'(?i)(token|auth_token|access_token|refresh_token)\s*[=:]\s*["\']?(\S{8,})["\']?', "TOKEN"),
    (r'(?i)(api_key|apikey|api-key)\s*[=:]\s*["\']?(\S{8,})["\']?', "API_KEY"),

    # =========================================================================
    # Private keys and certificates
    # =========================================================================
    (r'-----BEGIN (RSA )?PRIVATE KEY-----', "RSA_PRIVATE_KEY"),
    (r'-----BEGIN EC PRIVATE KEY-----', "EC_PRIVATE_KEY"),
    (r'-----BEGIN DSA PRIVATE KEY-----', "DSA_PRIVATE_KEY"),
    (r'-----BEGIN OPENSSH PRIVATE KEY-----', "OPENSSH_PRIVATE_KEY"),
    (r'-----BEGIN PGP PRIVATE KEY BLOCK-----', "PGP_PRIVATE_KEY"),
    (r'-----BEGIN CERTIFICATE-----', "CERTIFICATE"),
    (r'-----BEGIN ENCRYPTED PRIVATE KEY-----', "ENCRYPTED_PRIVATE_KEY"),

    # =========================================================================
    # JWTs and bearer tokens
    # =========================================================================
    (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}', "JWT"),
    (r'(?i)bearer\s+[a-zA-Z0-9\-._~+/]+=*', "BEARER_TOKEN"),
    (r'(?i)authorization\s*[=:]\s*["\']?Bearer\s+\S+', "AUTH_HEADER"),

    # =========================================================================
    # Webhook URLs (contain embedded secrets)
    # =========================================================================
    (r'https://discord\.com/api/webhooks/[0-9]+/[a-zA-Z0-9_\-]+', "DISCORD_WEBHOOK"),
    (r'https://[a-z0-9]+\.webhook\.office\.com/\S+', "TEAMS_WEBHOOK"),

    # =========================================================================
    # Generic hex/base64 secrets in sensitive context
    # =========================================================================
    (r'(?i)(secret|token|key|auth|bearer|credential)\s*[=:]\s*["\']?[0-9a-f]{32,}["\']?', "HEX_SECRET"),
    (r'(?i)(secret|token|key|auth|bearer|credential)\s*[=:]\s*["\']?[A-Za-z0-9+/]{40,}={0,2}["\']?', "BASE64_SECRET"),

    # =========================================================================
    # .env file patterns (common in agent reasoning that reads .env)
    # =========================================================================
    (r'(?i)^[A-Z_]*(SECRET|KEY|TOKEN|PASSWORD|CREDENTIAL|AUTH)[A-Z_]*\s*=\s*\S+', "ENV_SECRET"),

    # =========================================================================
    # IP addresses and internal hostnames (optional, reduces info leakage)
    # =========================================================================
    (r'(?:^|[^0-9])(?:10|172\.(?:1[6-9]|2[0-9]|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?:[^0-9]|$)', "INTERNAL_IP"),
]
```

**Pattern count:** 80+ patterns across 15 categories. The list is designed to be additive - users can extend it via `.g4a/config.json` `masking.additional_patterns` without modifying the core list.

**Pattern testing:** Every pattern in this list has a corresponding test case with real-world examples. The CI suite maintains a corpus of 500+ known secret formats and verifies 100% detection rate on every release.

**Replacement format:**

```
Original: sk-ant-abcdef123456789...
Masked:   [REDACTED:ANTHROPIC_KEY:sha256=a1b2c3]
```

The SHA-256 prefix (first 6 chars) allows detecting if the SAME secret appears in multiple records without revealing the secret itself. This helps answer "was the same API key exposed in multiple sessions?" without storing the key.

### 8.4 Stage 2: Entropy detection

For secrets that don't match known patterns:

```python
def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * log2(c / length) for c in freq.values())

def detect_alphabet(s: str) -> str:
    """Classify the character alphabet of a string."""
    chars = set(s.lower())
    if chars <= set("0123456789abcdef"):
        return "hex"          # max entropy: log2(16) = 4.0
    if chars <= set("0123456789abcdefghijklmnopqrstuvwxyz"):
        return "alphanum_lower"  # max entropy: log2(36) = 5.17
    if chars <= set(string.ascii_letters + string.digits + "+/="):
        return "base64"       # max entropy: log2(64) = 6.0
    return "mixed"            # max entropy: ~6.5+

# Thresholds per alphabet - set at ~85% of theoretical max
ENTROPY_THRESHOLDS = {
    "hex": 3.4,              # 85% of 4.0 - catches random hex keys
    "alphanum_lower": 4.4,   # 85% of 5.17
    "base64": 5.1,           # 85% of 6.0
    "mixed": 4.5,            # conservative default
}

def is_likely_secret(s: str) -> bool:
    """Detect high-entropy strings that are likely secrets."""
    if len(s) < 16:
        return False
    alphabet = detect_alphabet(s)
    threshold = ENTROPY_THRESHOLDS[alphabet]
    entropy = shannon_entropy(s)
    # Length >= 32: reduces false positives on short identifiers.
    # Shorter secrets (16-31 chars) are caught by Stage 1 (known patterns)
    # and Stage 3 (context-aware detection) instead.
    return entropy > threshold and len(s) >= 32
```

**Why alphabet-aware thresholds:** A hex string (charset 0-9a-f) has a theoretical maximum entropy of log2(16) = 4.0. A flat threshold of 4.5 would miss every hex-encoded secret. By measuring relative to the alphabet's maximum, we catch high-entropy strings in any encoding.

**Context required:** Entropy alone produces false positives. Stage 2 only triggers when the high-entropy string appears near a sensitive context keyword (secret, key, token, password, auth, credential, bearer, api_key). Additionally, the Stage 0 allowlist runs first - git SHAs and UUIDs are excluded before entropy detection.

### 8.5 Stage 3: Context-aware detection

Catches secrets that are low-entropy or short but appear in sensitive positions:

```python
SENSITIVE_KEYS = [
    "password", "passwd", "pwd", "secret", "token", "key", "auth",
    "credential", "api_key", "apikey", "access_key", "private_key",
    "client_secret", "bearer", "authorization"
]

def mask_key_value_pairs(text: str) -> str:
    """Mask values that appear next to sensitive variable names.

    Uses bounded capture groups [^"'\s,}]+ to avoid consuming
    JSON structural characters (quotes, commas, braces). Without this,
    masking {"password": "secret", "env": "prod"} would destroy the JSON.
    """
    for key in SENSITIVE_KEYS:
        # Match: PASSWORD=foo, password: "foo", "password": "foo"
        # Bounded: stops at quotes, commas, braces, brackets, whitespace
        pattern = rf'(?i)({key})\s*[=:]\s*["\']?([^"\x27\s,\}\]]+)["\']?'
        text = re.sub(pattern, rf'\1=[REDACTED:CONTEXT:{key.upper()}]', text)
    return text
```

### 8.6 Stage 4: Path sanitization

Absolute paths leak username and directory structure:

```python
def sanitize_paths(text: str, repo_root: str) -> str:
    """Replace absolute paths with repo-relative paths.

    Uses pathlib for correct prefix matching to avoid partial replacements.
    e.g., /Users/lokesh should NOT match /Users/lokeshbasu
    """
    repo = Path(repo_root).resolve()
    home = Path.home()

    def replace_path(match: re.Match) -> str:
        raw = match.group(0)
        p = Path(raw)
        try:
            return str(p.relative_to(repo))
        except ValueError:
            pass
        try:
            return "~/" + str(p.relative_to(home))
        except ValueError:
            pass
        return raw  # Not under repo or home - leave as-is

    # Match absolute paths (Unix and Windows)
    # Known limitation: does not match paths with spaces or Unicode characters.
    # Paths with spaces are uncommon in codebases; can be extended in POC if needed.
    text = re.sub(r'(?:/[\w./-]+|[A-Z]:\\[\w.\\-]+)', replace_path, text)
    return text
```

### 8.7 Recursive masking for structured data

Tool inputs and results are dicts/lists, not flat strings. The masker recurses:

```python
def mask_value(value, depth=0):
    if depth > 10:
        return "[REDACTED:DEPTH_LIMIT]"
    if isinstance(value, str):
        return mask_string(value)  # Runs all 5 stages
    if isinstance(value, dict):
        return {k: mask_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v, depth + 1) for v in value]
    return value  # int, float, bool, None - pass through
```

**Tool results are masked BEFORE truncation.** A tool result might contain a secret in the first 100 chars. The order is: mask all content first, then truncate to 2000 chars. Never truncate unmasked content.

### 8.8 Irreversibility guarantee

The masking pipeline is one-way. The original text is never stored, cached, or logged. The pipeline operates on the in-memory representation before any bytes touch disk. There is no "unmask" command. This is by design - if a secret is accidentally captured, it cannot be recovered from g4a data.

### 8.9 Timing considerations

Full constant-time masking is impractical (regex and entropy have input-dependent timing). The goal is: **no obviously input-dependent timing that leaks secret existence or structure.** The masking pipeline runs on the entire content regardless of whether secrets are found, so the presence/absence of secrets is not detectable from timing alone. Detailed secret length or position timing is not a practical attack vector for local file processing.

---

## 9. Query engine

### 9.1 Index structure

The search index maps terms to commit SHAs. Three index types:

```
file_index:     "src/payment.py"     -> [sha1, sha2, sha5]
function_index: "calculate_total"    -> [sha2, sha5]
text_index:     "decimal"            -> [sha2, sha3, sha5]
                "settlement"         -> [sha2, sha5]
                "precision"          -> [sha2]
```

**Index building:**
- `file_index`: populated from `files_changed` in every record
- `function_index`: populated by parsing diff hunks for function/class definitions
  - Python: `def foo`, `class Bar`
  - JavaScript/TypeScript: `function foo`, `class Bar`, `const foo =`
  - Go: `func foo`
  - Rust: `fn foo`, `struct Bar`
- `text_index`: populated by tokenizing `intent`, `exploration`, `alternatives`, `risks` fields. Stop words removed. Tokens lowercased.

**Index format:** Sorted array of `(term, sha_list)` tuples serialized as CBOR. Binary search for lookup. Typical size: 50-200 KB for 1,000 commits.

### 9.2 Query resolution

`g4a why <term>` resolves through multiple strategies:

```python
def resolve_query(term: str) -> List[CommitRecord]:
    results = []

    # 1. Exact file match
    if term in file_index:
        results.extend(load_records(file_index[term]))

    # 2. Exact function match
    if term in function_index:
        results.extend(load_records(function_index[term]))

    # 3. Prefix file match (e.g., "payment" matches "src/payment.py")
    for key in file_index:
        if term in key:
            results.extend(load_records(file_index[key]))

    # 4. Text search
    if term.lower() in text_index:
        results.extend(load_records(text_index[term.lower()]))

    # 5. Fuzzy text search (for typos)
    for key in text_index:
        if edit_distance(term.lower(), key) <= 2:
            results.extend(load_records(text_index[key]))

    # Deduplicate, rank, return top N
    return rank_and_deduplicate(results)
```

### 9.3 Ranking

```python
def rank(record: CommitRecord, query: str) -> float:
    score = 0.0

    # Recency: newer records score higher
    age_days = (now() - record.timestamp).days
    score += max(0, 100 - age_days)  # 100 points for today, 0 for 100+ days ago

    # Relevance: exact matches score higher
    if any(query in fc.path for fc in record.files_changed):
        score += 200  # Exact file match
    if query.lower() in (record.intent or "").lower():
        score += 100  # Mentioned in intent
    if query.lower() in (record.exploration or "").lower():
        score += 75   # Mentioned in exploration

    # Source quality (POC: captured > partial > metadata-only)
    if record.source == "captured":
        score += 50
    elif record.source == "partial":
        score += 25
    # metadata-only: +0

    # Confidence: higher confidence records are more useful
    if record.confidence:
        score += record.confidence * 30

    return score
```

---

## 10. CLI design

### 10.1 Command reference

```
g4a init                                Initialize g4a in current repo
g4a log [--limit N]                     Show recent commits with step counts and agent info
g4a log --timeline <commit>             Show full step-by-step trace between two commits
g4a show <commit>                       Show diff + reasoning summary side by side
g4a show <commit> --full                Show diff + full session trace for this commit
g4a why <term>                          Decision trail for a file, function, or keyword
g4a session <id>                        Browse full session: every prompt, dead end, correction
g4a web [--port PORT]                   Open visual report in browser
g4a status                              Show g4a health: pending captures, index stats
g4a backfill [--since COMMIT]           Re-process past commits
g4a reindex                             Rebuild search index from git notes
g4a config [key] [value]                Get/set configuration
g4a export <commit> [--format json|md]  Export reasoning as JSON or Markdown
g4a uninit                              Remove g4a hooks, notes config, and .g4a/ from repo
```

### 10.2 `g4a init` (the most important command)

Must complete in < 2 seconds with zero user input.

```
$ g4a init

  g4a initialized.

  Installed:
    .g4a/schema.json           reasoning record schema
    .g4a/config.json           g4a configuration
    .git/hooks/post-commit     reasoning capture hook
    .git/hooks/post-rewrite    rebase/amend SHA remapping
    .git/g4a/                  local index and state

  Configured:
    git fetch                  now pulls reasoning notes automatically
    git push                   now pushes reasoning notes alongside code

  Detected:
    Claude Code               direct transcript capture (best quality)
    Other commits              metadata-only (SHA, files, message)

  Next: use your AI coding agent normally. Reasoning is captured automatically.
  Run "g4a log" after your next commit to see it.

  Note: teammates need to run "g4a init" once to configure note fetching.
```

**What `g4a init` does:**

1. Creates `.g4a/` directory with `schema.json` and `config.json`
2. Creates `.git/g4a/` directory for local state (index, pending, errors)
   Seeds `.git/g4a/last_capture_mtime` with current time (prevents parsing
   months of old transcripts on first commit - use `g4a backfill` for history)
3. Installs `.git/hooks/post-commit` hook:
   - If no hook exists: writes the g4a hook
   - If a hook exists: appends g4a invocation to the end (preserves existing hooks)
   - Hook always exits 0, never breaks the hook chain
4. Installs `.git/hooks/post-rewrite` hook (for rebase/amend SHA remapping)
5. Generates a stable client ID (stored in `.git/g4a/client_id`)
6. Configures git notes fetch (all clients) and push (this client only):
   ```
   CLIENT_ID=<generated>
   git config --add remote.origin.fetch "+refs/notes/g4a-commits/*:refs/notes/g4a-commits/*"
   git config --add remote.origin.fetch "+refs/notes/g4a-sessions/*:refs/notes/g4a-sessions/*"
   git config --add remote.origin.push "HEAD"
   git config --add remote.origin.push "refs/notes/g4a-commits/$CLIENT_ID:refs/notes/g4a-commits/$CLIENT_ID"
   git config --add remote.origin.push "refs/notes/g4a-sessions/$CLIENT_ID:refs/notes/g4a-sessions/$CLIENT_ID"
   ```
   Fetch uses wildcard (all clients). Push is client-specific (no merge conflicts).
   The explicit `HEAD` entry ensures `git push` still pushes the current branch.
7. Auto-detects available agents (Claude Code transcripts)
8. Prints summary

**What `g4a init` does NOT do:**
- Ask any questions
- Require any API keys
- Create any accounts
- Make any network requests

**`g4a uninit` (clean removal):**

```
$ g4a uninit

  Removed:
    .g4a/                    schema and config
    .git/g4a/                local index and state
    .git/hooks/post-commit   g4a hook block removed (other hooks preserved)
    .git/hooks/post-rewrite  g4a hook block removed (other hooks preserved)
    git config               notes fetch/push refspecs removed

  NOT removed (reasoning data preserved in git notes):
    To also remove this client's reasoning from git, run:
      git update-ref -d refs/notes/g4a-commits/a3f8b2c1d4e5
      git update-ref -d refs/notes/g4a-sessions/a3f8b2c1d4e5
      git push origin :refs/notes/g4a-commits/a3f8b2c1d4e5
      git push origin :refs/notes/g4a-sessions/a3f8b2c1d4e5
```

Note: `g4a uninit` reads `.git/g4a/client_id` and prints the exact commands with the real client ID **before** deleting `.git/g4a/`. This ensures the user can copy the commands even after uninit completes.

**Record lifecycle (metadata-only to captured):**

When a capture retry succeeds, the metadata-only commit record is **replaced in-place** by writing a new git note to the same SHA. `g4a log` reflects the current state: if you see "metadata-only (capture pending retry)" now, after retry succeeds it shows "captured" with full reasoning. `g4a status` tracks the lifecycle:

```
pending retry -> retry succeeds -> metadata-only note overwritten with captured note
pending retry -> 3 failures    -> moved to .git/g4a/permanent_failures.json
                                  g4a status shows it, g4a backfill retries it
```

### 10.3 `g4a log` output

The default view shows commits with step counts and agent info:

```
$ g4a log

  a1b2c3d  2 hours ago  claude-code (120 steps, 14 prompts, 1 dead end)
  refactor: Update payment calculation to use Decimal
  Intent: Switch from float to Decimal for currency precision.
          Batch settlements accumulate floating-point errors.
  Confidence: 0.85  |  Files: 8  |  Risks: 1 flagged
  ──────────────────────────────────────────────────────

  e4f5g6h  yesterday  2 agents (claude-code: 85 steps, claude-code: 12 steps)
  feat: Add billing dashboard + update docs
  Intent: New billing dashboard with real-time settlement data.
  Confidence: 0.78  |  Files: 11  |  Risks: 0
  ──────────────────────────────────────────────────────

  i7j8k9l  3 days ago  human (0 steps)
  chore: Update dependencies
  (no agent reasoning captured)
  Files: 2
```

Key UX decisions:
- Step count tells you how much exploration went into a commit (120 steps vs 0 steps)
- Multi-agent commits show each agent's contribution
- Human-only commits show "0 steps" - the minimum timeline entry
- Dead end count draws attention to non-obvious reasoning paths

### 10.4 `g4a log --timeline` output

Expanded view showing every step between commits:

```
$ g4a log --timeline a1b2c3d

  ── Commit e4f5g6h (yesterday) ────────────────────────────────
  |
  |  [claude-code session 542e] 85 steps
  |  |
  |  |  09:30:12  PROMPT   "Refactor payment processing to use Decimal"
  |  |  09:30:15  THINKING "I need to understand the current payment system..."
  |  |  09:30:16  READ     checkout.py
  |  |  09:30:18  READ     billing.py
  |  |  09:30:20  READ     refunds.py
  |  |  09:30:22  READ     settlement.py
  |  |  ...       (12 more reads)
  |  |
  |  |  09:52:03  THINKING "Let me try using integer cents..."
  |  |  09:52:10  EDIT     checkout.py (integer cents approach)
  |  |  09:55:00  BASH     python -m pytest tests/
  |  |  09:55:06  ERROR    test_api_response FAILED
  |  |  09:55:08  THINKING "Integer cents won't work, APIs expect decimal..."
  |  |            ^^^ DEAD END (steps 46-68, abandoned after test failure)
  |  |
  |  |  10:01:30  THINKING "Pivot to Decimal everywhere..."
  |  |  10:01:35  EDIT     checkout.py (Decimal approach)
  |  |  10:02:10  EDIT     billing.py
  |  |  ...       (6 more edits)
  |  |  10:12:00  BASH     python -m pytest tests/
  |  |  10:12:08  PASS     all tests
  |  |  10:15:01  COMMIT   a1b2c3d
  |  |
  |  [claude-code session b2b3] 12 steps
  |  |
  |  |  10:00:00  PROMPT   "Update README with new payment API docs"
  |  |  10:00:05  READ     README.md
  |  |  10:00:10  EDIT     README.md
  |  |  ...
  |  |  10:05:00  (no commit - changes staged but not committed separately)
  |
  ── Commit a1b2c3d (2 hours ago) ──────────────────────────────
```

### 10.5 `g4a why` output

```
$ g4a why process_payment

  Decision trail for "process_payment"
  Found in 3 commits, 4 agent sessions, 267 total steps

  ── a1b2c3d (2 hours ago, 120 steps, confidence: 0.85) ──────────
  Agents: claude-code (session 542e)
  Intent: Switch from float to Decimal for currency precision.
  Exploration: Read 6 files, found 14 call sites, ran 10K simulated transactions.
  Dead ends:
    - Integer cents approach (steps 46-68): abandoned after API test failures
  Alternatives:
    1. Keep float + round at end     REJECTED: error accumulates in batches
    2. Integer cents                  REJECTED: would touch 23 files (tried and failed)
    3. Decimal everywhere             CHOSEN: cleanest path, 8 files
  Risks:
    - batch_settlement_job.py:47 CSV export formatting  [LOW confidence: 0.6]
  Run "g4a log --timeline a1b2c3d" for the full 120-step trace.

  ── f2g3h4i (2 weeks ago, 34 steps, confidence: 0.91) ──────────
  Agents: claude-code (session 8a9b)
  Intent: Add retry logic to process_payment for transient gateway errors.
  Dead ends: none
  Alternatives:
    1. Exponential backoff            CHOSEN
    2. Circuit breaker                REJECTED: overkill
  Risks: none flagged

  ── b5c6d7e (1 month ago, 0 steps) ──────────────────────────────
  Human commit (no agent reasoning captured)
  Initial implementation of process_payment function.
```

### 10.6 `g4a show` output

```
$ g4a show a1b2c3d

  ┌─ DIFF ──────────────────────┐  ┌─ REASONING ─────────────────────┐
  │ checkout.py                 │  │ Intent: Switch from float to    │
  │ @@ -45,7 +45,7 @@          │  │ Decimal for currency precision  │
  │ -  total = float(sum)       │  │                                 │
  │ +  total = Decimal(sum)     │  │ 120 steps | 14 prompts          │
  │                             │  │ 1 dead end | Confidence: 0.85   │
  │ billing.py                  │  │                                 │
  │ @@ -12,5 +12,5 @@          │  │ Alternatives considered:        │
  │ -  amount = float(price)    │  │ 1. float + round -> rejected    │
  │ +  amount = Decimal(price)  │  │ 2. integer cents -> tried,      │
  │                             │  │    failed (steps 46-68)         │
  │ settlement.py               │  │                                 │
  │ @@ -47,3 +47,3 @@          │  │ Risk flagged:                   │
  │ -  f"{total:.2f}"           │  │ CSV export on line 47 may       │
  │ +  f"{total:.2f}"           │  │ truncate Decimal [conf: 0.6]    │
  └─────────────────────────────┘  └─────────────────────────────────┘

  Agents: claude-code (session 542e, steps 0-120)
  Run "g4a log --timeline a1b2c3d" for the full step-by-step trace.
```

---

## 11. Web reporter

`g4a web` generates a static HTML report and opens it in the browser.

### 11.1 Features

- **Timeline view:** Visual DAG showing commits as nodes, with expandable step traces between them. Parallel agent branches shown as parallel tracks.
- **Per-commit detail:** Diff + reasoning side by side, with "expand timeline" to see all steps
- **Step-level drill-down:** Click any step to see the full content (thinking text, tool call details, error messages)
- **Multi-agent view:** When 2+ agents worked between commits, show them as parallel swim lanes with timestamps aligned
- **Dead end highlighting:** Steps that were part of abandoned approaches are visually marked (dimmed or strikethrough) so reviewers can see what was tried and rejected
- **Phase grouping:** Steps are auto-grouped into phases (exploration, implementation, testing, debugging) with collapsible sections
- **Search:** Full-text search across all reasoning records and session events
- **Filter by:** agent, confidence range, date range, step count, has-dead-ends
- **Risk dashboard:** All flagged risks across commits
- **Statistics:** Commits per day, average steps per commit, dead end frequency, agent distribution

### 11.2 Implementation

`g4a web` is a two-step process: build then open.

**Build step** (runs `git notes` commands to extract data):
1. Read all commit notes across all client namespaces (`refs/notes/g4a-commits/*`)
2. Deserialize each commit record
3. Embed as a JSON blob in the HTML

**View step:**
- **Default (`g4a web`):** Generates a single static HTML file with all commit records embedded. Session traces are NOT included (too large). Opens with `python -m webbrowser`. No server needed. Dead simple.
- **Full mode (`g4a web --serve`):** Starts a local HTTP server that serves commit records statically and loads session traces on-demand via `fetch()` (the server calls `git notes show` behind the scenes). This enables lazy session loading and the full timeline drill-down.

**Tech:**
- Vanilla JavaScript, no framework, no build step
- Commit records in static mode, session traces only in serve mode

### 11.3 Size budget

The HTML report with commit records for 1,000 commits should be < 2 MB. Session traces are loaded on-demand. For larger repos, the timeline view paginates.

---

## 12. Latency budget

### 12.1 Capture path (must be invisible)

| Step | Target | Method |
|------|--------|--------|
| Post-commit hook fires | 0ms | Git calls the hook |
| Hook reads commit SHA | 5ms | `git rev-parse HEAD` |
| Hook forks background process | 10ms | `os.fork()` + `os.setsid()` |
| Hook returns to git | **< 20ms total** | Git continues immediately |
| Background: detect agent | 50ms | File stat on ~/.claude/projects/ |
| Background: parse transcript (Claude Code) | 200-500ms | Stream JSONL, extract window |
| Background: mask secrets | 100-200ms | Regex + entropy scan |
| Background: serialize | 30-50ms | CBOR encoding |
| Background: write git notes | 30ms | `git notes add` + `git hash-object` |
| Background: update index | 50ms | Append to index file |
| **Total hook latency** | **< 20ms** | Developer never waits |
| **Total background time** | **< 1s** | Invisible (local file parsing only in POC) |

### 12.2 Query path (must feel instant)

| Step | Target | Method |
|------|--------|--------|
| CLI startup | 30ms | Lazy imports, no heavy init |
| Load index | 20ms | Memory-map the index file |
| Search index | 5ms | Binary search on sorted keys |
| Load matching records | 50-200ms | Read 5-20 git note blobs, deserialize CBOR |
| Rank results | 5ms | Score and sort |
| Render output | 20ms | Rich terminal formatting |
| **Total query time** | **< 300ms** | Feels instant |

### 12.3 How we keep the CLI fast

- **Lazy imports:** `import cbor2` only when actually needed, not at CLI startup
- **No global init:** CLI parses args and dispatches immediately, no database connections or config validation at startup
- **Memory-mapped index:** The search index is memory-mapped, so the OS handles caching. Second query is near-instant.
- **Parallel deserialization:** When loading multiple records, read git note blobs in parallel using `concurrent.futures.ThreadPoolExecutor`

---

## 13. Security model

### 13.1 Threat model

| Threat | Mitigation |
|--------|-----------|
| Secret leaked into reasoning record | Mandatory masking pipeline, no bypass |
| Git notes pushed to public repo | Secrets already masked before write. Binary CBOR format prevents casual reading. Notes are not visible in GitHub web UI. |
| Reasoning reveals proprietary logic | Same as pushing source code - reasoning notes visibility matches repo visibility |
| Code sent to external API | POC makes zero network calls (local transcript parsing only). Future inference adapters will require explicit opt-in. |
| Malicious CBOR payload in git notes | CBOR deserialization with strict mode, size limits, no code execution |
| Hook script injection | Hook is a static shell script, no dynamic content from .g4a/ files |
| Denial of service via large transcript | Transcript parsing has a 50 MB size limit, timeout of 30 seconds |

### 13.2 Data flow security

```
Agent reasoning (untrusted, may contain secrets)
    |
    v
[In-memory only - never touches disk in raw form]
    |
    v
Secret masking pipeline (allowlist + 4 stages)
    |
    v
[Verified clean - all patterns checked]
    |
    v
CBOR serialization (structured, typed)
    |
    v
CBOR binary format (not human-readable in raw form)
    |
    v
Written to git notes (never touches working tree)
```

**Key invariant:** Raw reasoning text NEVER exists on disk. It exists only in memory between capture and masking. If the process crashes during capture, nothing is written. There is no temp file, no log, no cache of unmasked content.

### 13.3 Network security (POC)

The Claude Code adapter makes **zero network calls**. It reads local JSONL transcript files from `~/.claude/projects/`. All processing is local. No data leaves the machine during capture.

Future adapters that use LLM inference will require explicit opt-in via `G4A_API_KEY` and will only send data already visible in git (diffs, commit messages). This will be documented extensively when those adapters ship.

### 13.4 Deserialization safety

CBOR deserialization uses strict mode:

```python
def safe_deserialize(data: bytes) -> dict:
    if len(data) > MAX_RECORD_SIZE:  # 10 MB
        raise G4AError("Record exceeds maximum size")

    # cbor2 strict mode: no tags, no indefinite-length, no shared refs
    result = cbor2.loads(data)

    # Type validation
    if not isinstance(result, dict):
        raise G4AError("Record must be a dictionary")

    # Schema validation
    validate_against_schema(result)

    return result
```

---

## 14. Error handling

### 14.1 Core invariant

**g4a never breaks the developer's workflow.** The entire capture path runs in a background process that the git hook fork-and-forgets. No error in g4a - no matter how severe - can block a commit, slow down a push, or corrupt the repo. If g4a crashes, the developer doesn't even notice. They find out later when `g4a log` shows a gap.

### 14.2 The retry queue

Instead of dropping failed captures silently, g4a writes them to a retry queue. The next successful capture run picks up pending retries automatically. No user action needed.

**Retry queue file:** `.git/g4a/pending.json`

```json
[
  {
    "commit_sha": "a1b2c3d",
    "failed_at": "2026-03-22T10:15:03Z",
    "error": "Transcript parse failed: unexpected message type 'custom_event' at line 847",
    "stage": "capture",
    "retry_count": 0,
    "next_retry_after": "2026-03-22T10:15:03Z"
  }
]
```

**Retry logic:**

```python
def run_capture(commit_sha: str):
    try:
        # 1. Attempt capture for this commit
        record = capture(commit_sha)
        write_commit_record(record)

        # 2. After success, drain the retry queue
        drain_pending_retries()

    except Exception as e:
        # Never propagate - log and queue for retry
        add_to_retry_queue(commit_sha, error=str(e), stage="capture")
        log_error(commit_sha, e)


def drain_pending_retries():
    """Process pending retries after a successful capture."""
    pending = load_pending_queue()
    still_pending = []

    for item in pending:
        if item["retry_count"] >= MAX_RETRIES:  # Max 3 retries
            # Move to permanent failures log, stop retrying
            log_permanent_failure(item)
            continue

        if now() < item["next_retry_after"]:
            still_pending.append(item)
            continue

        try:
            record = capture(item["commit_sha"])
            write_commit_record(record)
            # Success - don't re-add to queue
        except Exception:
            item["retry_count"] += 1
            # Exponential backoff: 1 min, 5 min, 30 min
            backoff = [60, 300, 1800][min(item["retry_count"] - 1, 2)]
            item["next_retry_after"] = (now() + timedelta(seconds=backoff)).isoformat()
            still_pending.append(item)

    save_pending_queue(still_pending)
```

**Key behaviors:**
- Retries piggyback on the next commit's capture run (no background daemon, no cron)
- Exponential backoff: 1 minute, 5 minutes, 30 minutes
- Max 3 retries, then the commit moves to permanent failures
- `g4a backfill <sha>` manually retries any commit (ignores retry count)
- `g4a status` shows pending retries and permanent failures

### 14.3 Error boundaries per stage

Every stage in the capture pipeline has its own try/catch. A failure in one stage triggers the appropriate fallback without killing the entire pipeline:

```python
def capture(commit_sha: str) -> CommitRecord:
    record = CommitRecord(commit_sha=commit_sha, timestamp=now())
    record.parent_sha = git_parent_sha(commit_sha)
    record.files_changed = git_files_changed(commit_sha)
    record.commit_message = git_commit_message(commit_sha)

    # Stage 1: Find contributing sessions
    # Step 1a: Find the PRIMARY transcript (contains this commit's SHA)
    #          Uses settle period (up to 2s polling) for slow flush
    # Step 1b: Find ALL transcripts modified since watermark
    #          (catches parallel agent sessions that didn't commit)
    try:
        primary = find_transcript_with_settle(commit_sha, repo_root)
        if primary:
            # Primary found: also grab parallel sessions modified since watermark
            all_transcripts = find_all_contributing_transcripts(commit_sha, repo_root)
            # Preserve order: primary first, then others (deduplicated)
            seen = {primary}
            session_paths = [primary] + [t for t in all_transcripts if t not in seen]
        else:
            # No transcript contains this commit's SHA.
            # Do NOT use all_transcripts - they may be unrelated sessions
            # that didn't produce this commit.
            session_paths = []
    except Exception as e:
        raise CaptureError(f"session lookup: {e}")

    if not session_paths:
        # No transcripts found at all
        record.source = "metadata-only"
        record.contributing_sessions = []
        record.total_steps = 0
        record.total_agent_sessions = 0
        # Decide: slow flush or human commit?
        if should_retry_capture(repo_root):
            add_to_retry_queue(commit_sha, error="no transcript after settle",
                               stage="capture")
        update_capture_watermark()  # Even metadata-only updates the watermark
        return mask_and_write(record)

    # Stage 2: Capture sessions and build commit record
    try:
        record.contributing_sessions = []
        for session_path in session_paths:
            session = capture_session(session_path, session_path.stem)
            start, end = find_commit_range(session, commit_sha)
            # Determine anchor SHA: first commit in this session,
            # or current commit if this is the first
            # get_or_create_session_anchor:
            # Look up session_id in .git/g4a/index.db.
            # If found, return the stored anchor_sha (first commit of this session).
            # If not found, this is the first commit - use current commit_sha as anchor.
            anchor = get_or_create_session_anchor(session.session_id, commit_sha)
            link = SessionLink(
                session_id=session.session_id,
                anchor_sha=anchor,
                agent=session.agent,
                msg_start=start,
                msg_end=end,
                step_count=end - start + 1
            )
            record.contributing_sessions.append(link)
            write_session_note(anchor, session)  # git notes add on anchor SHA

        # Synthesize reasoning summary from all contributing sessions
        reasoning = synthesize_from_all_sessions(
            record.contributing_sessions, commit_sha
        )
        record.intent = reasoning.intent
        record.exploration = reasoning.exploration
        record.alternatives = reasoning.alternatives
        record.risks = reasoning.risks
        record.confidence = reasoning.confidence
        record.files_read = reasoning.files_read
        record.tools_used = reasoning.tools_used
        record.tests_run = reasoning.tests_run
        record.dead_ends = reasoning.dead_ends
        record.total_steps = sum(s.step_count for s in record.contributing_sessions)
        record.total_user_prompts = reasoning.total_user_prompts
        record.total_thinking_blocks = reasoning.total_thinking_blocks
        record.total_agent_sessions = len(record.contributing_sessions)
        record.agents = list(set(s.agent for s in record.contributing_sessions))
        # primary_agent: the agent whose transcript contained the commit SHA
        record.primary_agent = (record.contributing_sessions[0].agent
                                if record.contributing_sessions and primary
                                else None)
        record.source = "captured"

    except Exception as e:
        # Session exists but parsing failed
        # Write metadata-only + queue retry for full parse
        log_error(commit_sha, e, stage="parse")
        record.source = "metadata-only"
        record.contributing_sessions = []
        record.total_steps = 0
        record.total_agent_sessions = 0
        add_to_retry_queue(commit_sha, error=str(e), stage="parse")
        # Fall through to masking - still write the metadata record

    # Stage 3: Mask secrets (NEVER skip this)
    try:
        record = mask_secrets(record)
    except Exception as e:
        # Masking failure is critical - discard entire record
        raise CaptureError(f"CRITICAL masking failure, record discarded: {e}")

    # Stage 4: Serialize and write
    try:
        write_commit_record(record)
    except Exception as e:
        raise CaptureError(f"write failed: {e}")

    # Stage 5: Update watermark
    # Written after every successful capture (full or metadata-only)
    # so the next commit only scans transcripts modified after this point.
    update_capture_watermark()  # Writes time.time() to .git/g4a/last_capture_mtime

    return record
```

**The masking stage is the only one that kills the record entirely.** Every other stage degrades gracefully: transcript not found -> metadata-only, parse fails -> metadata-only + retry, write fails -> retry. But if masking fails, the record is discarded completely. Writing unmasked data is never acceptable.

### 14.4 What the developer sees

**During normal operation:** Nothing. g4a is invisible. The hook returns in < 50ms, background processing happens silently.

**When something fails:**

```
$ g4a log

  a1b2c3d  2 hours ago  captured via claude-code
  refactor: Update payment calculation to use Decimal
  ...

  e4f5g6h  yesterday  metadata-only (capture pending retry)
  fix: Handle null user in auth middleware
  Intent: (capture failed, retry 1 of 3 scheduled)
  ...
```

```
$ g4a status

  g4a status
  Records: 47 captured, 2 metadata-only, 1 pending retry
  Index: 49 commits indexed, 156 files tracked
  Pending:
    e4f5g6h  retry 1 of 3  next retry: in 4 minutes
             error: transcript parse failed (unexpected EOF)
  Permanent failures: 0
  Disk usage: 1.2 MB (git notes + local index)
```

**Recovery is always automatic.** The developer never needs to run a command to fix a failed capture. The next commit triggers retry. If they want to force it, `g4a backfill e4f5g6h` retries immediately.

### 14.5 Error log

`.git/g4a/errors.log` captures all background processing errors. This file is local-only (inside `.git/`, never committed or pushed) so it cannot leak PII to the remote:

```
2026-03-22T10:15:03Z ERROR capture stage=parse commit=a1b2c3d
  session=542e0ff9-b7aa-490f-ab02-f6b1f6952727
  error: Unexpected message type "custom_event" at line 847
  action: wrote metadata-only record, queued retry (1 of 3)
  next_retry: 2026-03-22T10:16:03Z

2026-03-22T10:16:03Z INFO  retry stage=parse commit=a1b2c3d
  action: retry succeeded, upgraded metadata-only -> captured

2026-03-22T11:30:15Z WARN  masker stage=entropy commit=f4e5g6h
  location: intent field, position 234-270
  action: masked conservatively (may be a false positive)
  masked_as: [REDACTED:ENTROPY:sha256=f4e5d6]
```

The log is append-only, capped at 1 MB (oldest entries rotated out). It is included in `.gitignore` by default since it may contain file paths and error details that vary per machine.

### 14.6 Graceful degradation chain

```
Claude Code transcript found, parse succeeds
  -> Full CommitRecord with all fields (best)

Claude Code transcript found, parse fails
  -> Metadata-only record written immediately
  -> Full capture queued for retry (up to 3 attempts)
  -> If retry succeeds, metadata-only record is replaced with full record

No Claude Code transcript found (primary is None after settle)
  -> Metadata-only record (commit SHA, files changed, message)
  -> If Claude recently active (slow flush): queue retry
  -> If Claude NOT recently active (human commit): no retry, no error

Masking pipeline fails
  -> Record discarded entirely (security-critical)
  -> Error logged, queued for retry

Write to git notes fails (disk full, permissions, git lock, etc.)
  -> Error logged, queued for retry
  -> Commit succeeds normally

Everything fails including error logging
  -> Commit succeeds normally
  -> Developer sees gap in g4a log
  -> g4a backfill <sha> can always retry manually
```

---

## 15. Testing strategy

### 15.1 Unit tests

| Component | Test focus |
|-----------|-----------|
| Secret masker | Every pattern in PATTERNS list, entropy thresholds, false positive rates |
| CBOR codec | Round-trip serialization, schema validation, corrupt data handling |
| Claude Code adapter | Parse real transcript fixtures, session capture, commit range detection, multi-commit sessions |
| Query engine | Index building, search accuracy, ranking correctness |
| Agent detector | Claude Code detection, fallback to metadata-only |

### 15.2 Integration tests

- **End-to-end capture:** Create a git repo, make commits with mock Claude Code transcripts, verify git notes are written correctly
- **End-to-end query:** Populate git notes with fixture records, run g4a why/log/show, verify output
- **Hook installation:** Verify g4a init installs hooks correctly, preserves existing hooks
- **Secret masking:** Feed known secrets through the full pipeline, verify they never appear in output

### 15.3 Security tests

- **Secret corpus:** Maintain a test corpus of 500+ real-world secret patterns. Every CI run verifies 100% detection rate.
- **Fuzzing:** Fuzz the CBOR deserializer with random bytes, verify no crashes or code execution
- **Timing:** Verify masking pipeline does not exhibit obviously input-dependent timing that would leak secret presence (e.g., no short-circuit on first match - pipeline always runs all stages on all content)

### 15.4 Performance benchmarks

- **Capture latency:** Hook must return in < 50ms (measured in CI)
- **Query latency:** `g4a why` must return in < 300ms for 1,000 records (measured in CI)
- **Index size:** Must be < 500 KB for 1,000 commits
- **Record size:** Must be < 100 KB per commit (uncompressed CBOR, before git's native compression)

---

## 16. Packaging and distribution

### 16.1 PyPI package

```
Package name: g4a
Entry point:  g4a (console script)
Python:       >= 3.9
License:      CC-BY-4.0
```

```
pip install g4a
```

Single command. No extras, no optional dependencies for core functionality. `cbor2` includes pre-built wheels for all major platforms. No C compiler needed. No zstd dependency - git handles compression natively.

### 16.2 Homebrew

```
brew install g4a
```

Ships as a day-one installation option alongside pip. The Homebrew formula:

- Hosted in a tap: `brew tap lcbasu/g4a && brew install g4a` (short form: `brew install lcbasu/g4a/g4a`)
- Once adoption grows, submit to homebrew-core for `brew install g4a` directly
- The formula installs the same Python package under the hood via a virtualenv managed by Homebrew
- Bundles Python 3.12+ as a dependency so users don't need to manage Python themselves
- Pins the `cbor2` C extension to the Homebrew-compiled version for native performance

**Homebrew formula (`Formula/g4a.rb`):**

```ruby
class G4a < Formula
  include Language::Python::Virtualenv

  desc "The reasoning layer for AI-written code"
  homepage "https://www.git4aiagents.com"
  url "https://files.pythonhosted.org/packages/source/g/g4a/g4a-0.1.0.tar.gz"
  sha256 "TBD"
  license "CC-BY-4.0"

  depends_on "python@3.12"

  resource "cbor2" do
    url "https://files.pythonhosted.org/packages/source/c/cbor2/cbor2-5.6.5.tar.gz"
    sha256 "TBD"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/source/c/click/click-8.1.8.tar.gz"
    sha256 "TBD"
  end

  resource "rich" do
    url "https://files.pythonhosted.org/packages/source/r/rich/rich-13.9.4.tar.gz"
    sha256 "TBD"
  end

  resource "jinja2" do
    url "https://files.pythonhosted.org/packages/source/j/Jinja2/jinja2-3.1.5.tar.gz"
    sha256 "TBD"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"g4a", "--version"
  end
end
```

**Why both pip and brew:**
- `pip install` is universal and works everywhere Python runs
- `brew install` is the default for macOS developers (g4a's primary audience), avoids Python version conflicts, and feels native
- Both produce the exact same `g4a` binary with identical behavior

### 16.3 What ships in the package

```
g4a/                    Core Python package
g4a/hooks/post-commit   Shell script template for capture hook
g4a/hooks/post-rewrite  Shell script template for rebase/amend remapping
g4a/web/templates/      HTML templates for web reporter
g4a/security/patterns/  Secret pattern database
```

---

## 17. Future extensions

Not in the POC, but designed to be additive:

### 17.0 Schema migration

Schema evolution is **additive only** - new fields are added, old fields are never removed or renamed. Old readers ignore unknown fields. There is no migration needed when reading old records with a new version of g4a.

If a future version needs to restructure records (breaking change), `g4a migrate` will read old records, transform them, and write new records. This is a manual command, never automatic. The old schema version in the record header tells the reader which parser to use.

### 17.1 Phase 2: GitHub/GitLab integration

- **PR comment bot:** Automatically post reasoning summary as a PR comment
- **CI check:** Fail the build if a commit has no reasoning record (configurable)
- **GitHub Action:** `uses: g4a/capture@v1` in CI workflow

### 17.2 Phase 3: Agent-to-agent context

- **g4a read API:** Agents call `g4a why <file>` before modifying code
- **CLAUDE.md integration:** Auto-generate CLAUDE.md sections from .g4a/ records
- **MCP server:** Expose reasoning records as an MCP tool for any agent

### 17.3 Phase 4: Team features

- **g4a dashboard:** Web dashboard for team-wide reasoning visibility
- **Confidence alerts:** Notify when low-confidence changes are merged
- **Reasoning diff:** Show how reasoning changed between two versions of the same file

### 17.4 Phase 5: Trust scoring

- **Agent scorecards:** Track per-agent accuracy over time (did flagged risks materialize?)
- **Auto-approve rules:** High-trust agents with high confidence can bypass review for specific file patterns
- **Audit trail:** Complete history of who (human or agent) approved what, with reasoning

---

## Appendix A: Configuration reference

`.g4a/config.json` (committed to repo):

```json
{
  "version": "1.0",
  "capture": {
    "enabled": true,
    "background_timeout_seconds": 30,
    "max_transcript_size_mb": 50
  },
  "masking": {
    "additional_patterns": [],
    "mask_paths": true
  },
  "index": {
    "languages": ["python", "javascript", "typescript", "go", "rust"],
    "max_context_lines": 200
  }
}
```

Environment variables (never committed):

```
G4A_DISABLE          Set to "1" to temporarily disable capture
G4A_DEBUG            Set to "1" for verbose logging to .git/g4a/errors.log
```

Reserved for future adapters (not used in POC):

```
G4A_API_KEY          API key for LLM inference adapters (post-POC)
G4A_API_BASE         Custom API base URL (post-POC)
G4A_MODEL            Model for inference (post-POC)
```

---

## Appendix B: Hook scripts

### B.1 Post-commit hook (`.git/hooks/post-commit`)

```bash
#!/bin/sh
# g4a reasoning capture hook
# Returns immediately. All work happens in a background process.
# Reasoning is stored as git notes, never touching the working tree.

# Skip if g4a is disabled (useful during mass rebase)
[ "$G4A_DISABLE" = "1" ] && exit 0

# Skip during amend/rebase - post-rewrite handles note remapping
# post-commit fires BEFORE post-rewrite during amend, causing a race.
# Let post-rewrite handle it instead.
if [ "$GIT_REFLOG_ACTION" = "commit (amend)" ] || \
   echo "$GIT_REFLOG_ACTION" | grep -q "rebase"; then
  exit 0
fi

# Get the commit SHA and repo root
SHA=$(git rev-parse HEAD 2>/dev/null) || exit 0
REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

# Check g4a is initialized
[ -f "$REPO/.g4a/config.json" ] || exit 0

# Fork to background and return immediately
(
  exec </dev/null >/dev/null 2>/dev/null

  # Acquire lock (serializes concurrent captures)
  python3 -m g4a capture "$SHA" --repo "$REPO" \
    2>> "$REPO/.git/g4a/errors.log" || true
) &

# Return immediately - never block the commit
exit 0
```

**Hook integration with existing hooks:** If a post-commit hook already exists, `g4a init` appends the g4a block (wrapped in a comment marker) to the end. The existing hook runs first, then g4a. g4a always exits 0 regardless of errors, so it never stops the hook chain.

### B.2 Post-rewrite hook (`.git/hooks/post-rewrite`)

```bash
#!/bin/sh
# g4a note remapping hook
# Fires after git commit --amend and git rebase
# Remaps reasoning notes from old SHAs to new SHAs

[ "$G4A_DISABLE" = "1" ] && exit 0

REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$REPO/.g4a/config.json" ] || exit 0

while read old_sha new_sha _rest; do
  # Scan ALL client namespaces - developer may rebase commits
  # whose reasoning was created on a different machine
  for ref in $(git for-each-ref --format='%(refname)' refs/notes/g4a-commits/); do
    if git notes --ref="$ref" show "$old_sha" >/dev/null 2>&1; then
      git notes --ref="$ref" copy "$old_sha" "$new_sha" 2>/dev/null
      git notes --ref="$ref" remove "$old_sha" 2>/dev/null
    fi
  done
  for ref in $(git for-each-ref --format='%(refname)' refs/notes/g4a-sessions/); do
    if git notes --ref="$ref" show "$old_sha" >/dev/null 2>&1; then
      git notes --ref="$ref" copy "$old_sha" "$new_sha" 2>/dev/null
      git notes --ref="$ref" remove "$old_sha" 2>/dev/null
    fi
  done
done

# Rebuild local index (anchor SHAs may have changed)
cd "$REPO" && python3 -m g4a reindex --quiet 2>/dev/null &

exit 0
```

**Note:** Push configuration uses `remote.origin.push` refspecs (Section 6.3), not a pre-push hook. This avoids the concurrent push race condition and TTY/auth issues that a background `git push &` would cause.

---

## Appendix C: Record examples (CBOR, shown as JSON)

### C.1 Commit record (git note on a1b2c3d, ref: g4a-commits/&lt;client_id&gt;)

```json
{
  "version": "1.0",
  "commit_sha": "a1b2c3d4e5f6",
  "parent_sha": "z9y8x7w6v5u4",
  "timestamp": "2026-03-22T10:15:03Z",

  "contributing_sessions": [
    {
      "session_id": "542e0ff9",
      "anchor_sha": "a1b2c3d4e5f6",
      "agent": "claude-code",
      "msg_start": 0,
      "msg_end": 120,
      "step_count": 121
    }
  ],

  "source": "captured",
  "agents": ["claude-code"],
  "primary_agent": "claude-code",

  "files_changed": [
    {"path": "checkout.py", "lines_added": 5, "lines_removed": 5, "change_type": "modified"},
    {"path": "billing.py", "lines_added": 3, "lines_removed": 3, "change_type": "modified"},
    {"path": "refunds.py", "lines_added": 4, "lines_removed": 4, "change_type": "modified"},
    {"path": "settlement.py", "lines_added": 8, "lines_removed": 6, "change_type": "modified"},
    {"path": "tests/test_payment.py", "lines_added": 25, "lines_removed": 12, "change_type": "modified"}
  ],
  "commit_message": "refactor: Update payment calculation to use Decimal",

  "intent": "Switch from float to Decimal for currency precision. Batch settlements accumulate floating-point errors. After testing with 10,000 simulated transactions, float arithmetic drifted by $0.03 while Decimal was exact.",
  "exploration": "Read checkout.py, billing.py, refunds.py, settlement.py. Found 14 call sites across 5 files. Ran test with 10,000 simulated transactions. Checked batch_settlement_job.py - it calls calculate_total() which now returns Decimal.",
  "alternatives": [
    {
      "description": "Keep float + round at the end",
      "rejected_reason": "Error accumulates across batch operations. 10,000 transactions drift by $0.03.",
      "effort_estimate": "1 file change"
    },
    {
      "description": "Use integer cents everywhere",
      "rejected_reason": "Existing APIs expect decimal format. Migration would touch 23 files.",
      "effort_estimate": "23 files"
    },
    {
      "description": "Decimal everywhere",
      "rejected_reason": null,
      "effort_estimate": "8 files"
    }
  ],
  "risks": [
    {
      "description": "batch_settlement_job.py CSV export on line 47 uses f-string formatting that may truncate Decimal",
      "confidence": 0.6,
      "file": "batch_settlement_job.py",
      "line": 47
    }
  ],
  "confidence": 0.85,
  "confidence_details": {
    "overall": 0.85,
    "csv_export_formatting": 0.6
  },

  "files_read": [
    "checkout.py", "billing.py", "refunds.py", "settlement.py",
    "batch_settlement_job.py", "tests/test_payment.py"
  ],
  "tools_used": ["Read", "Edit", "Bash", "Grep"],
  "tests_run": ["python -m pytest tests/test_payment.py"],
  "errors_encountered": [],
  "dead_ends": ["Tried integer cents approach (steps 46-68) - tests failed because existing APIs expect decimal format"],

  "total_steps": 121,
  "total_user_prompts": 14,
  "total_thinking_blocks": 23,
  "total_agent_sessions": 1,

  "capture_duration_ms": 487,
  "record_size_bytes": 3241
}
```

### C.2 Multi-agent commit record (git note, ref: g4a-commits/&lt;client_id&gt;)

```json
{
  "version": "1.0",
  "commit_sha": "m3n4o5p6q7r8",
  "parent_sha": "a1b2c3d4e5f6",
  "timestamp": "2026-03-22T11:30:00Z",

  "contributing_sessions": [
    {
      "session_id": "542e0ff9",
      "anchor_sha": "a1b2c3d4e5f6",
      "agent": "claude-code",
      "msg_start": 121,
      "msg_end": 147,
      "step_count": 27
    },
    {
      "session_id": "b2b35939",
      "anchor_sha": "m3n4o5p6q7r8",
      "agent": "claude-code",
      "msg_start": 0,
      "msg_end": 12,
      "step_count": 13
    }
  ],

  "source": "captured",
  "agents": ["claude-code"],
  "primary_agent": "claude-code",
  "total_steps": 40,
  "total_user_prompts": 5,
  "total_thinking_blocks": 8,
  "total_agent_sessions": 2
}
```

### C.3 Human-only commit record (0 steps)

```json
{
  "version": "1.0",
  "commit_sha": "h1i2j3k4l5m6",
  "parent_sha": "m3n4o5p6q7r8",
  "timestamp": "2026-03-22T14:00:00Z",

  "contributing_sessions": [],

  "source": "metadata-only",
  "agents": [],
  "primary_agent": null,

  "files_changed": [
    {"path": "package.json", "lines_added": 2, "lines_removed": 2, "change_type": "modified"}
  ],
  "commit_message": "chore: Update dependencies",

  "intent": null,
  "total_steps": 0,
  "total_user_prompts": 0,
  "total_thinking_blocks": 0,
  "total_agent_sessions": 0,

  "capture_duration_ms": 12,
  "record_size_bytes": 384
}
```

### C.4 Session trace (git note on a1b2c3d, ref: g4a-sessions/&lt;client_id&gt;, abbreviated)

```json
{
  "version": "1.0",
  "session_id": "542e0ff9",
  "agent": "claude-code",
  "agent_version": "2.1.81",
  "model": "claude-opus-4-6",
  "started_at": "2026-03-22T09:30:00Z",
  "last_captured_at": "2026-03-22T11:30:00Z",
  "commits_in_session": ["a1b2c3d4e5f6", "m3n4o5p6q7r8"],
  "total_user_prompts": 18,
  "total_thinking_blocks": 31,
  "total_tool_calls": 89,
  "total_files_read": 12,
  "total_files_written": 8,
  "total_errors": 2,
  "events": [
    {
      "index": 0,
      "type": "user_prompt",
      "timestamp": "2026-03-22T09:30:12Z",
      "content": "Refactor payment processing to use Decimal instead of float",
      "parent_event": null,
      "is_dead_end": false,
      "phase": "exploration"
    },
    {
      "index": 1,
      "type": "thinking",
      "timestamp": "2026-03-22T09:30:15Z",
      "content": "I need to understand the current payment system. Let me read the relevant files first...",
      "parent_event": 0,
      "is_dead_end": false,
      "phase": "exploration"
    },
    {
      "index": 2,
      "type": "tool_call",
      "timestamp": "2026-03-22T09:30:16Z",
      "content": "",
      "tool_name": "Read",
      "tool_input": {"file_path": "checkout.py"},
      "tool_duration_ms": 45,
      "parent_event": 1,
      "is_dead_end": false,
      "phase": "exploration"
    },
    "... (events 3-45: reading files, exploring codebase) ...",
    {
      "index": 46,
      "type": "thinking",
      "timestamp": "2026-03-22T09:52:03Z",
      "content": "Let me try using integer cents. This avoids floating point entirely...",
      "parent_event": 0,
      "is_dead_end": true,
      "phase": "implementation"
    },
    "... (events 47-65: integer cents attempt, all marked is_dead_end: true) ...",
    {
      "index": 66,
      "type": "tool_call",
      "timestamp": "2026-03-22T10:01:22Z",
      "content": "",
      "tool_name": "Bash",
      "tool_input": {"command": "python -m pytest tests/test_payment.py"},
      "tool_duration_ms": 6000,
      "parent_event": 46,
      "is_dead_end": true,
      "phase": "testing"
    },
    {
      "index": 67,
      "type": "tool_result",
      "timestamp": "2026-03-22T10:01:28Z",
      "content": "FAILED tests/test_payment.py::test_api_response - AssertionError: expected Decimal, got int",
      "parent_event": 66,
      "is_dead_end": true,
      "phase": "testing"
    },
    {
      "index": 68,
      "type": "thinking",
      "timestamp": "2026-03-22T10:01:30Z",
      "content": "Integer cents won't work - the existing APIs expect decimal format. I'd have to touch 23 files. Let me try Decimal everywhere instead...",
      "parent_event": 0,
      "is_dead_end": false,
      "phase": "implementation"
    },
    "... (events 69-119: Decimal approach, implementation, testing) ...",
    {
      "index": 120,
      "type": "commit",
      "timestamp": "2026-03-22T10:15:01Z",
      "content": "refactor: Update payment calculation to use Decimal",
      "tool_name": "Bash",
      "tool_input": {"command": "git commit -m 'refactor: Update payment calculation to use Decimal'"},
      "parent_event": 68,
      "is_dead_end": false,
      "phase": "implementation"
    },
    "... (events 121-147: second task, reporting module update, second commit) ..."
  ]
}
```

The session trace shows the full story: 14 prompts, 23 thinking blocks, a dead end with integer cents (events 46-68, all marked `is_dead_end: true`), the pivot to Decimal, and the final commit. Two commits in one session, both linked. All captured. Nothing lost.
