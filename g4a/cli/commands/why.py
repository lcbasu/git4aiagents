import click

from g4a.cli.commands.log import relative_time
from g4a.git_utils import repo_root as get_repo_root
from g4a.query.reader import load_all_records
from g4a.query.search import search_records


@click.command()
@click.argument("term")
def why(term):
    """Show the decision trail for a file, function, or keyword."""
    try:
        root = get_repo_root()
    except RuntimeError:
        click.echo("Error: not inside a git repository.", err=True)
        raise SystemExit(1)

    records = load_all_records(root)
    if not records:
        click.echo("No reasoning data found.")
        return

    results = search_records(records, term)
    if not results:
        click.echo(f'No results for "{term}".')
        click.echo("Try a file name, function name, or keyword from the reasoning.")
        return

    click.echo(f'  Decision trail for "{term}"')
    click.echo(f"  Found in {len(results)} commit{'s' if len(results) != 1 else ''}")
    click.echo("")

    for score, record in results[:10]:
        sha = record.get("commit_sha", "?")[:7]
        ts = relative_time(record.get("timestamp", ""))
        agent = record.get("agent") or record.get("source", "?")
        total = record.get("total_events", 0)
        message = record.get("commit_message", "")
        intent = record.get("intent")
        files_changed = record.get("files_changed", [])
        files_read = record.get("files_read", [])
        tools = record.get("tools_used", [])
        reasoning = record.get("reasoning_summary") or ""

        click.echo(f"  -- {sha} ({ts}, {total} events) " + "-" * 30)
        click.echo(f"  Agent: {agent}")
        click.echo(f"  {message}")

        if intent:
            display = intent[:300].replace("\n", " ")
            click.echo(f"  Intent: {display}")

        if files_changed:
            paths = [fc["path"] for fc in files_changed[:5]]
            extra = f" (+{len(files_changed) - 5} more)" if len(files_changed) > 5 else ""
            click.echo(f"  Files changed: {', '.join(paths)}{extra}")

        if tools:
            click.echo(f"  Tools: {', '.join(tools)}")

        if reasoning:
            # Show first 200 chars of reasoning
            snippet = reasoning[:200].replace("\n", " ").strip()
            if snippet:
                click.echo(f'  Reasoning: "{snippet}..."')

        click.echo("")
