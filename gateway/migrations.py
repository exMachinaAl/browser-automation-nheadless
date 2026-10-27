"""Additive SQLite migrations with consistent online backup before schema changes."""
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
SCHEMA_VERSION = 3


def migrate(db: sqlite3.Connection, path: Path, backup_existing=True):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError('database_schema_newer_than_code')
    if version == SCHEMA_VERSION:
        return None
    backup = None
    if backup_existing and path.exists() and path.stat().st_size > 0:
        folder = path.parent / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = folder / ('pre-schema-v3-' + stamp + '.sqlite3')
        n=1
        while backup.exists():
            backup=folder / ('pre-schema-v3-' + stamp + '-' + str(n) + '.sqlite3')
            n+=1
        snapshot=sqlite3.connect(str(backup))
        try: db.backup(snapshot)
        finally: snapshot.close()
        if os.name!='nt': os.chmod(backup,0o600)
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS site_profiles (
            origin TEXT PRIMARY KEY,name TEXT NOT NULL,selectors TEXT NOT NULL,updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,action TEXT NOT NULL,status TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,result TEXT,error TEXT)''')
        db.execute("UPDATE jobs SET status='interrupted',error='gateway_restarted',updated_at=datetime('now') WHERE status IN ('queued','running')")
        db.execute('''CREATE TABLE IF NOT EXISTS map_evidence (
            map_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, metadata TEXT NOT NULL,
            updated_at TEXT NOT NULL, FOREIGN KEY(map_id) REFERENCES maps(id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS map_revisions (
            map_id TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
            changed_at TEXT NOT NULL, PRIMARY KEY(map_id,revision))''')
        db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
    return str(backup) if backup else None
