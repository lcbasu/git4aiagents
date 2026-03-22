import os
import re
from pathlib import Path

PATTERNS = [
    (r'AKIA[0-9A-Z]{16}', "[MASKED:AWS_KEY]"),
    (r'ASIA[0-9A-Z]{16}', "[MASKED:AWS_TEMP_KEY]"),
    (r'sk-ant-[a-zA-Z0-9\-]{20,}', "[MASKED:ANTHROPIC_KEY]"),
    (r'sk-proj-[a-zA-Z0-9\-]{20,}', "[MASKED:OPENAI_KEY]"),
    (r'sk-[a-zA-Z0-9]{20,}', "[MASKED:API_KEY]"),
    (r'ghp_[a-zA-Z0-9]{36}', "[MASKED:GITHUB_PAT]"),
    (r'ghs_[a-zA-Z0-9]{36}', "[MASKED:GITHUB_APP_TOKEN]"),
    (r'gho_[a-zA-Z0-9]{36}', "[MASKED:GITHUB_OAUTH]"),
    (r'glpat-[a-zA-Z0-9\-]{20,}', "[MASKED:GITLAB_PAT]"),
    (r'xoxb-[0-9]{10,}-[a-zA-Z0-9]{20,}', "[MASKED:SLACK_TOKEN]"),
    (r'xoxp-[0-9]{10,}-[a-zA-Z0-9]{20,}', "[MASKED:SLACK_TOKEN]"),
    (r'-----BEGIN[A-Z ]*PRIVATE KEY-----', "[MASKED:PRIVATE_KEY]"),
    (r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}', "[MASKED:JWT]"),
    (r'(?i)Bearer\s+[a-zA-Z0-9\-._~+/]+=*', "Bearer [MASKED:TOKEN]"),
    (r'(?i)(password|passwd|pwd|secret|token|api_key|apikey)\s*[=:]\s*["\']?([^"\'\s,}\]]{4,})',
     r'\1=[MASKED:VALUE]'),
    (r'(?i)(mongodb|postgres|mysql|redis)(\+srv)?://[^@\s]+@', r'\1\2://[MASKED:CREDS]@'),
]

COMPILED = [(re.compile(p), r) for p, r in PATTERNS]


def mask_secrets(text, repo_root=None):
    if not text:
        return text

    for pattern, replacement in COMPILED:
        text = pattern.sub(replacement, text)

    if repo_root:
        repo = str(Path(repo_root).resolve())
        home = str(Path.home())
        text = text.replace(repo + os.sep, "")
        text = text.replace(repo, ".")
        text = text.replace(home + os.sep, "~/")
        text = text.replace(home, "~")

    return text


def mask_dict(d, repo_root=None):
    if isinstance(d, str):
        return mask_secrets(d, repo_root)
    if isinstance(d, dict):
        return {k: mask_dict(v, repo_root) for k, v in d.items()}
    if isinstance(d, list):
        return [mask_dict(v, repo_root) for v in d]
    return d
