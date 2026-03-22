import click

from g4a.cli.commands.log import relative_time, render_chain
from g4a.git_utils import repo_root as get_repo_root
from g4a.query.reader import load_all_records
from g4a.query.search import search_records


@click.command()
@click.argument("term")
@click.option("--short", is_flag=True, help="Show compact summary only (no reasoning chain)")
def why(term, short):
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
        click.echo("Try a file name, function name, or keyword.")
        return

    click.echo(f'\n  Decision trail for "{term}"')
    click.echo(f"  Found in {len(results)} commit{'s' if len(results) != 1 else ''}")

    for score, record in results[:10]:
        sha = record.get("commit_sha", "?")[:7]
        ts = relative_time(record.get("timestamp", ""))
        agent = record.get("agent") or record.get("source", "?")
        total = record.get("total_events", 0)
        message = record.get("commit_message", "")
        intent = record.get("intent")
        exploration = record.get("exploration")
        files_changed = record.get("files_changed", [])
        files_written = record.get("files_written", [])
        tools = record.get("tools_used", [])
        user_prompts = record.get("user_prompts", [])
        commands = record.get("commands_run", [])
        chain = record.get("reasoning_chain", [])

        click.echo(f"\n  -- {sha} ({ts}, {agent}, {total} events) " + "-" * 20)
        click.echo(f"  {message}")

        if user_prompts:
            click.echo(f"  User: \"{user_prompts[0][:200]}\"")

        if intent:
            for line in intent.split("\n")[:3]:
                click.echo(f"  {line[:200]}")

        if exploration:
            click.echo(f"  {exploration}")

        if files_changed:
            paths = [fc["path"] for fc in files_changed[:5]]
            extra = f" (+{len(files_changed) - 5} more)" if len(files_changed) > 5 else ""
            click.echo(f"  Changed: {', '.join(paths)}{extra}")

        if files_written:
            click.echo(f"  Wrote: {', '.join(files_written[:5])}" +
                       (f" (+{len(files_written) - 5} more)" if len(files_written) > 5 else ""))

        if commands:
            click.echo(f"  Commands ({len(commands)}):")
            for cmd in commands[:3]:
                click.echo(f"    $ {cmd[:120]}")

        if tools:
            click.echo(f"  Tools: {', '.join(tools)}")

        # Full reasoning chain (default, unless --short)
        if not short and chain:
            click.echo("")
            click.echo("  Reasoning chain:")
            render_chain(chain)

    click.echo("")
