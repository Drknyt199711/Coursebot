# Updated backup.py
import sqlite3
import os
from datetime import datetime

# 1. Use Render's absolute paths
DB_PATH = "/opt/render/project/src/students.db"
BACKUP_DIR = "/opt/render/project/src/backups/"

# 2. Force-create backup directory
os.makedirs(BACKUP_DIR, exist_ok=True)

# 3. Create human-readable SQL dump
timestamp = datetime.now().strftime("%Y%m%d")
backup_path = f"{BACKUP_DIR}students_{timestamp}.sql"

with sqlite3.connect(DB_PATH) as conn:
    with open(backup_path, 'w') as f:
        for line in conn.iterdump():
            f.write(f"{line}\n")

print(f"Backup created at {backup_path}")
