import json


def search_records(records, term):
    term_lower = term.lower()
    results = []

    for record in records:
        score = 0

        for fc in record.get("files_changed", []):
            path = fc.get("path", "").lower()
            if term_lower == path:
                score += 200
            elif term_lower in path:
                score += 100

        for fr in record.get("files_read", []):
            if term_lower in fr.lower():
                score += 50

        for fw in record.get("files_written", []):
            if term_lower in fw.lower():
                score += 50

        intent = record.get("intent") or ""
        if term_lower in intent.lower():
            score += 75

        exploration = record.get("exploration") or ""
        if term_lower in exploration.lower():
            score += 50

        message = record.get("commit_message") or ""
        if term_lower in message.lower():
            score += 25

        for prompt in record.get("user_prompts", []):
            if term_lower in prompt.lower():
                score += 80
                break

        for cmd in record.get("commands_run", []):
            if term_lower in cmd.lower():
                score += 30
                break

        # Search reasoning chain
        chain = record.get("reasoning_chain", [])
        chain_text = json.dumps(chain).lower()
        if term_lower in chain_text:
            score += 40

        if score > 0:
            results.append((score, record))

    results.sort(key=lambda x: (-x[0], x[1].get("timestamp", "")))
    return results
