import json
import subprocess
import tempfile


def run_git(*args, repo=None, stdin_data=None):
    cmd = ["git"]
    if repo:
        cmd += ["-C", repo]
    cmd += list(args)
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        input=stdin_data,
    )
    return result


def write_note(ref, sha, data_dict, repo=None):
    payload = json.dumps(data_dict, indent=2, default=str)
    # For large payloads, use blob approach
    if len(payload) > 4000:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(payload)
            f.flush()
            blob_result = run_git("hash-object", "-w", f.name, repo=repo)
            if blob_result.returncode != 0:
                raise RuntimeError(f"hash-object failed: {blob_result.stderr}")
            blob_sha = blob_result.stdout.strip()
            result = run_git("notes", "--ref", ref, "add", "-f", "-C", blob_sha, sha, repo=repo)
        import os
        os.unlink(f.name)
    else:
        result = run_git("notes", "--ref", ref, "add", "-f", "-m", payload, sha, repo=repo)

    if result.returncode != 0:
        raise RuntimeError(f"notes add failed: {result.stderr}")


def read_note(ref, sha, repo=None):
    result = run_git("notes", "--ref", ref, "show", sha, repo=repo)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def list_notes(ref, repo=None):
    result = run_git("notes", "--ref", ref, "list", repo=repo)
    if result.returncode != 0:
        return []
    entries = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split()
        if len(parts) >= 2:
            entries.append({"note_sha": parts[0], "commit_sha": parts[1]})
    return entries


def list_notes_refs(prefix, repo=None):
    result = run_git("for-each-ref", "--format=%(refname)", f"refs/notes/{prefix}/", repo=repo)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
