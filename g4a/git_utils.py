import hashlib
import platform
import subprocess


def run_git(*args, repo=None):
    cmd = ["git"]
    if repo:
        cmd += ["-C", repo]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def run_git_ok(*args, repo=None):
    try:
        return run_git(*args, repo=repo)
    except RuntimeError:
        return None


def repo_root(path="."):
    return run_git("rev-parse", "--show-toplevel", repo=path)


def generate_client_id():
    email = run_git_ok("config", "user.email") or "unknown"
    hostname = platform.node()
    raw = f"{email}@{hostname}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def find_sub_repos(root):
    """Find nested git repos inside root that are gitignored."""
    import os
    sub_repos = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip the root's own .git and common non-repo dirs
        rel = os.path.relpath(dirpath, root)
        if rel == ".":
            dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules", "__pycache__")]
            continue
        if ".git" in dirnames:
            # Check if this directory is gitignored by the root repo
            check = run_git_ok("check-ignore", "-q", rel, repo=root)
            # check-ignore -q returns 0 if ignored, non-zero if not
            is_ignored = check is not None
            if is_ignored:
                sub_repos.append(os.path.join(root, rel))
            # Don't descend into this sub-repo's tree
            dirnames[:] = []
        else:
            dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", "node_modules", "__pycache__")]
    return sub_repos


def find_parent_repo(repo_path):
    """Find the parent git repo that contains this repo, if any."""
    import os
    parent = os.path.dirname(os.path.abspath(repo_path))
    while parent != os.path.dirname(parent):  # stop at filesystem root
        if os.path.isdir(os.path.join(parent, ".git")):
            return parent
        parent = os.path.dirname(parent)
    return None
