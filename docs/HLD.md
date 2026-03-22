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

```
+------------------------------------------------------------------+
|                         Developer workflow                        |
|  [AI Agent] --> [writes code] --> [git commit] --> [git push]     |
+------------------------------------------------------------------+
        |                                |
        |  (1) Session transcript        |  (2) Post-commit hook
        |      (Claude Code only)        |      (all agents)
        v                                v
+------------------+          +--------------------+
|  Claude Code     |          |  Git Hook          |
|  Adapter         |          |  Adapter           |
|                  |          |                    |
|  Reads JSONL     |          |  Reads diff +      |
|  transcripts     |          |  context, infers   |
|  directly        |          |  reasoning via LLM |
+--------+---------+          +---------+----------+
         |                              |
         +-------------+----------------+
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
              |  Regex + entropy|
              |  detection      |
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
| Git hook adapter | Read diff, call LLM for inference | < 10s (background) |
| Reasoning extractor | Normalize captured/inferred reasoning to unified schema | < 100ms (background) |
| Secret masking pipeline | Scan and redact all sensitive data | < 200ms (background) |
| Storage engine | Serialize to CBOR, compress with zstd, write to `.g4a/` | < 100ms (background) |
| Query engine | Decompress, search, rank results | < 300ms (interactive) |
| CLI | Parse commands, render output | < 50ms overhead |
| Web reporter | Generate static HTML, open browser | < 1s |

**Total background capture time:** < 1s for Claude Code, < 12s for LLM-inferred.
**Total interactive query time:** < 500ms for any query.

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
    adapters/
      __init__.py
      base.py              # Abstract adapter interface
      claude_code.py       # Claude Code transcript parser
      git_hook.py          # Generic git diff + LLM inference
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

**No LLM SDK in core.** The git hook inference adapter calls the LLM via HTTP directly using `urllib` from the stdlib. This avoids pulling in `anthropic` (15 MB+) or `openai` as a hard dependency. If the user has the SDK installed, g4a can optionally use it, but it is never required.

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

### 4.2 Capture flow (other agents - git hook inference)

```
1. Developer uses any AI coding agent (Cursor, Copilot, Codex, etc.)
2. Agent commits code
3. Post-commit hook fires:
   a. Hook shim reads the commit SHA
   b. Fork a detached background process
   c. Hook returns immediately (< 50ms)
4. Background process:
   a. detector.py checks for Claude Code transcripts - none found
   b. Falls back to git_hook.py adapter:
      - Reads the commit diff (git diff HEAD~1 HEAD)
      - Reads the commit message
      - Reads the list of files changed
      - Reads up to 200 lines of surrounding context per changed file
      - Constructs a prompt for reasoning inference
   c. Calls the configured LLM API:
      - Default: Claude claude-haiku-4-5-20251001 (fast, cheap)
      - Configurable via G4A_MODEL env var
      - Prompt: "Given this diff and context, infer the developer's
        reasoning: intent, alternatives considered, risks, confidence."
      - Response parsed into ReasoningRecord fields
      - Record is tagged: source="inferred" (vs "captured" for direct)
   d. masker.py scans and redacts secrets
   e. engine.py serializes and writes to .g4a/commits/{sha}.g4a
   f. Updates .g4a/index.g4a
```

### 4.3 Capture flow (no LLM available - metadata only)

If no LLM API key is configured, g4a still captures structural metadata:

```
- Commit SHA, timestamp, author
- Files changed, lines added/removed per file
- Commit message
- Whether the commit was likely AI-generated (heuristic: checks for
  "Co-Authored-By" trailer, common AI commit message patterns)
- source="metadata-only"
```

This ensures `.g4a/` is never empty. Even metadata-only records power `g4a log` and provide a timeline. When an LLM key is added later, `g4a backfill` can re-process past commits.

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

### 7.3 Git hook adapter (inference)

For agents without direct transcript access:

**Input gathered:**

```python
def gather_context(commit_sha):
    return {
        "diff": git_diff(commit_sha),           # Full diff
        "message": git_log_message(commit_sha),  # Commit message
        "files": git_diff_names(commit_sha),     # File list
        "context": {},                            # Surrounding code per file
    }

    # For each changed file, read up to 200 lines of surrounding context
    # This gives the LLM enough to understand WHY the change was made
    for file in context["files"]:
        context["context"][file] = read_surrounding_context(file, 200)
```

**Inference prompt:**

```
You are analyzing a code commit to infer the developer's reasoning.

Commit: {sha}
Message: {message}

Files changed:
{file_list}

Diff:
{diff}

Surrounding context:
{context}

Respond in JSON with these fields:
- intent: Why was this change made? (1-3 sentences)
- exploration: What files/functions were likely reviewed? (list)
- alternatives: What other approaches might have been considered and rejected? (list with reasons)
- risks: Any concerns about this change? (list with confidence 0-1)
- confidence: Overall confidence this reasoning is correct (0-1)

