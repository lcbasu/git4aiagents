import time
from datetime import datetime, timezone

import click

from g4a.git_utils import repo_root as get_repo_root
from g4a.query.reader import load_all_records


def relative_time(iso_timestamp):
    try:
        if "+" in iso_timestamp or iso_timestamp.endswith("Z"):
            dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(iso_timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m} min ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h}h ago"
        d = seconds // 86400
        if d == 1:
            return "yesterday"
        if d < 30:
            return f"{d}d ago"
        return f"{d // 30}mo ago"
    except Exception:
        return iso_timestamp


@click.command("log")
@click.option("--limit", "-n", default=10, help="Number of commits to show")
@click.option("--full", is_flag=True, help="Show full reasoning chain")
def log_cmd(limit, full):
    """Show recent commits with reasoning summaries."""
    try:
        root = get_repo_root()
    except RuntimeError:
        click.echo("Error: not inside a git repository.", err=True)
        raise SystemExit(1)

    records = load_all_records(root)
    if not records:
        click.echo("No reasoning data found.")
        click.echo("Make a commit with an AI agent, then run 'g4a log' again.")
        return

    for record in records[:limit]:
        sha = record.get("commit_sha", "?")[:7]
        ts = relative_time(record.get("timestamp", ""))
        source = record.get("source", "?")
        agent = record.get("agent")
        message = record.get("commit_message", "")
        intent = record.get("intent")
        exploration = record.get("exploration")
        total = record.get("total_events", 0)
        files = record.get("files_changed", [])
        files_read = record.get("files_read", [])
        files_written = record.get("files_written", [])
        tools = record.get("tools_used", [])
        user_prompts = record.get("user_prompts", [])
        commands = record.get("commands_run", [])
        chain = record.get("reasoning_chain", [])

        # Header
        if source == "captured" and agent:
            click.echo(f"\n  {sha}  {ts}  {agent} ({total} events)")
        else:
            click.echo(f"\n  {sha}  {ts}  {source}")
        click.echo(f"  {message}")

        if source == "metadata-only":
            if files:
                click.echo(f"  {len(files)} files changed (no agent reasoning captured)")
            click.echo("  " + "-" * 50)
            continue

        # User prompts
        if user_prompts:
            click.echo(f"  User: \"{user_prompts[0][:150]}\"")
            if len(user_prompts) > 1:
                click.echo(f"        (+{len(user_prompts) - 1} more prompts)")

        # Intent / first response
        if intent:
            for line in intent.split("\n")[:3]:
                click.echo(f"  {line[:150]}")

        # Exploration
        if exploration:
            click.echo(f"  {exploration}")

        # Files
        if files_written:
            click.echo(f"  Wrote: {', '.join(files_written[:8])}" +
                       (f" (+{len(files_written) - 8} more)" if len(files_written) > 8 else ""))

        # Commands
        if commands:
            click.echo(f"  Commands: {len(commands)} run")
            for cmd in commands[:3]:
                click.echo(f"    $ {cmd[:120]}")
            if len(commands) > 3:
                click.echo(f"    ... +{len(commands) - 3} more")

        # Tools summary
        if tools:
            click.echo(f"  Tools: {', '.join(tools)}")

        # Full reasoning chain
        if full and chain:
            click.echo("")
            click.echo("  Reasoning chain:")
            for i, step in enumerate(chain):
                stype = step.get("step", "?")
                if stype == "user_prompt":
                    click.echo(f"    [{i}] USER: \"{step.get('content', '')[:120]}\"")
                elif stype == "response":
                    text = step.get('content', '')
                    # Show more of reasoning text - it's the most valuable part
                    lines = text.split('\n')
                    click.echo(f"    [{i}] AGENT: {lines[0][:150]}")
                    for extra_line in lines[1:6]:
                        if extra_line.strip():
                            click.echo(f"           {extra_line[:150]}")
                    if len(lines) > 6:
                        click.echo(f"           ... ({len(lines) - 6} more lines)")
                elif stype == "thinking":
                    click.echo(f"    [{i}] THINK: {step.get('content', '')[:120]}")
                elif stype == "read":
                    click.echo(f"    [{i}] READ: {step.get('file', '')}")
                elif stype == "write":
                    click.echo(f"    [{i}] WRITE: {step.get('file', '')}")
                elif stype == "command":
                    desc = step.get("description") or step.get("command", "")
                    click.echo(f"    [{i}] RUN: {desc[:120]}")
                elif stype == "search":
                    click.echo(f"    [{i}] {step.get('tool', 'SEARCH')}: {step.get('pattern', '')[:80]}")
                elif stype == "agent":
                    click.echo(f"    [{i}] AGENT-SPAWN: {step.get('description', '')[:80]}")
                elif stype == "task":
                    click.echo(f"    [{i}] {step.get('tool', 'TASK')}: {step.get('subject', '')} {step.get('status', '')}")
                elif stype == "result":
                    text = step.get('content', '')
                    lines = text.split('\n')
                    click.echo(f"    [{i}] RESULT: {lines[0][:150]}")
                    for extra_line in lines[1:4]:
                        if extra_line.strip():
                            click.echo(f"             {extra_line[:150]}")
                    if len(lines) > 4:
                        click.echo(f"             ... ({len(lines) - 4} more lines)")
                elif stype == "error":
                    click.echo(f"    [{i}] ERROR: {step.get('content', '')[:120]}")
                elif stype == "truncated":
                    click.echo(f"    ... {step.get('skipped', 0)} steps skipped ...")
                else:
                    click.echo(f"    [{i}] {stype}: {step.get('tool', '')}")

        click.echo("  " + "-" * 50)
