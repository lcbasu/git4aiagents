import time

from g4a.capture.transcript import find_transcript, parse_transcript, find_commit_in_transcript
from g4a.git_utils import run_git, run_git_ok
from g4a.security.masker import mask_secrets, mask_dict
from g4a.storage.notes import write_note


def run_capture(commit_sha, repo_root):
    # Read client_id
    import os
    client_id_path = os.path.join(repo_root, ".git", "g4a", "client_id")
    try:
        with open(client_id_path) as f:
            client_id = f.read().strip()
    except FileNotFoundError:
        return  # g4a not initialized

    notes_ref = f"g4a-commits/{client_id}"

    # Get commit metadata
    fmt = "%H%n%P%n%aI%n%s%n%an"
    log_output = run_git_ok("log", "-1", f"--format={fmt}", commit_sha, repo=repo_root)
    if not log_output:
        return

    lines = log_output.split("\n")
    full_sha = lines[0] if len(lines) > 0 else commit_sha
    parent_sha = lines[1] if len(lines) > 1 else ""
    timestamp = lines[2] if len(lines) > 2 else ""
    message = lines[3] if len(lines) > 3 else ""
    author = lines[4] if len(lines) > 4 else ""

    # Get files changed
    diff_output = run_git_ok("diff", "--name-status", f"{parent_sha}..{full_sha}", repo=repo_root) or ""
    files_changed = []
    for line in diff_output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            change_type = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed"}.get(parts[0][0], parts[0])
            files_changed.append({"path": parts[-1], "change_type": change_type})

    # Find transcript
    transcript_path = find_transcript(repo_root)

    if not transcript_path:
        # Metadata-only record
        record = build_record(
            full_sha, parent_sha, timestamp, message, author, files_changed,
            source="metadata-only", agent=None, session_id=None,
            intent=None, files_read=[], tools_used=[],
            total_events=0, thinking_blocks=0, user_prompts=0,
            reasoning_summary=None,
        )
        write_note(notes_ref, full_sha, record, repo=repo_root)
        update_watermark(repo_root)
        return

    # Parse transcript
    events = parse_transcript(transcript_path)
    commit_idx = find_commit_in_transcript(events, full_sha)

    # Determine event range for this commit
    if commit_idx >= 0:
        # Find previous commit boundary
        prev_boundary = 0
        for i in range(commit_idx - 1, -1, -1):
            if events[i]["type"] == "tool_call":
                tool_input = events[i].get("tool_input", {})
                cmd = tool_input.get("command", "")
                if "git commit" in cmd:
                    prev_boundary = i + 1
                    break
        relevant_events = events[prev_boundary:commit_idx + 1]
    else:
        # Commit not found in transcript - use last 100 events as best guess
        relevant_events = events[-100:] if len(events) > 100 else events

    # Extract reasoning from relevant events
    session_id = transcript_path.stem

    thinking_texts = []
    text_blocks = []
    files_read = []
    tools_used = set()
    user_prompt_count = 0
    thinking_count = 0

    for evt in relevant_events:
        content = mask_secrets(evt.get("content", ""), repo_root)

        if evt["type"] == "thinking":
            thinking_texts.append(content)
            thinking_count += 1
        elif evt["type"] == "text":
            text_blocks.append(content)
        elif evt["type"] == "tool_call":
            tool_name = evt.get("tool_name", "")
            tools_used.add(tool_name)
            tool_input = evt.get("tool_input", {})
            masked_input = mask_dict(tool_input, repo_root)
            if tool_name == "Read" and "file_path" in masked_input:
                files_read.append(masked_input["file_path"])
        elif evt["type"] == "user_prompt":
            user_prompt_count += 1

    # Build intent from first thinking block or first text block
    intent = None
    if thinking_texts:
        intent = thinking_texts[0][:500]
    elif text_blocks:
        intent = text_blocks[0][:500]

    # Build reasoning summary from all thinking blocks
    reasoning_summary = "\n---\n".join(t[:300] for t in thinking_texts[:10])
    if len(reasoning_summary) > 3000:
        reasoning_summary = reasoning_summary[:3000] + "\n[TRUNCATED]"

    record = build_record(
        full_sha, parent_sha, timestamp, message, author, files_changed,
        source="captured", agent="claude-code", session_id=session_id,
        intent=intent, files_read=list(dict.fromkeys(files_read)),
        tools_used=sorted(tools_used),
        total_events=len(relevant_events),
        thinking_blocks=thinking_count,
        user_prompts=user_prompt_count,
        reasoning_summary=reasoning_summary,
    )

    write_note(notes_ref, full_sha, record, repo=repo_root)
    update_watermark(repo_root)


def build_record(sha, parent, timestamp, message, author, files_changed,
                 source, agent, session_id, intent, files_read, tools_used,
                 total_events, thinking_blocks, user_prompts, reasoning_summary):
    return {
        "version": "1.0",
        "commit_sha": sha,
        "parent_sha": parent,
        "timestamp": timestamp,
        "author": author,
        "source": source,
        "agent": agent,
        "session_id": session_id,
        "commit_message": message,
        "files_changed": files_changed,
        "intent": intent,
        "files_read": files_read,
        "tools_used": tools_used,
        "total_events": total_events,
        "thinking_blocks": thinking_blocks,
        "user_prompts": user_prompts,
        "reasoning_summary": reasoning_summary,
    }


def update_watermark(repo_root):
    import os
    path = os.path.join(repo_root, ".git", "g4a", "last_capture_mtime")
    with open(path, "w") as f:
        f.write(str(time.time()))
