import subprocess
import re

def main():
    proc = subprocess.run(['git', 'diff', '--cached'], capture_output=True, text=True, errors='ignore')
    diff_text = proc.stdout

    token_pat = re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b')
    tg_url_pat = re.compile(r'api\.telegram\.org/bot[^/\s]+')
    key_pat = re.compile(r'(?:api_key|secret_key|private_key|password)\s*=\s*[\"\'][A-Za-z0-9_\-\.\$\@\#\!]{8,}[\"\']', re.IGNORECASE)

    issues = []
    for idx, line in enumerate(diff_text.splitlines(), 1):
        if line.startswith('+') and not line.startswith('+++'):
            if token_pat.search(line):
                issues.append((idx, 'Telegram Token'))
            if tg_url_pat.search(line):
                if '%s' not in line and '{' not in line:
                    issues.append((idx, 'Hardcoded Telegram Bot URL'))
            if key_pat.search(line):
                issues.append((idx, 'Potential Key/Password'))

    print("=== STAGED CHANGES SECRET SCAN ===")
    if not issues:
        print("SUCCESS: git diff --cached is 100% CLEAN of secrets, tokens, and passwords!")
        return 0
    else:
        print(f"CRITICAL: Found {len(issues)} secrets in staged diff:")
        for line_num, label in issues:
            print(f"  Line {line_num}: [{label}]")
        return 1

if __name__ == '__main__':
    exit(main())
