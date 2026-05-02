import sqlite3
import os

db = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend', 'outputs', 'db', 'schedule.db')
print('DB:', db, 'exists:', os.path.exists(db))
c = sqlite3.connect(db)
tables = c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
print('Tables:', tables)
rows = c.execute("SELECT base_mfg_notes FROM assemblies WHERE base_mfg_notes IS NOT NULL LIMIT 5").fetchall()
print(f'Found {len(rows)} rows with notes')
for r in rows:
    print(repr(r[0]))
    print('---')
c.close()
