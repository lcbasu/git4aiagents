def search_records(records, term):
    term_lower = term.lower()
    results = []

    for record in records:
        score = 0

        # Check files changed
        for fc in record.get("files_changed", []):
            path = fc.get("path", "").lower()
            if term_lower == path:
                score += 200
            elif term_lower in path:
                score += 100

        # Check files read
        for fr in record.get("files_read", []):
            if term_lower in fr.lower():
                score += 50

        # Check intent
        intent = record.get("intent") or ""
        if term_lower in intent.lower():
            score += 75

        # Check reasoning summary
        summary = record.get("reasoning_summary") or ""
        if term_lower in summary.lower():
            score += 50

        # Check commit message
        message = record.get("commit_message") or ""
        if term_lower in message.lower():
            score += 25

        # Check tools used
        for tool in record.get("tools_used", []):
            if term_lower in tool.lower():
                score += 10

        if score > 0:
            results.append((score, record))

    results.sort(key=lambda x: (-x[0], x[1].get("timestamp", "")))
    return results
