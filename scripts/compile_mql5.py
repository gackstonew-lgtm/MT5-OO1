"""
MQL5 EA Compiler and Verification Script.
Executes MetaEditor64.exe and parses compile log, ensuring 0 errors, 0 warnings.
"""

import os
import subprocess
import sys

EA_PATH = os.path.abspath("mql5/Experts/PythonBridgeEA.mq5")
METAEDITOR_PATH = r"C:\Program Files\MetaTrader 5\MetaEditor64.exe"
COMPILE_LOG = os.path.abspath("compile_final.log")


def main():
    if not os.path.exists(EA_PATH):
        print(f"Error: {EA_PATH} not found.")
        return 1

    if not os.path.exists(METAEDITOR_PATH):
        print(f"Error: {METAEDITOR_PATH} not found.")
        return 1

    if os.path.exists(COMPILE_LOG):
        try:
            os.remove(COMPILE_LOG)
        except Exception:
            pass

    cmd = [
        METAEDITOR_PATH,
        f'/compile:{EA_PATH}',
        f'/log:{COMPILE_LOG}'
    ]
    # MetaEditor requires execution via shell on Windows when arguments contain spaces
    cmd_str = f'"{METAEDITOR_PATH}" /compile:"{EA_PATH}" /log:"{COMPILE_LOG}"'
    print(f"Executing: {cmd_str}")
    subprocess.run(cmd_str, shell=True)

    if not os.path.exists(COMPILE_LOG):
        print("Error: Compile log not produced.")
        return 1

    log_content = ""
    for enc in ["utf-16", "utf-8", "cp1252"]:
        try:
            with open(COMPILE_LOG, "r", encoding=enc) as f:
                log_content = f.read()
            if log_content:
                break
        except Exception:
            continue

    print("=" * 60)
    print("METAEDITOR COMPILATION LOG OUTPUT")
    print("=" * 60)
    for line in log_content.splitlines():
        if line.strip():
            print(line.strip())
    print("=" * 60)

    if "0 errors, 0 warnings" in log_content:
        print("VERIFICATION RESULT: PASS (0 errors, 0 warnings)")
        return 0
    else:
        print("VERIFICATION RESULT: FAIL")
        return 1


if __name__ == "__main__":
    sys.exit(main())
