import json
import os
import stat
import sys
import time

import click

from g4a.git_utils import repo_root, run_git, run_git_ok, generate_client_id, find_sub_repos

POST_COMMIT_MARKER_START = "# --- g4a start ---"
POST_COMMIT_MARKER_END = "# --- g4a end ---"

POST_COMMIT_HOOK = """
{marker_start}
# g4a reasoning capture hook
# Returns immediately. All work happens in a background process.
[ "$G4A_DISABLE" = "1" ] && exit 0
if [ "$GIT_REFLOG_ACTION" = "commit (amend)" ] || \\
   echo "$GIT_REFLOG_ACTION" | grep -q "rebase"; then
  exit 0
fi
SHA=$(git rev-parse HEAD 2>/dev/null) || exit 0
REPO=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
[ -f "$REPO/.g4a/config.json" ] || exit 0
(
  exec </dev/null >/dev/null 2>/dev/null
  "{python}" -m g4a capture "$SHA" --repo "$REPO" \\
    2>> "$REPO/.git/g4a/errors.log" || true
) &
exit 0
{marker_end}
""".strip()


def init_single_repo(root, quiet=False):
    """Initialize g4a in a single repo. Returns the client_id."""
    g4a_dir = os.path.join(root, ".g4a")
    git_g4a_dir = os.path.join(root, ".git", "g4a")
    hooks_dir = os.path.join(root, ".git", "hooks")

    # 1. Create .g4a/ with config
    os.makedirs(g4a_dir, exist_ok=True)
    config_path = os.path.join(g4a_dir, "config.json")
    if not os.path.exists(config_path):
        with open(config_path, "w") as f:
            json.dump({
                "version": "1.0",
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }, f, indent=2)

    # 2. Create .git/g4a/ with client_id and watermark
    os.makedirs(git_g4a_dir, exist_ok=True)

    client_id_path = os.path.join(git_g4a_dir, "client_id")
    if not os.path.exists(client_id_path):
        client_id = generate_client_id()
        with open(client_id_path, "w") as f:
            f.write(client_id)
    else:
        with open(client_id_path) as f:
            client_id = f.read().strip()

    watermark_path = os.path.join(git_g4a_dir, "last_capture_mtime")
    if not os.path.exists(watermark_path):
        with open(watermark_path, "w") as f:
            f.write(str(time.time()))

    # 3. Install post-commit hook
    os.makedirs(hooks_dir, exist_ok=True)
    hook_path = os.path.join(hooks_dir, "post-commit")
    python_path = sys.executable

    hook_block = POST_COMMIT_HOOK.format(
        marker_start=POST_COMMIT_MARKER_START,
        marker_end=POST_COMMIT_MARKER_END,
        python=python_path,
    )

    hook_existed = False
    if os.path.exists(hook_path):
        with open(hook_path) as f:
            existing = f.read()
        if POST_COMMIT_MARKER_START in existing:
            hook_existed = True
            if not quiet:
                click.echo("  post-commit hook already has g4a block.")
        else:
            with open(hook_path, "a") as f:
                f.write("\n" + hook_block + "\n")
    else:
        with open(hook_path, "w") as f:
            f.write("#!/bin/sh\n" + hook_block + "\n")

    # Make executable
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # 4. Configure git notes fetch
    existing_fetch = run_git_ok("config", "--get-all", "remote.origin.fetch", repo=root) or ""
    if "g4a-commits" not in existing_fetch:
        run_git_ok("config", "--add", "remote.origin.fetch",
                   "+refs/notes/g4a-commits/*:refs/notes/g4a-commits/*", repo=root)
        run_git_ok("config", "--add", "remote.origin.fetch",
                   "+refs/notes/g4a-sessions/*:refs/notes/g4a-sessions/*", repo=root)

    return client_id


@click.command()
def init():
    """Initialize g4a in the current repo and any sub-repos."""
    try:
        root = repo_root()
    except RuntimeError:
        click.echo("Error: not inside a git repository.", err=True)
        raise SystemExit(1)

    client_id = init_single_repo(root)

    # Print summary for root repo
    click.echo("")
    click.echo("  g4a initialized.")
    click.echo("")
    click.echo("  Installed:")
    click.echo(f"    .g4a/config.json           configuration")
    click.echo(f"    .git/g4a/client_id         {client_id}")
    click.echo(f"    .git/hooks/post-commit     reasoning capture hook")
    click.echo(f"    git fetch config           notes auto-fetch enabled")

    # Always scan for sub-repos
    sub_repos = find_sub_repos(root)
    if sub_repos:
        click.echo("")
        click.echo(f"  Sub-repos ({len(sub_repos)}):")
        for sub in sub_repos:
            rel = os.path.relpath(sub, root)
            sub_client_id = init_single_repo(sub, quiet=True)
            click.echo(f"    {rel}/  ready (client_id: {sub_client_id})")

    click.echo("")
    click.echo("  How it works:")
    click.echo("    1. Use your AI coding agent normally (Claude Code, etc.)")
    click.echo("    2. Make a commit - reasoning is captured automatically")
    click.echo("    3. Wait 3 seconds for background capture to finish")
    click.echo("")
    click.echo("  Commands to try:")
    click.echo("")
    click.echo("    g4a log                    Show full reasoning for all commits")
    click.echo("                               (every step the AI agent took)")
    click.echo("")
    click.echo("    g4a log --short            Show compact summary view")
    click.echo("")
    click.echo("    g4a why <term>             Search the decision trail")
    click.echo("                               (shows full reasoning chain)")
    click.echo("                               e.g. g4a why auth")
    click.echo("                                    g4a why payment.py")
    click.echo("                                    g4a why \"database migration\"")
    click.echo("")
    click.echo("    g4a why <term> --short     Search with summary only")
    click.echo("")
    click.echo("    g4a capture <sha>          Manually capture a specific commit")
    click.echo("                               (normally runs automatically via hook)")
    click.echo("")
    click.echo("")
