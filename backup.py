import sqlite3
import os
from datetime import datetime

# 1. Create a timestamped backup
timestamp = datetime.now().strftime("%Y%m%d_%H%M")
backup_file = f"backups/students_{timestamp}.db"
os.makedirs("backups", exist_ok=True)

# 2. Safely copy the database (avoid corruption)
with sqlite3.connect('students.db') as src:
    with sqlite3.connect(backup_file) as dst:
        src.backup(dst)

print(f"Backup created: {backup_file}")

# 3. Optional: Upload to cloud storage (see next section)