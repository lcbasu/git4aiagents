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
5. [Reasoning record schema](#5-reasoning-record-schema)
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

**Hard rule:** Every byte written to `.g4a/` passes through the secret masking pipeline first. There is no bypass. No flag, no env var, no config option skips masking.

### 1.3 Zero-config usability

Two commands to start: `pip install g4a` (or `brew install g4a`) then `g4a init`. No account, no server, no API key, no config file, no YAML. g4a auto-detects which agent produced the commit and selects the right capture adapter. The developer never thinks about g4a until they need it.

**Hard rule:** `g4a init` completes in under 2 seconds and requires zero user input.

---

## 2. Architecture overview

**POC scope: Claude Code only.** The architecture is agent-agnostic by design - the adapter layer, schema, and storage are all decoupled from any specific agent. But the POC ships with one adapter: Claude Code. Other agents (Cursor, Copilot, Codex, etc.) will be added once the Claude Code version is stable and the schema has been validated in production. The adapter interface is documented so the community can contribute adapters.

```
+------------------------------------------------------------------+
|                         Developer workflow                        |
|  [AI Agent (Claud Code to start with)] --> [writes code] --> [git commit] --> [git push]  |
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
|  CBOR + zstd    |
|  .g4a/ dir      |
+--------+--------+
         |
         |  Retry queue
         |  (.g4a/pending.json)
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
| Storage engine | Serialize to CBOR, compress with zstd, write to `.g4a/` | < 100ms (background) |
| Retry queue | Track failed captures, drain on next success | < 50ms (background) |
| Query engine | Decompress, search, rank results | < 300ms (interactive) |
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
    retry.py               # Retry queue management (.g4a/pending.json)
    adapters/
      __init__.py
      base.py              # Abstract adapter interface
      claude_code.py       # Claude Code transcript parser (POC)
      metadata.py          # Fallback: metadata-only capture
  extract/
    __init__.py
    extractor.py           # Normalize raw capture to ReasoningRecord
    schema.py              # ReasoningRecord dataclass + validation
  security/
    __init__.py
    masker.py              # Secret masking pipeline
    patterns.py            # Regex patterns for known secret formats
    entropy.py             # Shannon entropy detector for unknown secrets
  storage/
    __init__.py
    engine.py              # Read/write .g4a/ files
    codec.py               # CBOR serialization + zstd compression
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
    post-commit            # Shell script installed by g4a init
```

### 3.2 Dependency budget

Minimal dependencies keep install fast and attack surface small:

| Dependency | Purpose | Size |
|------------|---------|------|
| `cbor2` | CBOR serialization (RFC 8949) | 45 KB |
| `zstandard` | zstd compression | 1.2 MB (C extension) |
| `click` | CLI framework | 300 KB |
| `rich` | Terminal formatting | 700 KB |
| `jinja2` | HTML template rendering (web report) | 500 KB |

**No LLM SDK required.** The POC uses only local file parsing (Claude Code JSONL transcripts). No network calls, no API keys, no LLM SDK. Future inference adapters may optionally use LLM APIs, but the core package will never require them.

**Total install size target:** < 5 MB.
**Install time target:** < 10 seconds on a cold pip cache.

---

## 4. Data flow

### 4.1 Capture flow (Claude Code - direct)

```
1. Developer uses Claude Code normally
2. Claude Code writes session transcript to:
   ~/.claude/projects/{project-slug}/{session-id}.jsonl
3. Developer commits (or Claude Code commits for them)
4. Post-commit hook fires:
   a. Hook shim reads the commit SHA
   b. Fork a detached background process
   c. Hook returns immediately (< 50ms)
5. Background process:
   a. detector.py checks for Claude Code transcripts
      - Looks at ~/.claude/projects/ for the current project
      - Finds the most recent transcript modified within the last 60 seconds
      - Falls back to git hook adapter if no transcript found
   b. claude_code.py parses the JSONL transcript:
      - Extracts all tool_use blocks (Read, Edit, Write, Bash, Grep, Glob)
      - Extracts all text blocks (agent's visible reasoning)
      - Extracts all thinking blocks (extended thinking, if available)
      - Maps tool_use calls to files read/written
      - Identifies the diff window: only messages between the last
        commit and this commit are relevant
   c. extractor.py normalizes to ReasoningRecord
   d. masker.py scans and redacts secrets
   e. engine.py serializes to CBOR, compresses with zstd
   f. Writes to .g4a/commits/{sha}.g4a
   g. Updates .g4a/index.g4a (append-only search index)
```

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

This ensures `.g4a/` is never empty. Even metadata-only records power `g4a log` and provide a timeline. When additional adapters are added in the future, `g4a backfill` can re-process past commits.

### 4.4 Query flow

```
1. User runs: g4a why process_payment
2. CLI parses the query term
3. query/engine.py:
   a. Reads .g4a/index.g4a (fast path - binary search index)
   b. Finds all commits that mention "process_payment" in:
      - Files changed
      - Reasoning text (intent, exploration, alternatives)
      - Function names extracted from diffs
   c. Loads matching .g4a/commits/{sha}.g4a files
   d. Decompresses (zstd) and deserializes (CBOR)
   e. ranker.py scores results by:
      - Recency (newer = higher)
      - Relevance (exact function name match > file match > text match)
      - Source quality (captured > inferred > metadata-only)
   f. Returns top N results (default 10)
4. CLI renders results with rich formatting
```

---

## 5. Reasoning record schema

The core data structure. Every reasoning record follows this schema regardless of which agent or adapter produced it.

```python
@dataclass
class ReasoningRecord:
    # Identity
    version: str                    # Schema version, e.g. "1.0"
    commit_sha: str                 # Git commit SHA this record is for
    session_id: Optional[str]       # Agent session ID (if available)
    timestamp: str                  # ISO 8601 UTC

    # Source
    source: str                     # "captured" | "inferred" | "metadata-only"
    agent: str                      # "claude-code" | "cursor" | "copilot" | "codex"
                                    # | "windsurf" | "aider" | "unknown"
    agent_version: Optional[str]    # e.g. "2.1.81"
    model: Optional[str]           # e.g. "claude-opus-4-6"

    # What changed
    files_changed: List[FileChange]
    commit_message: str

    # Reasoning (the core value)
    intent: Optional[str]           # WHY this change was made
    exploration: Optional[str]      # What the agent read/tested before deciding
    alternatives: Optional[List[Alternative]]  # Approaches considered + rejected
    risks: Optional[List[Risk]]     # Flagged concerns with confidence levels
    confidence: Optional[float]     # 0.0-1.0 overall confidence
    confidence_details: Optional[Dict[str, float]]  # Per-area confidence

    # Context
    files_read: Optional[List[str]] # Files the agent read during the session
    tools_used: Optional[List[str]] # Tools the agent invoked (Bash, grep, etc.)
    tests_run: Optional[List[str]]  # Test commands executed
    errors_encountered: Optional[List[str]]  # Errors the agent hit and recovered from

    # Metadata
    capture_duration_ms: int        # How long capture took
    record_size_bytes: int          # Size after compression


@dataclass
class FileChange:
    path: str
    lines_added: int
    lines_removed: int
    change_type: str                # "modified" | "added" | "deleted" | "renamed"


@dataclass
class Alternative:
    description: str                # What was considered
    rejected_reason: str            # Why it was rejected
    effort_estimate: Optional[str]  # e.g. "would touch 23 files"


@dataclass
class Risk:
    description: str
    confidence: float               # 0.0-1.0 - how confident the agent is this is NOT a problem
    file: Optional[str]             # Which file the risk applies to
    line: Optional[int]             # Which line
```

### Schema versioning

The schema version is stored in every record. The `.g4a/schema.json` file in the repo defines the current schema. Older records are always readable - new fields are additive, never breaking. If a field is missing from an older record, queries treat it as `null`.

### Self-describing format

`.g4a/schema.json` is a JSON Schema document that describes the ReasoningRecord format. This means any future tool can parse `.g4a/` files without knowing about g4a. The schema is committed to the repo alongside the records.

---

## 6. Storage engine

### 6.1 Directory layout

```
your-project/
  .g4a/
    schema.json                    # JSON Schema for reasoning records
    config.json                    # g4a configuration (agent detection, etc.)
    commits/
      a1b2c3d.g4a                  # Reasoning for commit a1b2c3d
      e4f5g6h.g4a                  # Reasoning for commit e4f5g6h
      ...
    sessions/
      {session-id}.g4a             # Full session trace (Claude Code only)
      ...
    index.g4a                      # Search index (binary, append-only)
```

### 6.2 File format: .g4a files

Each `.g4a` file is:

```
[4 bytes: magic number "G4A\x01"]
[4 bytes: schema version as uint32]
[4 bytes: uncompressed size as uint32]
[N bytes: zstd-compressed CBOR payload]
```

**Why this format:**
- Magic number allows `file` command and git hooks to identify .g4a files
- Schema version in the header means you can detect format without decompressing
- Uncompressed size allows pre-allocating the decompression buffer (faster)
- CBOR is ~30% smaller than JSON for structured data and much faster to parse
- zstd at compression level 3 gives 3-5x compression at 500 MB/s speed

**Expected sizes:**
- Simple commit (1-3 files, basic reasoning): 2-5 KB compressed
- Complex commit (8+ files, detailed alternatives): 15-50 KB compressed
- Full session trace (multi-hour Claude Code session): 100-500 KB compressed

### 6.3 Search index

`.g4a/index.g4a` is an append-only index that maps:

```
file_path    -> [commit_sha, commit_sha, ...]
function_name -> [commit_sha, commit_sha, ...]
keyword      -> [commit_sha, commit_sha, ...]
```

The index is a sorted array of `(key, sha)` tuples, CBOR-encoded and zstd-compressed. Queries use binary search on the sorted keys. The index is rebuilt from scratch if corrupted or missing (`g4a reindex`).

**Why not SQLite?** SQLite adds a 1.2 MB dependency and creates files that don't merge well in git. The append-only index is smaller, faster for the read patterns g4a needs, and produces clean git history.

### 6.4 Git integration

`.g4a/` is a normal directory tracked by git. The `.gitattributes` file marks `.g4a` files as binary:

```
*.g4a binary
```

This prevents git from trying to diff them (which would be meaningless). Users see "binary file changed" in git diffs. The reasoning is only readable through g4a tools. This is intentional - it prevents accidental exposure of reasoning in GitHub PR diffs.

---

## 7. Capture engine

### 7.1 Agent detection

When the post-commit hook fires, g4a must determine which agent produced the commit. Detection order:

```python
def detect_agent(commit_sha: str) -> str:
    # 1. Check for Claude Code session transcript
    #    Look in ~/.claude/projects/{project-slug}/
    #    for a .jsonl file modified within last 60 seconds
    if claude_code_transcript_found():
        return "claude-code"

    # 2. Check commit trailers
    #    "Co-Authored-By: Claude" -> claude-code
    #    "Generated by Cursor" -> cursor
    #    "Co-Authored-By: Copilot" -> copilot
    trailer = parse_commit_trailers(commit_sha)
    if trailer:
        return trailer

    # 3. Check for agent-specific markers
    #    .cursor/ directory exists -> cursor
    #    .github/copilot/ -> copilot
    if agent_markers_found():
        return detected_agent

    # 4. Heuristic: AI-generated commit message patterns
    #    Multiple files changed with formulaic message -> likely AI
    if looks_ai_generated(commit_sha):
        return "unknown-ai"

    # 5. Default
    return "unknown"
```

### 7.2 Claude Code adapter (deep dive)

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

**Commit window detection:**

A single Claude Code session may span multiple commits. g4a must isolate the reasoning for THIS commit only:

```python
def extract_commit_window(transcript, commit_sha):
    # Find the tool_use block where the commit was made
    # This is a Bash tool call containing "git commit"
    commit_index = find_commit_tool_call(transcript, commit_sha)

    # Walk backward to find the start of this unit of work
    # Heuristics:
    #   - Previous commit (if any) marks the start
    #   - A user message that starts a new task marks the start
    #   - Beginning of session if no other marker
    start_index = find_work_start(transcript, commit_index)

    # The reasoning window is [start_index, commit_index]
    return transcript[start_index:commit_index + 1]
```

**Reasoning synthesis:**

From the extracted window, g4a synthesizes the ReasoningRecord fields:

```python
def synthesize_reasoning(window):
    record = ReasoningRecord()

    # Intent: from thinking blocks and text blocks
    # Look for patterns like "I need to...", "The goal is...",
    # "This change will..."
    record.intent = extract_intent(window.thinking + window.text)

    # Exploration: from tool_use blocks
    # Which files were Read, what Bash commands were run
    record.files_read = [t.input["file_path"] for t in window.tool_calls
                         if t.tool == "Read"]
    record.tools_used = list(set(t.tool for t in window.tool_calls))

    # Alternatives: from thinking blocks
    # Look for patterns like "Option 1...", "I could also...",
    # "rejected because..."
    record.alternatives = extract_alternatives(window.thinking)

    # Risks: from thinking blocks and text blocks
    # Look for patterns like "risk", "concern", "might break",
    # "low confidence", "not sure about"
    record.risks = extract_risks(window.thinking + window.text)

    # Confidence: from explicit mentions or inferred from
    # hedging language and exploration depth
    record.confidence = estimate_confidence(window)

    # Tests: from Bash tool calls containing test commands
    record.tests_run = [t.input["command"] for t in window.tool_calls
                        if t.tool == "Bash" and is_test_command(t.input["command"])]

    return record
```

### 7.3 Other agents (post-POC)

The POC ships with Claude Code only. The adapter interface (`capture/adapters/base.py`) is designed so new adapters can be added without changing any other component. See [Future adapters](#future-adapters-post-poc) in the architecture overview for the planned roadmap.

When no Claude Code transcript is found for a commit, g4a writes a metadata-only record (SHA, files changed, commit message) so the timeline has no gaps. This is the fallback for any agent without an adapter.

---

## 8. Secret masking pipeline

The most security-critical component. Every string in the reasoning record passes through this pipeline before being written to disk. There is no way to disable it.

### 8.1 Pipeline stages

```
Raw reasoning text
        |
        v
  [Stage 1: Known patterns]
  Regex matching for known secret formats
        |
        v
  [Stage 2: Entropy detection]
  Shannon entropy analysis for unknown secrets
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

### 8.2 Stage 1: Known patterns

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

### 8.3 Stage 2: Entropy detection

For secrets that don't match known patterns:

```python
def shannon_entropy(s: str) -> float:
    """Calculate Shannon entropy of a string."""
    if not s:
        return 0.0
    freq = Counter(s)
    length = len(s)
    return -sum((c / length) * log2(c / length) for c in freq.values())

def is_likely_secret(s: str) -> bool:
    """Detect high-entropy strings that are likely secrets."""
    if len(s) < 16:
        return False
    entropy = shannon_entropy(s)
    # Random strings: entropy > 4.5 for alphanumeric
    # English text: entropy ~ 3.5-4.0
    # Code: entropy ~ 4.0-4.5
    # Secrets: entropy > 4.5 AND length > 16
    return entropy > 4.5 and len(s) > 16
```

**Context required:** Entropy alone produces false positives (hashes, UUIDs, base64-encoded data). Stage 2 only triggers when the high-entropy string appears near a sensitive context keyword (secret, key, token, password, auth, credential, bearer, api_key).

### 8.4 Stage 3: Context-aware detection

Catches secrets that are low-entropy or short but appear in sensitive positions:

```python
SENSITIVE_KEYS = [
    "password", "passwd", "pwd", "secret", "token", "key", "auth",
    "credential", "api_key", "apikey", "access_key", "private_key",
    "client_secret", "bearer", "authorization"
]

def mask_key_value_pairs(text: str) -> str:
    """Mask values that appear next to sensitive variable names."""
    for key in SENSITIVE_KEYS:
        # Match: PASSWORD=foo, password: "foo", "password": "foo"
        pattern = rf'(?i)({key})\s*[=:]\s*["\']?(\S+)["\']?'
        text = re.sub(pattern, rf'\1=[REDACTED:CONTEXT:{key.upper()}]', text)
    return text
```

### 8.5 Stage 4: Path sanitization

Absolute paths leak username and directory structure:

```python
def sanitize_paths(text: str, repo_root: str) -> str:
    """Replace absolute paths with repo-relative paths."""
    # /Users/lokeshbasu/Developer/git4aiagents/src/main.py
    # becomes: src/main.py
    text = text.replace(repo_root + "/", "")
    text = text.replace(repo_root, ".")

    # Also mask home directory references
    home = os.path.expanduser("~")
    text = text.replace(home, "~")

    return text
```

### 8.6 Irreversibility guarantee

The masking pipeline is one-way. The original text is never stored, cached, or logged. The pipeline operates on the in-memory representation before any bytes touch disk. There is no "unmask" command. This is by design - if a secret is accidentally captured, it cannot be recovered from `.g4a/` files.

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

**Index format:** Sorted array of `(term, sha_list)` tuples. Binary search for lookup. CBOR + zstd compressed. Typical size: 50-200 KB for 1,000 commits.

### 9.2 Query resolution

`g4a why <term>` resolves through multiple strategies:

```python
def resolve_query(term: str) -> List[ReasoningRecord]:
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
def rank(record: ReasoningRecord, query: str) -> float:
    score = 0.0

    # Recency: newer records score higher
    age_days = (now() - record.timestamp).days
    score += max(0, 100 - age_days)  # 100 points for today, 0 for 100+ days ago

    # Relevance: exact matches score higher
    if query in record.files_changed:
        score += 200  # Exact file match
    if query in record.function_index:
        score += 150  # Exact function match
    if query.lower() in record.intent.lower():
        score += 100  # Mentioned in intent

    # Source quality
    if record.source == "captured":
        score += 50   # Direct transcript (Claude Code)
    # metadata-only: +0
    # Future: "inferred" records will score between captured and metadata-only

    # Confidence: higher confidence records are more useful
    if record.confidence:
        score += record.confidence * 30

    return score
```

---

## 10. CLI design

### 10.1 Command reference

```
g4a init                           Initialize g4a in current repo
g4a log [--limit N]                Show recent commits with reasoning summaries
g4a show <commit>                  Show diff + reasoning side by side
g4a why <term>                     Decision trail for a file, function, or keyword
g4a web [--port PORT]              Open visual report in browser
g4a status                         Show g4a health: pending captures, index stats
g4a backfill [--since COMMIT]      Re-process past commits (e.g. after fixing a parse bug)
g4a reindex                        Rebuild search index from .g4a/ files
g4a config [key] [value]           Get/set configuration
g4a export <commit> [--format json|md]  Export reasoning as JSON or Markdown
```

### 10.2 `g4a init` (the most important command)

Must complete in < 2 seconds with zero user input.

```
$ g4a init

  g4a initialized.

  Installed:
    .g4a/schema.json        reasoning record schema
    .g4a/config.json         configuration
    .gitattributes           *.g4a marked as binary
    .git/hooks/post-commit   reasoning capture hook

  Detected:
    Claude Code             direct transcript capture (best quality)
    Other commits           metadata-only (SHA, files, message)

  Next: use your AI coding agent normally. Reasoning is captured automatically.
  Run "g4a log" after your next commit to see it.
```

**What `g4a init` does:**

1. Creates `.g4a/` directory with `schema.json` and `config.json`
2. Appends `*.g4a binary` to `.gitattributes` (creates if needed, does not overwrite)
3. Installs `.git/hooks/post-commit` hook:
   - If no hook exists: writes the g4a hook
   - If a hook exists: appends g4a invocation to the end (preserves existing hooks)
4. Auto-detects available agents (Claude Code transcripts, env vars)
5. Prints summary

**What `g4a init` does NOT do:**
- Ask any questions
- Require any API keys
- Create any accounts
- Make any network requests
- Modify any existing files (except appending to post-commit hook and .gitattributes)

### 10.3 `g4a log` output

```
$ g4a log

  a1b2c3d  2 hours ago  captured via claude-code
  refactor: Update payment calculation to use Decimal
  Intent: Switch from float to Decimal for currency precision.
          Batch settlements accumulate floating-point errors.
  Confidence: 0.85  |  Files: 8  |  Risks: 1 flagged
  ──────────────────────────────────────────────────────

  e4f5g6h  yesterday  inferred via git-hook (haiku)
  fix: Handle null user in auth middleware
  Intent: Prevent NullPointerException when unauthenticated
          request hits the /api/settings endpoint.
  Confidence: 0.72  |  Files: 2  |  Risks: 0
  ──────────────────────────────────────────────────────

  i7j8k9l  3 days ago  metadata-only
  chore: Update dependencies
  Intent: (no Claude Code transcript found for this commit)
  Files: 2
```

Key UX decisions:
- Source quality is always visible: "captured", "inferred", "metadata-only"
- Confidence is always visible when available
- Risk count draws attention to flagged commits
- Metadata-only records gently prompt for LLM configuration

### 10.4 `g4a why` output

```
$ g4a why process_payment

  Decision trail for "process_payment"
  Found in 3 commits across 2 files

  ── a1b2c3d (2 hours ago, captured, confidence: 0.85) ──────────
  Intent: Switch from float to Decimal for currency precision.
  Exploration: Read checkout.py, billing.py, refunds.py, settlement.py.
               Found 14 call sites. Tested 10,000 simulated transactions.
  Alternatives:
    1. Keep float + round at end     REJECTED: error accumulates in batches
    2. Integer cents                  REJECTED: would touch 23 files
    3. Decimal everywhere             CHOSEN: cleanest path, 8 files
  Risks:
    - batch_settlement_job.py:47 CSV export formatting  [LOW confidence: 0.6]
  Files read: checkout.py, billing.py, refunds.py, settlement.py,
              batch_settlement_job.py, tests/

  ── f2g3h4i (2 weeks ago, captured, confidence: 0.91) ──────────
  Intent: Add retry logic to process_payment for transient gateway errors.
  Exploration: Read payment_gateway.py, found 3 timeout scenarios.
               Tested with mock gateway returning 503.
  Alternatives:
    1. Exponential backoff            CHOSEN: standard pattern, 3 retries max
    2. Circuit breaker                REJECTED: overkill for 3 retries
  Risks: none flagged

  ── b5c6d7e (1 month ago, inferred, confidence: 0.68) ──────────
  Intent: Initial implementation of process_payment function.
  (Inferred reasoning - original agent session not available)
```

### 10.5 `g4a show` output

```
$ g4a show a1b2c3d

  ┌─ DIFF ──────────────────────┐  ┌─ REASONING ─────────────────────┐
  │ checkout.py                 │  │ Intent: Switch from float to    │
  │ @@ -45,7 +45,7 @@          │  │ Decimal for currency precision  │
  │ -  total = float(sum)       │  │                                 │
  │ +  total = Decimal(sum)     │  │ This file: 3 of 14 call sites  │
  │                             │  │ Confidence: 0.85                │
  │ billing.py                  │  │                                 │
  │ @@ -12,5 +12,5 @@          │  │ Alternatives considered:        │
  │ -  amount = float(price)    │  │ 1. float + round -> rejected    │
  │ +  amount = Decimal(price)  │  │ 2. integer cents -> rejected    │
  │                             │  │                                 │
  │ settlement.py               │  │ Risk flagged:                   │
  │ @@ -47,3 +47,3 @@          │  │ CSV export on line 47 may      │
  │ -  f"{total:.2f}"           │  │ truncate Decimal [conf: 0.6]   │
  │ +  f"{total:.2f}"           │  │                                 │
  └─────────────────────────────┘  └─────────────────────────────────┘
```

Side-by-side diff + reasoning. The reviewer sees WHAT changed on the left and WHY on the right.

---

## 11. Web reporter

`g4a web` generates a static HTML report and opens it in the browser.

### 11.1 Features

- Timeline view of all commits with reasoning
- Per-commit detail pages (diff + reasoning side by side)
- Search across all reasoning records
- Filter by: source (captured/inferred), confidence range, agent, date range
- Risk dashboard: all flagged risks across commits
- Statistics: commits per day, average confidence, source distribution

### 11.2 Implementation

- Single static HTML file with embedded CSS/JS (no server required)
- All data embedded as a JSON blob in a `<script>` tag
- Uses vanilla JavaScript - no framework, no build step
- Opens with `python -m webbrowser` (stdlib)
- Optional: `g4a web --serve` starts a local HTTP server for live reload

### 11.3 Size budget

The HTML report for 1,000 commits should be < 2 MB. For larger repos, `g4a web` paginates and uses lazy loading.

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
| Background: serialize + compress | 50-100ms | CBOR + zstd |
| Background: write to disk | 10ms | Single file write |
| Background: update index | 50ms | Append to index file |
| **Total hook latency** | **< 20ms** | Developer never waits |
| **Total background time** | **< 1s (captured), < 12s (inferred)** | Invisible |

### 12.2 Query path (must feel instant)

| Step | Target | Method |
|------|--------|--------|
| CLI startup | 30ms | Lazy imports, no heavy init |
| Load index | 20ms | Memory-map the index file |
| Search index | 5ms | Binary search on sorted keys |
| Load matching records | 50-200ms | Decompress 5-20 .g4a files |
| Rank results | 5ms | Score and sort |
| Render output | 20ms | Rich terminal formatting |
| **Total query time** | **< 300ms** | Feels instant |

### 12.3 How we keep the CLI fast

- **Lazy imports:** `import cbor2` and `import zstandard` only when actually needed, not at CLI startup
- **No global init:** CLI parses args and dispatches immediately, no database connections or config validation at startup
- **Memory-mapped index:** The search index is memory-mapped, so the OS handles caching. Second query is near-instant.
- **Parallel decompression:** When loading multiple records, decompress in parallel using `concurrent.futures.ThreadPoolExecutor`

---

## 13. Security model

### 13.1 Threat model

| Threat | Mitigation |
|--------|-----------|
| Secret leaked into reasoning record | Mandatory masking pipeline, no bypass |
| .g4a/ files pushed to public repo | Secrets already masked before write. Binary format prevents casual reading. |
| Reasoning reveals proprietary logic | Same as pushing source code - .g4a/ visibility matches repo visibility |
| Code sent to external API | POC makes zero network calls (local transcript parsing only). Future inference adapters will require explicit opt-in. |
| Malicious .g4a/ file in cloned repo | CBOR deserialization with strict mode, size limits, no code execution |
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
Secret masking pipeline (4 stages)
    |
    v
[Verified clean - all patterns checked]
    |
    v
CBOR serialization (structured, typed)
    |
    v
zstd compression (binary, not human-readable)
    |
    v
Written to .g4a/ (git-tracked, binary-diffed)
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

**Retry queue file:** `.g4a/pending.json`

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
        write_record(record)

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
            write_record(record)
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
def capture(commit_sha: str) -> ReasoningRecord:
    record = ReasoningRecord(commit_sha=commit_sha, timestamp=now())

    # Stage 1: Find transcript
    try:
        transcript = find_claude_code_transcript(commit_sha)
        record.source = "captured"
        record.agent = "claude-code"
    except TranscriptNotFound:
        # No transcript available - write metadata-only record
        record.source = "metadata-only"
        record = populate_metadata_only(record, commit_sha)
        return mask_and_return(record)
    except Exception as e:
        # Unexpected error finding transcript - queue retry, write nothing
        raise CaptureError(f"transcript lookup: {e}")

    # Stage 2: Parse transcript
    try:
        window = extract_commit_window(transcript, commit_sha)
        reasoning = synthesize_reasoning(window)
        record.intent = reasoning.intent
        record.exploration = reasoning.exploration
        record.alternatives = reasoning.alternatives
        record.risks = reasoning.risks
        record.confidence = reasoning.confidence
        record.files_read = reasoning.files_read
        record.tools_used = reasoning.tools_used
        record.tests_run = reasoning.tests_run
    except Exception as e:
        # Transcript exists but parsing failed
        # Write what we have (metadata) + queue retry for full parse
        log_error(commit_sha, e, stage="parse")
        record.source = "metadata-only"
        record = populate_metadata_only(record, commit_sha)
        add_to_retry_queue(commit_sha, error=str(e), stage="parse")
        # Fall through to masking - still write the metadata record

    # Stage 3: Mask secrets (NEVER skip this)
    try:
        record = mask_secrets(record)
    except Exception as e:
        # Masking failure is critical - discard entire record rather than
        # risk writing unmasked secrets to disk
        raise CaptureError(f"CRITICAL masking failure, record discarded: {e}")

    # Stage 4: Serialize and write
    try:
        write_record(record)
    except Exception as e:
        raise CaptureError(f"write failed: {e}")

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
  Disk usage: 1.2 MB (.g4a/)
```

**Recovery is always automatic.** The developer never needs to run a command to fix a failed capture. The next commit triggers retry. If they want to force it, `g4a backfill e4f5g6h` retries immediately.

### 14.5 Error log

`.g4a/errors.log` captures all background processing errors for debugging:

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
  -> Full ReasoningRecord with all fields (best)

Claude Code transcript found, parse fails
  -> Metadata-only record written immediately
  -> Full capture queued for retry (up to 3 attempts)
  -> If retry succeeds, metadata-only record is replaced with full record

No Claude Code transcript found
  -> Metadata-only record (commit SHA, files changed, message)
  -> No retry needed (nothing to retry against)

Masking pipeline fails
  -> Record discarded entirely (security-critical)
  -> Error logged, queued for retry

Write to .g4a/ fails (disk full, permissions, etc.)
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
| Claude Code adapter | Parse real transcript fixtures, commit window detection |
| Query engine | Index building, search accuracy, ranking correctness |
| Agent detector | Claude Code detection, fallback to metadata-only |

### 15.2 Integration tests

- **End-to-end capture:** Create a git repo, make commits with mock Claude Code transcripts, verify .g4a/ files are created correctly
- **End-to-end query:** Populate .g4a/ with fixture records, run g4a why/log/show, verify output
- **Hook installation:** Verify g4a init installs hooks correctly, preserves existing hooks
- **Secret masking:** Feed known secrets through the full pipeline, verify they never appear in output

### 15.3 Security tests

- **Secret corpus:** Maintain a test corpus of 500+ real-world secret patterns. Every CI run verifies 100% detection rate.
- **Fuzzing:** Fuzz the CBOR deserializer with random bytes, verify no crashes or code execution
- **Timing attack:** Verify masking pipeline runs in constant time (doesn't leak secret presence via timing)

### 15.4 Performance benchmarks

- **Capture latency:** Hook must return in < 50ms (measured in CI)
- **Query latency:** `g4a why` must return in < 300ms for 1,000 records (measured in CI)
- **Index size:** Must be < 500 KB for 1,000 commits
- **Record size:** Must be < 100 KB per commit (compressed)

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

Single command. No extras, no optional dependencies for core functionality. The `zstandard` package includes pre-built wheels for all major platforms (macOS, Linux, Windows, x86_64, ARM64), so no C compiler is needed.

### 16.2 Homebrew

```
brew install g4a
```

Ships as a day-one installation option alongside pip. The Homebrew formula:

- Hosted in a tap: `brew tap lcbasu/g4a && brew install g4a` (short form: `brew install lcbasu/g4a/g4a`)
- Once adoption grows, submit to homebrew-core for `brew install g4a` directly
- The formula installs the same Python package under the hood via a virtualenv managed by Homebrew
- Bundles Python 3.12+ as a dependency so users don't need to manage Python themselves
- Pins the `zstandard` and `cbor2` C extensions to the Homebrew-compiled versions for native performance

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

  resource "zstandard" do
    url "https://files.pythonhosted.org/packages/source/z/zstandard/zstandard-0.23.0.tar.gz"
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
g4a/hooks/post-commit   Shell script template for git hook
g4a/web/templates/      HTML templates for web reporter
g4a/security/patterns/  Secret pattern database
```

---

## 17. Future extensions

Not in the POC, but designed to be additive:

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
G4A_DEBUG            Set to "1" for verbose logging to .g4a/errors.log
```

Reserved for future adapters (not used in POC):

```
G4A_API_KEY          API key for LLM inference adapters (post-POC)
G4A_API_BASE         Custom API base URL (post-POC)
G4A_MODEL            Model for inference (post-POC)
```

---

## Appendix B: Post-commit hook script

```bash
#!/bin/sh
# g4a reasoning capture hook
# This hook returns immediately. All work happens in the background.

# Skip if g4a is disabled
[ "$G4A_DISABLE" = "1" ] && exit 0

# Get the commit SHA
SHA=$(git rev-parse HEAD 2>/dev/null) || exit 0

# Get the repo root
REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

# Check g4a is initialized
[ -d "$REPO/.g4a" ] || exit 0

# Fork to background and return immediately
# The background process captures reasoning and writes to .g4a/
(
  # Detach from terminal
  exec </dev/null >/dev/null 2>/dev/null

  # Run capture in background
  python3 -m g4a capture "$SHA" --repo "$REPO" 2>> "$REPO/.g4a/errors.log" || true
) &

# Return immediately - never block the commit
exit 0
```

---

## Appendix C: Reasoning record example (CBOR, shown as JSON)

```json
{
  "version": "1.0",
  "commit_sha": "a1b2c3d4e5f6",
  "session_id": "542e0ff9-b7aa-490f-ab02-f6b1f6952727",
  "timestamp": "2026-03-22T10:15:03Z",
  "source": "captured",
  "agent": "claude-code",
  "agent_version": "2.1.81",
  "model": "claude-opus-4-6",
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
    "checkout.py",
    "billing.py",
    "refunds.py",
    "settlement.py",
    "batch_settlement_job.py",
    "tests/test_payment.py"
  ],
  "tools_used": ["Read", "Edit", "Bash", "Grep"],
  "tests_run": ["python -m pytest tests/test_payment.py"],
  "errors_encountered": [],
  "capture_duration_ms": 487,
  "record_size_bytes": 2847
}
```
