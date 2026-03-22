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
