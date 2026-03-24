import time

from g4a.capture.transcript import find_transcript, parse_transcript, find_commit_in_transcript, is_event_relevant_to_repo
from g4a.git_utils import run_git, run_git_ok
from g4a.security.masker import mask_secrets, mask_dict
from g4a.storage.notes import write_note


def run_capture(commit_sha, repo_root):
    import os
    client_id_path = os.path.join(repo_root, ".git", "g4a", "client_id")
    try:
        with open(client_id_path) as f:
            client_id = f.read().strip()
    except FileNotFoundError:
        return

    notes_ref = f"g4a-commits/{client_id}"

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

    diff_output = run_git_ok("diff", "--name-status", f"{parent_sha}..{full_sha}", repo=repo_root) or ""
    files_changed = []
    for line in diff_output.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            change_type = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed"}.get(parts[0][0], parts[0])
            files_changed.append({"path": parts[-1], "change_type": change_type})

    transcript_path, is_parent_transcript = find_transcript(repo_root)

    if not transcript_path:
        record = build_metadata_only(full_sha, parent_sha, timestamp, message, author, files_changed)
        write_note(notes_ref, full_sha, record, repo=repo_root)
        update_watermark(repo_root)
        return

    events = parse_transcript(transcript_path)
    commit_idx = find_commit_in_transcript(events, full_sha)

    if commit_idx >= 0:
        prev_boundary = 0
        for i in range(commit_idx - 1, -1, -1):
            if events[i]["type"] == "tool_call":
                tool_input = events[i].get("tool_input", {})
                cmd = tool_input.get("command", "")
                if "git commit" in cmd:
                    prev_boundary = i + 1
                    break
        relevant_events = events[prev_boundary:commit_idx + 1]
    elif is_parent_transcript:
        # Commit not found in parent's transcript - don't grab unrelated events.
        # Fall back to metadata-only instead of blindly taking last 200 events.
        record = build_metadata_only(full_sha, parent_sha, timestamp, message, author, files_changed)
        write_note(notes_ref, full_sha, record, repo=repo_root)
        update_watermark(repo_root)
        return
    else:
        relevant_events = events[-200:] if len(events) > 200 else events

    # Filter out events that reference files outside this repo
    resolved_root = os.path.abspath(repo_root)
    relevant_events = [
        evt for evt in relevant_events
        if is_event_relevant_to_repo(evt, resolved_root)
    ]

    session_id = transcript_path.stem

    # Extract the full reasoning chain
    reasoning_chain = []
    files_read = []
    files_written = []
    tools_used = set()
    commands_run = []
    user_prompts = []
    text_blocks = []
    user_prompt_count = 0
    thinking_count = 0

    for evt in relevant_events:
        content = mask_secrets(evt.get("content", ""), repo_root)
        etype = evt["type"]

        if etype == "thinking":
            thinking_count += 1
            if content:
                reasoning_chain.append({
                    "step": "thinking",
                    "content": content[:5000],
                })

        elif etype == "text":
            text_blocks.append(content)
            reasoning_chain.append({
                "step": "response",
                "content": content[:5000],
            })

        elif etype == "tool_call":
            tool_name = evt.get("tool_name", "")
            tools_used.add(tool_name)
            tool_input = mask_dict(evt.get("tool_input", {}), repo_root)

            # Extract meaningful info per tool type
            if tool_name == "Read":
                fp = tool_input.get("file_path", "")
                files_read.append(fp)
                reasoning_chain.append({"step": "read", "file": fp})
            elif tool_name in ("Edit", "Write"):
                fp = tool_input.get("file_path", "")
                files_written.append(fp)
                reasoning_chain.append({"step": "write", "file": fp})
            elif tool_name == "Bash":
                cmd = tool_input.get("command", "")
                desc = tool_input.get("description", "")
                commands_run.append(cmd[:200])
                reasoning_chain.append({
                    "step": "command",
                    "command": cmd[:200],
                    "description": desc[:200] if desc else None,
                })
            elif tool_name in ("Grep", "Glob"):
                pattern = tool_input.get("pattern", "")
                reasoning_chain.append({
                    "step": "search",
                    "tool": tool_name,
                    "pattern": pattern[:200],
                })
            elif tool_name == "Agent":
                desc = tool_input.get("description", "")
                prompt = tool_input.get("prompt", "")
                reasoning_chain.append({
                    "step": "agent",
                    "description": desc[:200],
                    "prompt": prompt[:500],
                })
            elif tool_name in ("TaskCreate", "TaskUpdate"):
                reasoning_chain.append({
                    "step": "task",
                    "tool": tool_name,
                    "subject": tool_input.get("subject", "")[:200],
                    "status": tool_input.get("status", ""),
                })
            else:
                reasoning_chain.append({
                    "step": "tool",
                    "tool": tool_name,
                })

        elif etype == "user_prompt":
            user_prompt_count += 1
            user_prompts.append(content[:500])
            reasoning_chain.append({
                "step": "user_prompt",
                "content": content[:500],
            })

        elif etype == "tool_result":
            if not content:
                continue
            # Capture error results
            if "error" in content.lower()[:200] or "failed" in content.lower()[:200]:
                reasoning_chain.append({
                    "step": "error",
                    "content": content[:2000],
                })
            # Capture rich tool results (agent exploration reports, long outputs)
            elif len(content) > 200:
                reasoning_chain.append({
                    "step": "result",
                    "content": content[:3000],
                })

    # Build intent: combine first user prompt + first substantive text response
    intent_parts = []
    if user_prompts:
        intent_parts.append(f"User asked: {user_prompts[0][:200]}")
    first_response = next((t for t in text_blocks if len(t) > 20), None)
    if first_response:
        intent_parts.append(first_response[:300])
    intent = "\n".join(intent_parts) if intent_parts else None

    # Build exploration summary
    exploration = None
    if files_read:
        unique_reads = list(dict.fromkeys(files_read))
        exploration = f"Read {len(unique_reads)} files: {', '.join(unique_reads[:10])}"
        if len(unique_reads) > 10:
            exploration += f" (+{len(unique_reads) - 10} more)"

    # Truncate reasoning chain if too large (keep first 150 + last 50 steps)
    if len(reasoning_chain) > 250:
        reasoning_chain = reasoning_chain[:150] + [
            {"step": "truncated", "skipped": len(reasoning_chain) - 200}
        ] + reasoning_chain[-50:]

    record = {
        "version": "1.0",
        "commit_sha": full_sha,
        "parent_sha": parent_sha,
        "timestamp": timestamp,
        "author": author,
        "source": "captured",
        "agent": "claude-code",
        "session_id": session_id,
        "commit_message": message,
        "files_changed": files_changed,
        "intent": intent,
        "exploration": exploration,
        "files_read": list(dict.fromkeys(files_read)),
        "files_written": list(dict.fromkeys(files_written)),
        "tools_used": sorted(tools_used),
        "commands_run": commands_run[:20],
        "user_prompts": user_prompts[:10],
        "total_events": len(relevant_events),
        "thinking_blocks": thinking_count,
        "user_prompt_count": user_prompt_count,
        "reasoning_chain": reasoning_chain,
    }

    write_note(notes_ref, full_sha, record, repo=repo_root)
    update_watermark(repo_root)


def build_metadata_only(sha, parent, timestamp, message, author, files_changed):
    return {
        "version": "1.0",
        "commit_sha": sha,
        "parent_sha": parent,
        "timestamp": timestamp,
        "author": author,
        "source": "metadata-only",
        "agent": None,
        "session_id": None,
        "commit_message": message,
        "files_changed": files_changed,
        "intent": None,
        "exploration": None,
        "files_read": [],
        "files_written": [],
        "tools_used": [],
        "commands_run": [],
        "user_prompts": [],
        "total_events": 0,
        "thinking_blocks": 0,
        "user_prompt_count": 0,
        "reasoning_chain": [],
    }


def update_watermark(repo_root):
    import os
    path = os.path.join(repo_root, ".git", "g4a", "last_capture_mtime")
    with open(path, "w") as f:
        f.write(str(time.time()))
