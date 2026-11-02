"""
Automated System Backup Script.
Creates timestamped archives of configuration, logs, reports, and trade history.
"""

import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path


def create_backup():
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = Path("reports/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    archive_path = backup_dir / f"trading_bot_backup_{timestamp}.tar.gz"

    print(f"Creating backup at {archive_path}...")
    with tarfile.open(archive_path, "w:gz") as tar:
        for folder in ["config", "reports"]:
            p = Path(folder)
            if p.exists():
                tar.add(p, arcname=folder)

    print(f"Backup created successfully: {archive_path} ({archive_path.stat().st_size} bytes)")


if __name__ == "__main__":
    create_backup()
