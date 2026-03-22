import os

import click

from g4a.capture.engine import run_capture
from g4a.git_utils import repo_root as get_repo_root


@click.command()
@click.argument("sha")
@click.option("--repo", default=".", help="Repository root path")
def capture(sha, repo):
    """Capture reasoning for a commit (called by post-commit hook)."""
    try:
        root = os.path.abspath(repo)
        run_capture(sha, root)
    except Exception as e:
        # Never crash - log errors to .git/g4a/errors.log
        errors_path = os.path.join(root, ".git", "g4a", "errors.log")
        try:
            with open(errors_path, "a") as f:
                import time
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ERROR capture {sha}: {e}\n")
        except Exception:
            pass
