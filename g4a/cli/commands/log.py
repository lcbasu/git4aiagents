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
            return f"{m} minute{'s' if m != 1 else ''} ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h} hour{'s' if h != 1 else ''} ago"
        d = seconds // 86400
        if d == 1:
            return "yesterday"
        if d < 30:
            return f"{d} days ago"
        return f"{d // 30} month{'s' if d // 30 != 1 else ''} ago"
    except Exception:
        return iso_timestamp


@click.command("log")
@click.option("--limit", "-n", default=10, help="Number of commits to show")
def log_cmd(limit):
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
        total = record.get("total_events", 0)
        thinking = record.get("thinking_blocks", 0)
        files = record.get("files_changed", [])
        files_read = record.get("files_read", [])
        tools = record.get("tools_used", [])

        # Header line
        if source == "captured" and agent:
            header = f"  {sha}  {ts}  {agent} ({total} events, {thinking} thinking)"
        else:
            header = f"  {sha}  {ts}  {source}"

        click.echo(header)
        click.echo(f"  {message}")

        if intent:
            # Truncate intent for display
            display_intent = intent[:200].replace("\n", " ")
            click.echo(f"  Intent: {display_intent}")

        info_parts = []
        if files:
            info_parts.append(f"{len(files)} files changed")
        if files_read:
            info_parts.append(f"{len(files_read)} files read")
        if tools:
            info_parts.append(f"Tools: {', '.join(tools)}")
        if info_parts:
            click.echo(f"  {' | '.join(info_parts)}")

        if source == "metadata-only":
            click.echo("  (no agent reasoning captured)")

        click.echo("  " + "-" * 50)
