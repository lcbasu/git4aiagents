import json
import os
import re
from pathlib import Path

from g4a.git_utils import find_parent_repo


def repo_to_slug(repo_root):
    normalized = str(Path(repo_root).resolve())
    slug = normalized.replace(os.sep, "-")
    slug = slug.replace(":", "")
    return slug


def _newest_transcript(transcripts_dir):
    if not transcripts_dir.exists():
        return None
    jsonl_files = list(transcripts_dir.glob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


def find_transcript(repo_root):
    # First check for transcripts under this repo's own slug
    slug = repo_to_slug(repo_root)
    transcripts_dir = Path.home() / ".claude" / "projects" / slug
    result = _newest_transcript(transcripts_dir)
    if result:
        return result

    # If this is a sub-repo, also check the parent repo's transcript dir.
    # Claude Code sessions typically run from the root repo, so the transcript
    # will be stored under the parent's slug even when commits happen in sub-repos.
    parent = find_parent_repo(repo_root)
    if parent:
        parent_slug = repo_to_slug(parent)
        parent_dir = Path.home() / ".claude" / "projects" / parent_slug
        return _newest_transcript(parent_dir)

    return None


def parse_transcript(path):
    events = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            timestamp = obj.get("timestamp", "")

            if msg_type == "assistant":
                message = obj.get("message", {})
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "thinking":
                        text = block.get("thinking", "")
                        if text:
                            events.append({
                                "type": "thinking",
                                "content": text,
                                "tool_name": None,
                                "tool_input": None,
                                "timestamp": timestamp,
                            })
                    elif btype == "text":
                        text = block.get("text", "")
                        if text:
                            events.append({
                                "type": "text",
                                "content": text,
                                "tool_name": None,
                                "tool_input": None,
                                "timestamp": timestamp,
                            })
                    elif btype == "tool_use":
                        events.append({
                            "type": "tool_call",
                            "content": "",
                            "tool_name": block.get("name", ""),
                            "tool_input": block.get("input", {}),
                            "timestamp": timestamp,
                        })

            elif msg_type == "user":
                message = obj.get("message", {})
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    events.append({
                        "type": "user_prompt",
                        "content": content,
                        "tool_name": None,
                        "tool_input": None,
                        "timestamp": timestamp,
                    })
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "tool_result":
                            result_content = block.get("content", "")
                            if isinstance(result_content, list):
                                result_content = json.dumps(result_content)[:2000]
                            elif isinstance(result_content, str):
                                result_content = result_content[:2000]
                            else:
                                result_content = str(result_content)[:2000]
                            events.append({
                                "type": "tool_result",
                                "content": result_content,
                                "tool_name": None,
                                "tool_input": None,
                                "timestamp": timestamp,
                            })

    return events


SHA_PATTERN = re.compile(r'\[.*? ([0-9a-f]{7,40})\]')


def find_commit_in_transcript(events, commit_sha):
    for i, evt in enumerate(events):
        if evt["type"] == "tool_result":
            match = SHA_PATTERN.search(evt["content"])
            if match and commit_sha.startswith(match.group(1)):
                # The commit event is the tool_call before this result
                if i > 0 and events[i - 1]["type"] == "tool_call":
                    tool_input = events[i - 1].get("tool_input", {})
                    cmd = tool_input.get("command", "")
                    if "git commit" in cmd or "git add" in cmd:
                        return i - 1
                return i
    return -1
