# backup.py - Runs entirely in GitHub's environment
import sqlite3
import os
from datetime import datetime
from github import Github  # PyGitHub library

# 1. Initialize (GitHub token from secrets)
g = Github(os.getenv('GITHUB_TOKEN'))
repo = g.get_repo("your-username/your-repo")

# 2. Create in-memory backup
timestamp = datetime.now().strftime("%Y%m%d")
backup_content = ""
with sqlite3.connect("students.db") as conn:
    for line in conn.iterdump():
        backup_content += line + "\n"

# 3. Push to GitHub as a new file
repo.create_file(
    path=f"backups/students_{timestamp}.sql",
    message=f"Automated backup {timestamp}",
    content=backup_content
)
