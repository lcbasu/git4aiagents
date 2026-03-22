from g4a.storage.notes import list_notes_refs, list_notes, read_note


def load_all_records(repo_root):
    records = []
    seen_shas = set()

    refs = list_notes_refs("g4a-commits", repo=repo_root)
    for ref in refs:
        # Strip refs/notes/ prefix to get the short ref for git notes commands
        short_ref = ref.replace("refs/notes/", "")
        entries = list_notes(short_ref, repo=repo_root)
        for entry in entries:
            sha = entry["commit_sha"]
            if sha in seen_shas:
                continue
            record = read_note(short_ref, sha, repo=repo_root)
            if record:
                seen_shas.add(sha)
                records.append(record)

    records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return records


def load_record(repo_root, commit_sha):
    refs = list_notes_refs("g4a-commits", repo=repo_root)
    for ref in refs:
        short_ref = ref.replace("refs/notes/", "")
        record = read_note(short_ref, commit_sha, repo=repo_root)
        if record:
            return record
    return None