Be specific. Reference exact file names, function names, and line numbers.
If you're uncertain about something, say so and lower the confidence.
```

**Model selection:**

```
G4A_MODEL=claude-haiku-4-5-20251001   # Default: fast, cheap ($0.25/M input, $1.25/M output)
G4A_MODEL=claude-sonnet-4-6           # Better reasoning, higher cost
G4A_MODEL=gpt-4o-mini                 # OpenAI alternative
G4A_API_KEY=...                       # Required for inference adapter
G4A_API_BASE=...                      # Custom API endpoint (for proxies/self-hosted)
```

**Cost estimate per commit:**
- Average diff: ~500 tokens
- Surrounding context: ~2,000 tokens
- Prompt template: ~200 tokens
- Total input: ~2,700 tokens
- Output: ~500 tokens
- Cost with Haiku: ~$0.001 per commit (less than a tenth of a cent)
- 1,000 commits/month: ~$1.00

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

Regex patterns for common secret formats:

```python
PATTERNS = [
    # AWS
    (r'AKIA[0-9A-Z]{16}', "AWS_ACCESS_KEY"),
    (r'[0-9a-zA-Z/+]{40}', "AWS_SECRET_KEY"),  # Only near "aws" or "secret" context

    # API keys (generic)
    (r'sk-[a-zA-Z0-9]{20,}', "API_KEY"),            # OpenAI, Anthropic
    (r'sk-ant-[a-zA-Z0-9\-]{80,}', "ANTHROPIC_KEY"),
    (r'key-[a-zA-Z0-9]{32,}', "API_KEY"),

    # Tokens
    (r'ghp_[a-zA-Z0-9]{36}', "GITHUB_TOKEN"),
    (r'gho_[a-zA-Z0-9]{36}', "GITHUB_OAUTH"),
    (r'glpat-[a-zA-Z0-9\-]{20}', "GITLAB_TOKEN"),
    (r'xoxb-[0-9]{10,}-[a-zA-Z0-9]{20,}', "SLACK_TOKEN"),

    # Passwords in connection strings
    (r'(?i)(password|passwd|pwd)\s*[=:]\s*\S+', "PASSWORD"),
    (r'(?i)(mongodb|postgres|mysql|redis):\/\/[^@]+@', "CONNECTION_STRING"),

    # Private keys
    (r'-----BEGIN (RSA |EC |DSA )?PRIVATE KEY-----', "PRIVATE_KEY"),
    (r'-----BEGIN CERTIFICATE-----', "CERTIFICATE"),

    # JWTs
    (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}', "JWT"),

    # Generic hex secrets (32+ chars in sensitive context)
    (r'(?i)(secret|token|key|auth|bearer)\s*[=:]\s*["\']?[0-9a-f]{32,}', "HEX_SECRET"),
]
```

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
        score += 50   # Direct transcript
    elif record.source == "inferred":
        score += 25   # LLM-inferred
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
g4a init                           Initialize g4a in current repo
g4a log [--limit N]                Show recent commits with reasoning summaries
g4a show <commit>                  Show diff + reasoning side by side
g4a why <term>                     Decision trail for a file, function, or keyword
g4a web [--port PORT]              Open visual report in browser
g4a status                         Show g4a health: pending captures, index stats
g4a backfill [--since COMMIT]      Re-process past commits (after adding LLM key)
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
    Git hook fallback       for other agents (set G4A_API_KEY for LLM inference)

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
  Intent: (no LLM configured - set G4A_API_KEY for reasoning inference)
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
| Background: or call LLM (inference) | 3-10s | HTTP POST to API |
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
| LLM inference sends code to external API | Only with explicit G4A_API_KEY. No default. Clearly documented. |
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

### 13.3 LLM inference security

When using the git hook inference adapter:

- **What is sent:** The diff, commit message, and surrounding code context. This is the same data already visible in git. No additional information is exposed.
- **What is NOT sent:** File contents not in the diff, other .g4a/ records, environment variables, system information.
- **API key handling:** Read from `G4A_API_KEY` env var. Never stored in `.g4a/config.json`. Never committed to git.
- **Network:** HTTPS only. Certificate validation enabled. No proxy bypass.

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

### 14.1 Philosophy

g4a must never break the developer's workflow. Every error is handled gracefully:

- **Capture failure:** Log to `.g4a/errors.log`, commit succeeds normally
- **Masking failure:** Discard the entire record rather than write unmasked data
- **Query failure:** Show a helpful error, suggest `g4a reindex`
- **Corrupt .g4a file:** Skip it, warn the user, continue with other records
- **Missing index:** Rebuild automatically on next query
- **Network failure (inference):** Fall back to metadata-only record

### 14.2 Error log

`.g4a/errors.log` captures all background processing errors:

```
2026-03-22T10:15:03Z ERROR capture/claude_code.py: Transcript parse failed for session abc123
  Reason: Unexpected message type "custom_event" at line 847
  Action: Skipped session, commit a1b2c3d has no reasoning record
  Recovery: Run "g4a backfill a1b2c3d" to retry

2026-03-22T11:30:15Z WARN security/masker.py: High-entropy string detected but no sensitive context
  Location: intent field, position 234-270
  Action: Masked conservatively (may be a false positive)
  String: [REDACTED:ENTROPY:sha256=f4e5d6]
```

### 14.3 Graceful degradation

```
Full capture available   -> ReasoningRecord with all fields
Transcript parse fails   -> Fall back to git hook inference
No LLM key configured   -> Fall back to metadata-only
Metadata extraction fails -> Empty record with just SHA + timestamp
Everything fails         -> No record written, commit succeeds, error logged
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
| Agent detector | Detection order, edge cases (multiple agents, no agent) |

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
G4A_API_KEY          API key for LLM inference (Anthropic, OpenAI, etc.)
G4A_API_BASE         Custom API base URL
G4A_MODEL            Model for inference (default: claude-haiku-4-5-20251001)
G4A_DISABLE          Set to "1" to temporarily disable capture
G4A_DEBUG            Set to "1" for verbose logging
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
