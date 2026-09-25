from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import shutil
import sqlite3
from typing import Iterator

from models.project import LEGACY_ARCHIVED_STATUS, PUBLISHED_STATUS, STATUSES


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            self._backup_before_v31_migration(connection)
            self._backup_before_v32_migration(connection)
            self._backup_before_v33_migration(connection)
            self._backup_before_v34_migration(connection)
            self._backup_before_v35_migration(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    client TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Need Edit',
                    video_type TEXT NOT NULL DEFAULT '',
                    folder_path TEXT NOT NULL,
                    raw_video_path TEXT NOT NULL DEFAULT '',
                    edited_video_path TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    estimated_payment REAL NOT NULL DEFAULT 0,
                    estimated_payment_custom INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_project_columns(connection)
            self._normalize_legacy_statuses(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    default_asset_folder TEXT NOT NULL DEFAULT '',
                    default_export_folder TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS client_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER NOT NULL,
                    pattern TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(client_id, pattern, pattern_type),
                    FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_client_patterns_lookup
                ON client_patterns(pattern_type, pattern)
                """
            )
            self._backfill_clients(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL UNIQUE,
                    normalized_file_path TEXT NOT NULL DEFAULT '',
                    asset_type TEXT NOT NULL DEFAULT 'file',
                    client TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._ensure_asset_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_assets (
                    project_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    linked_path TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (project_id, asset_id),
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_project_asset_columns(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS asset_tags (
                    asset_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (asset_id, tag),
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_assets_name ON assets(name)"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_assets_normalized_file_path
                ON assets(normalized_file_path)
                WHERE normalized_file_path != ''
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_assets_created_at
                ON project_assets(created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_asset_tags_tag
                ON asset_tags(tag)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_app_settings_key
                ON app_settings(key)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS watched_folders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    normalized_path TEXT NOT NULL DEFAULT '',
                    folder_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(normalized_path, folder_type)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    file_path TEXT NOT NULL,
                    normalized_file_path TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL,
                    detected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    version_label TEXT NOT NULL DEFAULT 'Unknown',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    modified_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS project_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """
            )
            self._backfill_status_history(connection)
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    project_id INTEGER,
                    asset_id INTEGER,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
                    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS detected_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL,
                    normalized_file_path TEXT NOT NULL UNIQUE,
                    detection_type TEXT NOT NULL,
                    watched_folder_id INTEGER,
                    project_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (watched_folder_id) REFERENCES watched_folders(id) ON DELETE SET NULL,
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_watched_folders_type
                ON watched_folders(folder_type, enabled)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_project_exports_normalized_path
                ON project_exports(project_id, normalized_file_path)
                WHERE normalized_file_path != ''
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_activity_log_created_at
                ON activity_log(created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_revisions_project
                ON project_revisions(project_id, completed, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_project_status_history_project
                ON project_status_history(project_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_detected_files_status
                ON detected_files(status, detection_type)
                """
            )

    def _ensure_project_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(projects)").fetchall()
        }
        additions = {
            "raw_video_path": "TEXT NOT NULL DEFAULT ''",
            "edited_video_path": "TEXT NOT NULL DEFAULT ''",
            "video_type": "TEXT NOT NULL DEFAULT ''",
            "client_id": "INTEGER",
            "priority": "INTEGER NOT NULL DEFAULT 0",
            "sort_order": "INTEGER NOT NULL DEFAULT 0",
            "estimated_payment": "REAL NOT NULL DEFAULT 0",
            "estimated_payment_custom": "INTEGER NOT NULL DEFAULT 0",
            "earned_amount": "REAL NOT NULL DEFAULT 0",
            "earned_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE projects ADD COLUMN {column} {definition}"
                )

    def _normalize_legacy_statuses(self, connection: sqlite3.Connection) -> None:
        valid_statuses = (*STATUSES, PUBLISHED_STATUS)
        connection.execute(
            """
            UPDATE projects
            SET status = CASE
                WHEN status = 'Ready to Upload' THEN 'Need Upload'
                WHEN status = 'Need Review' THEN 'Need Upload'
                WHEN status = 'Revision' THEN 'Editing'
                WHEN status = 'Uploaded' THEN 'Done'
                WHEN status = ? THEN ?
                WHEN status NOT IN (?, ?, ?, ?, ?) THEN 'Need Edit'
                ELSE status
            END
            """,
            (LEGACY_ARCHIVED_STATUS, PUBLISHED_STATUS, *valid_statuses),
        )

    def _ensure_asset_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(assets)").fetchall()
        }
        if "normalized_file_path" not in existing:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN normalized_file_path TEXT NOT NULL DEFAULT ''"
            )
        if "asset_type" not in existing:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN asset_type TEXT NOT NULL DEFAULT 'file'"
            )

    def _ensure_project_asset_columns(self, connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(project_assets)").fetchall()
        }
        if "linked_path" not in existing:
            connection.execute(
                "ALTER TABLE project_assets ADD COLUMN linked_path TEXT NOT NULL DEFAULT ''"
            )

    def _backup_before_v31_migration(self, connection: sqlite3.Connection) -> None:
        if not self.db_path.exists():
            return
        required_tables = {"watched_folders", "project_exports", "activity_log"}
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        existing = {row["name"] for row in rows}
        if required_tables.issubset(existing):
            return

        backup_dir = self.db_path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"editflow-before-v31-{datetime.now():%Y%m%d-%H%M%S}.db"
        if not backup_path.exists():
            shutil.copy2(self.db_path, backup_path)

    def _backup_before_v32_migration(self, connection: sqlite3.Connection) -> None:
        if not self.db_path.exists():
            return
        required_tables = {"clients", "client_patterns"}
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        existing = {row["name"] for row in rows}
        if required_tables.issubset(existing):
            return

        backup_dir = self.db_path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"editflow-before-v32-{datetime.now():%Y%m%d-%H%M%S}.db"
        if not backup_path.exists():
            shutil.copy2(self.db_path, backup_path)

    def _backup_before_v33_migration(self, connection: sqlite3.Connection) -> None:
        if not self.db_path.exists():
            return
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        existing = {row["name"] for row in rows}
        if "assets" not in existing:
            return
        if "asset_tags" in existing:
            return

        backup_dir = self.db_path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"editflow-before-v33-{datetime.now():%Y%m%d-%H%M%S}.db"
        if not backup_path.exists():
            shutil.copy2(self.db_path, backup_path)

    def _backup_before_v34_migration(self, connection: sqlite3.Connection) -> None:
        if not self.db_path.exists():
            return
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        existing = {row["name"] for row in rows}
        if "projects" not in existing:
            return
        if {"project_revisions", "project_status_history"}.issubset(existing):
            return

        backup_dir = self.db_path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"editflow-before-v34-{datetime.now():%Y%m%d-%H%M%S}.db"
        if not backup_path.exists():
            shutil.copy2(self.db_path, backup_path)

    def _backup_before_v35_migration(self, connection: sqlite3.Connection) -> None:
        if not self.db_path.exists():
            return
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
        existing = {row["name"] for row in rows}
        if "projects" not in existing:
            return

        has_asset_type = False
        if "assets" in existing:
            has_asset_type = any(
                row["name"] == "asset_type"
                for row in connection.execute("PRAGMA table_info(assets)").fetchall()
            )
        has_linked_path = False
        if "project_assets" in existing:
            has_linked_path = any(
                row["name"] == "linked_path"
                for row in connection.execute("PRAGMA table_info(project_assets)").fetchall()
            )
        if "app_settings" in existing and has_asset_type and has_linked_path:
            return

        backup_dir = self.db_path.parent / "migration-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"editflow-before-v35-{datetime.now():%Y%m%d-%H%M%S}.db"
        if not backup_path.exists():
            shutil.copy2(self.db_path, backup_path)

    def _backfill_status_history(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO project_status_history (project_id, status, created_at)
            SELECT
                p.id,
                p.status,
                CASE
                    WHEN p.created_at IS NULL OR p.created_at = ''
                    THEN CURRENT_TIMESTAMP
                    ELSE p.created_at
                END
            FROM projects p
            WHERE NOT EXISTS (
                SELECT 1
                FROM project_status_history h
                WHERE h.project_id = p.id
            )
            """
        )

    def _backfill_clients(self, connection: sqlite3.Connection) -> None:
        client_rows = connection.execute(
            """
            SELECT DISTINCT TRIM(client) AS name
            FROM projects
            WHERE TRIM(client) != ''
            """
        ).fetchall()
        for row in client_rows:
            connection.execute(
                """
                INSERT OR IGNORE INTO clients (name)
                VALUES (?)
                """,
                (row["name"],),
            )

        connection.execute(
            """
            UPDATE projects
            SET client_id = (
                SELECT clients.id
                FROM clients
                WHERE clients.name = projects.client COLLATE NOCASE
                LIMIT 1
            )
            WHERE TRIM(client) != ''
              AND (client_id IS NULL OR client_id = '')
            """
        )
