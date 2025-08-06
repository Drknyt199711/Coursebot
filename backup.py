# backup.py - Place in your repo root
import sqlite3
import os
from datetime import datetime

# 1. Configure Paths
DB_PATH = "/opt/render/project/src/students.db"
BACKUP_DIR = os.getenv("BACKUP_DIR")
os.makedirs(BACKUP_DIR, exist_ok=True)

# 2. Create SQL Dump (Text Format)
timestamp = datetime.now().strftime("%Y%m%d_%H%M")
backup_file = f"{BACKUP_DIR}students_{timestamp}.sql"

with sqlite3.connect(DB_PATH) as conn:
    with open(backup_file, 'w') as f:
        for line in conn.iterdump():  # Convert DB to SQL
            f.write(f"{line}\n")

# 3. Push to GitHub
os.system(f"""
cd /opt/render/project/src &&
git config --global user.email "bot@render.com" &&
git config --global user.name "Render Bot" &&
git add backups/ &&
git commit -m "Automatic backup {timestamp}" &&
git push https://{os.getenv('GITHUB_TOKEN')}@github.com/your-username/your-repo.git main
""")

print(f"✅ Backup saved to {backup_file}")
