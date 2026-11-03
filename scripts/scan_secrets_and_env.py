import os
import re

ENV_VAR_PATTERN = re.compile(r'(?:os\.getenv|os\.environ\.get|os\.environ\[)\([\"\']([A-Za-z0-9_]+)[\"\']')

SECRET_PATTERNS = [
    ("Telegram Bot Token", re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')),
    ("Telegram Bot URL", re.compile(r'https?://api\.telegram\.org/bot[^/\s]+')),
    ("Generic Password/Secret Key Assignment", re.compile(r'(?:api_key|secret_key|private_key|password)\s*=\s*[\"\'][A-Za-z0-9_\-\.\$\@\#\!]{8,}[\"\']', re.IGNORECASE)),
]

IGNORED_DIRS = {'.git', '__pycache__', '.venv', 'venv', '.idea', '.vscode', 'historical'}
# .env is excluded from git, but we want to scan all files that could be staged
EXCLUDED_EXTENSIONS = {'.ex5', '.png', '.jpg', '.jpeg', '.tar.gz', '.zip', '.pyc', '.wav'}

def scan():
    print("=== SCANNING FOR ENVIRONMENT VARIABLES ===")
    env_vars = set()
    secrets_found = []

    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext in EXCLUDED_EXTENSIONS:
                continue
            path = os.path.join(root, f)
            rel_path = os.path.relpath(path, '.')

            # Skip .env since it's gitignored and holds local secrets
            if rel_path == '.env' or rel_path.startswith('.env.'):
                continue
            # Skip logs
            if rel_path.startswith('logs') or rel_path.endswith('.log'):
                continue
            # Skip this scan script itself
            if f == 'scan_secrets_and_env.py':
                continue

            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as fp:
                    for line_no, line in enumerate(fp, start=1):
                        for m in ENV_VAR_PATTERN.finditer(line):
                            env_vars.add(m.group(1))

                        for label, pat in SECRET_PATTERNS:
                            for m in pat.finditer(line):
                                # Check if it's a template or example
                                val = m.group(0)
                                if "your_token" in val.lower() or "example" in val.lower() or "xxx" in val.lower() or "<" in val:
                                    continue
                                secrets_found.append((rel_path, line_no, label))
            except Exception as e:
                print(f"Error reading {rel_path}: {e}")

    print("Detected Environment Variables:")
    for v in sorted(env_vars):
        print(f"  - {v}")

    print("\n=== SCANNING FOR SECRETS IN TRACKABLE FILES ===")
    if not secrets_found:
        print("SUCCESS: 0 secrets found in trackable working tree files!")
    else:
        print(f"WARNING: Found {len(secrets_found)} secret(s) in working tree:")
        for path, line_no, label in secrets_found:
            print(f"  - {path}:{line_no} [{label}]")

if __name__ == '__main__':
    scan()
