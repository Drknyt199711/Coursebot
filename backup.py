# backup.py - Run this daily via Render Cron Job
import sqlite3
import os
from datetime import datetime

# 1. Define Paths (Render-specific)
DB_PATH = "/opt/render/project/src/students.db"
BACKUP_DIR = "/opt/render/project/src/backups/"
os.makedirs(BACKUP_DIR, exist_ok=True)

# 2. Create SQL Dump (Text format - safe for Git)
timestamp = datetime.now().strftime("%Y%m%d")
backup_file = f"{BACKUP_DIR}students_{timestamp}.sql"

with sqlite3.connect(DB_PATH) as conn:
    with open(backup_file, 'w') as f:
        for line in conn.iterdump():  # Convert DB to SQL commands
            f.write(f"{line}\n")

print(f"Backup saved to {backup_file}")

# 3. Push to GitHub (No SSH needed)
os.system(f"""
    cd /opt/render/project/src &&
    git config --global user.email "bot@render.com" &&
    git config --global user.name "Render Bot" &&
    git add backups/ &&
    git commit -m "Daily backup {timestamp}" &&
    git push https://{YOUR_GITHUB_TOKEN}@github.com/your-username/your-repo.git main
""")
