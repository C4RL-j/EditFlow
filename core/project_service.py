from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import re
import shutil
import sqlite3

from core.database import Database
from core.file_manager import (
    LINKED_ASSETS_FOLDER_NAME,
    backup_database,
    copy_asset_file_into_library,
    create_project_asset_link,
    default_asset_library_root,
    default_projects_root,
    detect_version_label,
    ensure_asset_library_root,
    ensure_linked_assets_folder,
    ensure_mp4,
    find_unassigned_mp4s,
    import_edited_video,
    import_raw_video,
    infer_asset_category,
    list_mp4_files,
    move_asset_folder_into_library,
    normalize_file_path,
    remove_project_asset_link,
    safe_child_path,
    safe_file_size,
    safe_modified_at,
    transfer_file_into_folder,
)
from models.project import (
    ActivityEntry,
    Asset,
    AssetSuggestion,
    Client,
    ClientPattern,
    ClientSuggestion,
    ClientSummary,
    DetectionEntry,
    FOLDER_TYPE_LABELS,
    FileIssue,
    Project,
    ProjectExport,
    ProjectRevision,
    ALL_STATUSES,
    PUBLISHED_STATUS,
    STATUSES,
    TimelineEntry,
    WatchedFolder,
    WorkflowSuggestion,
)


class ProjectService:
    DEFAULT_EARNING_RATE = 2.50
    EARNING_RATE_KEYS = {
        "ugc": "earning_rate_ugc",
        "personal_brand": "earning_rate_personal_brand",
    }

    def __init__(self, database: Database) -> None:
        self.database = database
        self._file_warnings: list[str] = []

    def consume_file_warnings(self) -> list[str]:
        warnings = list(self._file_warnings)
        self._file_warnings.clear()
        return warnings

    def get_setting(self, key: str, default: str = "") -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM app_settings
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        return row["value"] if row is not None else default

    def set_setting(self, key: str, value: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def projects_root(self) -> Path:
        value = self.get_setting("projects_root", "")
        root = Path(value) if value else default_projects_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / PUBLISHED_STATUS).mkdir(parents=True, exist_ok=True)
        return root

    def asset_library_root(self) -> Path:
        value = self.get_setting("asset_library_root", "")
        root = Path(value) if value else default_asset_library_root()
        return ensure_asset_library_root(root)

    def raw_file_mode(self) -> str:
        return self._file_mode_setting("raw_file_mode")

    def edited_file_mode(self) -> str:
        return self._file_mode_setting("edited_file_mode")

    def earning_rates(self) -> dict[str, float]:
        return {
            "ugc": self._earning_rate_setting("ugc"),
            "personal_brand": self._earning_rate_setting("personal_brand"),
        }

    def update_earning_rates(
        self,
        *,
        ugc_rate: float | None = None,
        personal_brand_rate: float | None = None,
    ) -> None:
        if ugc_rate is not None:
            self.set_setting(
                self.EARNING_RATE_KEYS["ugc"],
                f"{max(0.0, float(ugc_rate)):.2f}",
            )
        if personal_brand_rate is not None:
            self.set_setting(
                self.EARNING_RATE_KEYS["personal_brand"],
                f"{max(0.0, float(personal_brand_rate)):.2f}",
            )

    def estimated_payment_for_video_type(self, video_type: str) -> float:
        key = _video_type_key(video_type)
        if key not in self.EARNING_RATE_KEYS:
            return 0.0
        return self._earning_rate_setting(key)

    def flow_project_id(self) -> int | None:
        value = self.get_setting("flow_project_id", "")
        try:
            project_id = int(value)
        except ValueError:
            return None
        return project_id if project_id > 0 else None

    def set_flow_project(self, project: Project) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before Flow Mode can be updated.")
        self.set_setting("flow_project_id", str(project.id))
        updated = self._require_project(project.id)
        self.log_activity(
            "project_flow_enabled",
            f"{updated.name} entered Flow Mode",
            project_id=updated.id,
        )
        return updated

    def clear_flow_project(self, project: Project | None = None) -> Project | None:
        active_id = self.flow_project_id()
        self.set_setting("flow_project_id", "")
        if project is None:
            if active_id is None:
                return None
            project = self.get_project(active_id)
        if project is not None:
            updated = self.get_project(project.id or 0) if project.id is not None else project
            project = updated or project
            self.log_activity(
                "project_flow_disabled",
                f"{project.name} exited Flow Mode",
                project_id=project.id,
            )
        return project

    def update_file_workflow_settings(
        self,
        *,
        projects_root: Path | None = None,
        asset_library_root: Path | None = None,
        raw_file_mode: str | None = None,
        edited_file_mode: str | None = None,
    ) -> None:
        if projects_root is not None:
            root = projects_root.expanduser()
            root.mkdir(parents=True, exist_ok=True)
            self.set_setting("projects_root", str(root))
        if asset_library_root is not None:
            root = ensure_asset_library_root(asset_library_root)
            self.set_setting("asset_library_root", str(root))
        if raw_file_mode is not None:
            self._set_file_mode_setting("raw_file_mode", raw_file_mode)
        if edited_file_mode is not None:
            self._set_file_mode_setting("edited_file_mode", edited_file_mode)

    def list_projects(
        self,
        search: str = "",
        *,
        client: str = "",
        status: str = "",
        include_published: bool = False,
        published_only: bool = False,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[Project]:
        search = search.strip()
        client = client.strip()
        status = status.strip()
        include_published = include_published or include_archived
        published_only = published_only or archived_only
        parameters: list[object] = []
        conditions: list[str] = []

        if search:
            pattern = f"%{search}%"
            conditions.append(
                """
                (
                    p.name LIKE ?
                    OR p.client LIKE ?
                    OR p.notes LIKE ?
                    OR p.raw_video_path LIKE ?
                    OR p.edited_video_path LIKE ?
                )
                """
            )
            parameters.extend([pattern, pattern, pattern, pattern, pattern])
        if client:
            conditions.append("p.client = ?")
            parameters.append(client)
        if status in ALL_STATUSES:
            conditions.append("p.status = ?")
            parameters.append(status)
        if published_only:
            conditions.append("p.status = ?")
            parameters.append(PUBLISHED_STATUS)
        elif not include_published:
            conditions.append("p.status != ?")
            parameters.append(PUBLISHED_STATUS)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        flow_project_id = self.flow_project_id() or 0

        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    p.*,
                    CASE WHEN p.id = ? THEN 1 ELSE 0 END AS in_flow,
                    COUNT(DISTINCT pa.asset_id) AS asset_count,
                    COUNT(
                        DISTINCT CASE
                            WHEN pr.completed = 0 THEN pr.id
                        END
                    ) AS open_revision_count
                FROM projects p
                LEFT JOIN project_assets pa ON pa.project_id = p.id
                LEFT JOIN project_revisions pr ON pr.project_id = p.id
                {where}
                GROUP BY p.id
                ORDER BY p.priority DESC, p.sort_order ASC, p.updated_at DESC, p.id DESC
                """,
                (flow_project_id, *parameters),
            ).fetchall()
        return [self._decorate_project_payment(Project.from_row(row)) for row in rows]

    def list_published_projects(self, search: str = "") -> list[Project]:
        return self.list_projects(search, published_only=True)

    def list_archived_projects(self, search: str = "") -> list[Project]:
        return self.list_published_projects(search)

    def list_clients(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT name
                FROM clients
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [row["name"] for row in rows]

    def list_project_clients(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT TRIM(client) AS name
                FROM projects
                WHERE TRIM(client) != ''
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [row["name"] for row in rows]

    def list_client_records(self) -> list[Client]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM clients
                ORDER BY name COLLATE NOCASE
                """
            ).fetchall()
        return [Client.from_row(row) for row in rows]

    def get_client(self, client_id: int) -> Client | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM clients WHERE id = ?",
                (client_id,),
            ).fetchone()
        return Client.from_row(row) if row else None

    def get_client_by_name(self, name: str) -> Client | None:
        client_name = name.strip()
        if not client_name:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM clients
                WHERE name = ? COLLATE NOCASE
                """,
                (client_name,),
            ).fetchone()
        return Client.from_row(row) if row else None

    def ensure_client(self, name: str) -> Client | None:
        client_name = name.strip()
        if not client_name:
            return None
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO clients (name)
                VALUES (?)
                """,
                (client_name,),
            )
            row = connection.execute(
                """
                SELECT *
                FROM clients
                WHERE name = ? COLLATE NOCASE
                """,
                (client_name,),
            ).fetchone()
        return Client.from_row(row) if row else None

    def update_client_defaults(
        self,
        client_id: int,
        *,
        default_asset_folder: Path | None = None,
        default_export_folder: Path | None = None,
    ) -> Client:
        existing = self.get_client(client_id)
        if existing is None:
            raise LookupError(f"Client not found: {client_id}")
        asset_value = (
            str(default_asset_folder)
            if default_asset_folder is not None
            else str(existing.default_asset_folder or "")
        )
        export_value = (
            str(default_export_folder)
            if default_export_folder is not None
            else str(existing.default_export_folder or "")
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE clients
                SET default_asset_folder = ?,
                    default_export_folder = ?
                WHERE id = ?
                """,
                (asset_value, export_value, client_id),
            )
        updated = self.get_client(client_id)
        if updated is None:
            raise LookupError(f"Client not found after update: {client_id}")
        self.log_activity(
            "client_defaults_updated",
            f"{updated.name} client defaults updated",
        )
        return updated

    def list_client_summaries(self) -> list[ClientSummary]:
        clients = self.list_client_records()
        summaries: list[ClientSummary] = []
        with self.database.connect() as connection:
            for client in clients:
                active = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM projects
                    WHERE (client_id = ? OR client = ? COLLATE NOCASE)
                      AND status NOT IN ('Done', ?)
                    """,
                    (client.id, client.name, PUBLISHED_STATUS),
                ).fetchone()["count"]
                completed = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM projects
                    WHERE (client_id = ? OR client = ? COLLATE NOCASE)
                      AND status IN ('Done', ?)
                    """,
                    (client.id, client.name, PUBLISHED_STATUS),
                ).fetchone()["count"]
                summaries.append(
                    ClientSummary(
                        client=client,
                        active_projects=int(active or 0),
                        completed_projects=int(completed or 0),
                    )
                )
        return summaries

    def get_client_recent_assets(self, client_id: int, limit: int = 8) -> list[Asset]:
        client = self.get_client(client_id)
        if client is None:
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, MAX(pa.created_at) AS last_linked_at
                FROM assets a
                INNER JOIN project_assets pa ON pa.asset_id = a.id
                INNER JOIN projects p ON p.id = pa.project_id
                WHERE p.client_id = ? OR p.client = ? COLLATE NOCASE
                GROUP BY a.id
                ORDER BY last_linked_at DESC, a.updated_at DESC
                LIMIT ?
                """,
                (client.id, client.name, limit),
            ).fetchall()
        return [Asset.from_row(row) for row in rows]

    def get_client_recent_exports(
        self,
        client_id: int,
        limit: int = 8,
    ) -> list[ProjectExport]:
        client = self.get_client(client_id)
        if client is None:
            return []
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.*
                FROM project_exports e
                INNER JOIN projects p ON p.id = e.project_id
                WHERE p.client_id = ? OR p.client = ? COLLATE NOCASE
                ORDER BY e.detected_at DESC, e.id DESC
                LIMIT ?
                """,
                (client.id, client.name, limit),
            ).fetchall()
        return [ProjectExport.from_row(row) for row in rows]

    def suggest_client_for_path(self, file_path: Path) -> ClientSuggestion | None:
        path = file_path.expanduser()
        clients = self.list_client_records()

        filename_text = _normalize_match_text(path.stem)
        folder_parts = [
            _normalize_match_text(part)
            for part in path.parts[:-1]
            if part and part not in {path.anchor}
        ]
        folder_text = " ".join(folder_parts)
        best: tuple[Client, float, str] | None = None

        patterns = self._list_client_patterns()
        for client in clients:
            client_key = _normalize_match_text(client.name)
            score = 0.0
            reason = ""

            if client_key and _contains_pattern(filename_text, client_key):
                score = max(score, 0.82)
                reason = "client name in filename"
            if client_key and any(_contains_pattern(part, client_key) for part in folder_parts):
                score = max(score, 0.88)
                reason = "client folder name"
            if client_key and _contains_pattern(folder_text, client_key):
                score = max(score, 0.75)
                reason = reason or "client name in folder path"

            for pattern in patterns:
                if pattern.client_id != client.id:
                    continue
                pattern_text = _normalize_match_text(pattern.pattern)
                if not pattern_text:
                    continue
                if pattern.pattern_type == "filename" and _contains_pattern(filename_text, pattern_text):
                    if pattern.confidence > score:
                        score = pattern.confidence
                        reason = f"learned filename pattern: {pattern.pattern}"
                elif pattern.pattern_type == "folder" and any(
                    _contains_pattern(part, pattern_text) for part in folder_parts
                ):
                    if pattern.confidence > score:
                        score = pattern.confidence
                        reason = f"learned folder pattern: {pattern.pattern}"

            if best is None or score > best[1]:
                best = (client, score, reason)

        if best is not None and best[1] >= 0.6:
            return ClientSuggestion(best[0], min(best[1], 0.99), best[2] or "previous client history")

        inferred = _infer_client_name_from_path(path)
        if inferred is not None:
            name, confidence, reason = inferred
            return ClientSuggestion(
                Client(id=None, name=name),
                confidence,
                reason,
            )
        return None

    def learn_client_patterns(
        self,
        client: Client,
        file_path: Path,
        *,
        project_name: str = "",
    ) -> None:
        if client.id is None:
            return
        candidates: list[tuple[str, str, float]] = []
        client_key = _normalize_match_text(client.name)
        filename = _normalize_match_text(file_path.stem)
        project_text = _normalize_match_text(project_name)
        if client_key and _contains_pattern(filename, client_key):
            candidates.append((client.name, "filename", 0.76))
        if client_key and _contains_pattern(project_text, client_key):
            candidates.append((client.name, "filename", 0.72))

        for part in file_path.parts[:-1]:
            cleaned = part.strip(" .")
            normalized = _normalize_match_text(cleaned)
            if not normalized or len(normalized) < 3:
                continue
            if client_key and _contains_pattern(normalized, client_key):
                candidates.append((cleaned, "folder", 0.8))

        if not candidates:
            candidates.append((client.name, "filename", 0.62))

        with self.database.connect() as connection:
            for pattern, pattern_type, confidence in candidates:
                self._upsert_client_pattern(
                    connection,
                    client.id,
                    pattern,
                    pattern_type,
                    confidence,
                )

    def get_project(self, project_id: int) -> Project | None:
        flow_project_id = self.flow_project_id() or 0
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    p.*,
                    CASE WHEN p.id = ? THEN 1 ELSE 0 END AS in_flow,
                    COUNT(DISTINCT pa.asset_id) AS asset_count,
                    COUNT(
                        DISTINCT CASE
                            WHEN pr.completed = 0 THEN pr.id
                        END
                    ) AS open_revision_count
                FROM projects p
                LEFT JOIN project_assets pa ON pa.project_id = p.id
                LEFT JOIN project_revisions pr ON pr.project_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """,
                (flow_project_id, project_id),
            ).fetchone()
        return self._decorate_project_payment(Project.from_row(row)) if row else None

    def ensure_project_structure(self, project: Project) -> None:
        if project.folder_path.exists() and project.folder_path.is_dir():
            ensure_linked_assets_folder(project.folder_path)

    def create_project_from_raw(
        self,
        *,
        raw_video_path: Path,
        name: str,
        client: str,
        video_type: str,
    ) -> Project:
        name = name.strip()
        if not name:
            raise ValueError("Project name is required.")
        video_type = video_type.strip()
        if not video_type:
            raise ValueError("Video type is required.")

        project_folder, imported_raw = import_raw_video(
            raw_video_path,
            name,
            self.projects_root(),
            self.raw_file_mode(),
        )
        ensure_linked_assets_folder(project_folder)
        client_record = self.ensure_client(client)
        initial_status = STATUSES[0]

        with self.database.connect() as connection:
            sort_order = self._next_project_sort_order(connection, initial_status)
            cursor = connection.execute(
                """
                INSERT INTO projects (
                    name, client, client_id, status, video_type, folder_path, raw_video_path, sort_order, estimated_payment, estimated_payment_custom
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    client.strip(),
                    client_record.id if client_record else None,
                    initial_status,
                    video_type,
                    str(project_folder),
                    str(imported_raw),
                    sort_order,
                    self.estimated_payment_for_video_type(video_type),
                    1,
                ),
            )
            project_id = int(cursor.lastrowid)

        project = self.get_project(project_id)
        if project is None:
            raise sqlite3.DatabaseError("Project was created but could not be loaded.")
        self._record_status_history(project_id, project.status)
        if client_record is not None:
            self.learn_client_patterns(client_record, raw_video_path, project_name=name)
        self.log_activity(
            "project_created",
            f"{project.name} project created",
            project_id=project.id,
        )
        return project

    def update_status(self, project: Project, status: str) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before status can be updated.")
        return self.update_status_by_id(project.id, status)

    def update_status_by_id(self, project_id: int, status: str) -> Project:
        if status not in ALL_STATUSES:
            raise ValueError(f"Unknown status: {status}")

        existing = self._require_project(project_id)
        with self.database.connect() as connection:
            sort_order = existing.sort_order
            if existing.status != status:
                sort_order = self._next_project_sort_order(connection, status)
            if status == PUBLISHED_STATUS and existing.status != PUBLISHED_STATUS:
                earned_amount = max(0.0, float(existing.estimated_payment))
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?,
                        sort_order = ?,
                        earned_amount = ?,
                        earned_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, sort_order, earned_amount, project_id),
                )
            elif status != PUBLISHED_STATUS and existing.status == PUBLISHED_STATUS:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?,
                        sort_order = ?,
                        earned_amount = 0,
                        earned_at = '',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, sort_order, project_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE projects
                    SET status = ?, sort_order = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (status, sort_order, project_id),
                )
        project = self._require_project(project_id)
        if existing.status != status:
            self._record_status_history(project_id, status)
        self.log_activity(
            "project_status_changed",
            f"{project.name} moved to {status}",
            project_id=project.id,
        )
        return project

    def move_project_to_kanban_position(
        self,
        project_id: int,
        status: str,
        target_index: int,
    ) -> Project:
        if status not in STATUSES:
            raise ValueError(f"Unknown Kanban status: {status}")

        existing = self._require_project(project_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM projects
                WHERE status = ?
                  AND id != ?
                ORDER BY priority DESC, sort_order ASC, updated_at DESC, id DESC
                """,
                (status, project_id),
            ).fetchall()
            ordered_ids = [int(row["id"]) for row in rows]
            index = max(0, min(int(target_index), len(ordered_ids)))
            ordered_ids.insert(index, project_id)

            connection.execute(
                """
                UPDATE projects
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, project_id),
            )
            for order, ordered_id in enumerate(ordered_ids):
                connection.execute(
                    """
                    UPDATE projects
                    SET sort_order = ?
                    WHERE id = ?
                    """,
                    (order * 100, ordered_id),
                )

        project = self._require_project(project_id)
        if existing.status != status:
            self._record_status_history(project_id, status)
            event_type = "project_status_changed"
            message = f"{project.name} moved to {status}"
        else:
            event_type = "project_reordered"
            message = f"{project.name} reordered in {status}"
        self.log_activity(event_type, message, project_id=project.id)
        return project

    def publish_project(self, project_id: int) -> Project:
        project = self._require_project(project_id)
        if project.status != "Done":
            raise ValueError("Only Done projects can be published.")
        return self._move_project_folder_and_status(
            project,
            PUBLISHED_STATUS,
            self._published_projects_root(),
        )

    def restore_published_project(self, project_id: int) -> Project:
        project = self._require_project(project_id)
        if project.status != PUBLISHED_STATUS:
            raise ValueError("Only published projects can be restored.")
        return self._move_project_folder_and_status(
            project,
            "Done",
            self.projects_root(),
        )

    def _published_projects_root(self) -> Path:
        root = self.projects_root() / PUBLISHED_STATUS
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _move_project_folder_and_status(
        self,
        project: Project,
        target_status: str,
        destination_parent: Path,
    ) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before it can be moved.")
        if target_status not in ALL_STATUSES:
            raise ValueError(f"Unknown status: {target_status}")

        source_folder = project.folder_path.expanduser()
        if not source_folder.exists() or not source_folder.is_dir():
            raise FileNotFoundError(f"Project folder not found: {source_folder}")

        old_folder = source_folder.resolve(strict=False)
        destination_parent = destination_parent.expanduser().resolve(strict=False)
        target_folder = safe_child_path(
            destination_parent,
            source_folder.name,
            is_folder=True,
        )
        target_folder = target_folder.resolve(strict=False)

        if self._path_is_relative_to(target_folder, old_folder):
            raise ValueError("Cannot move a project folder into itself.")

        moved = False
        try:
            if old_folder != target_folder:
                shutil.move(str(old_folder), str(target_folder))
                moved = True
            ensure_linked_assets_folder(target_folder)
        except Exception as error:
            raise OSError(f"Could not move project folder: {error}") from error

        try:
            with self.database.connect() as connection:
                self._update_project_paths_after_folder_move(
                    connection,
                    project,
                    old_folder,
                    target_folder,
                    target_status,
                )
        except Exception as error:
            if moved:
                try:
                    if target_folder.exists() and not old_folder.exists():
                        shutil.move(str(target_folder), str(old_folder))
                except Exception as rollback_error:
                    raise RuntimeError(
                        "Project folder was moved, but EditFlow could not update the database "
                        "and could not move the folder back. "
                        f"Moved folder: {target_folder}. Database error: {error}. "
                        f"Rollback error: {rollback_error}"
                    ) from error
            raise

        updated = self._require_project(project.id)
        if project.status != target_status:
            self._record_status_history(project.id, target_status)
        self.log_activity(
            "project_status_changed",
            f"{updated.name} moved to {target_status}",
            project_id=updated.id,
        )
        return updated

    def _update_project_paths_after_folder_move(
        self,
        connection: sqlite3.Connection,
        project: Project,
        old_folder: Path,
        new_folder: Path,
        target_status: str,
    ) -> None:
        sort_order = project.sort_order
        if project.status != target_status:
            sort_order = self._next_project_sort_order(connection, target_status)

        raw_video_path = self._remap_project_folder_path(
            str(project.raw_video_path),
            old_folder,
            new_folder,
        )
        edited_video_path = self._remap_project_folder_path(
            str(project.edited_video_path or ""),
            old_folder,
            new_folder,
        )

        if target_status == PUBLISHED_STATUS and project.status != PUBLISHED_STATUS:
            earned_amount = max(0.0, float(project.estimated_payment))
            connection.execute(
                """
                UPDATE projects
                SET status = ?,
                    sort_order = ?,
                    earned_amount = ?,
                    earned_at = CURRENT_TIMESTAMP,
                    folder_path = ?,
                    raw_video_path = ?,
                    edited_video_path = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    target_status,
                    sort_order,
                    earned_amount,
                    str(new_folder),
                    raw_video_path,
                    edited_video_path,
                    project.id,
                ),
            )
        elif target_status != PUBLISHED_STATUS and project.status == PUBLISHED_STATUS:
            connection.execute(
                """
                UPDATE projects
                SET status = ?,
                    sort_order = ?,
                    earned_amount = 0,
                    earned_at = '',
                    folder_path = ?,
                    raw_video_path = ?,
                    edited_video_path = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    target_status,
                    sort_order,
                    str(new_folder),
                    raw_video_path,
                    edited_video_path,
                    project.id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE projects
                SET status = ?,
                    sort_order = ?,
                    folder_path = ?,
                    raw_video_path = ?,
                    edited_video_path = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    target_status,
                    sort_order,
                    str(new_folder),
                    raw_video_path,
                    edited_video_path,
                    project.id,
                ),
            )

        asset_rows = connection.execute(
            """
            SELECT asset_id, linked_path
            FROM project_assets
            WHERE project_id = ?
            """,
            (project.id,),
        ).fetchall()
        for row in asset_rows:
            linked_path = row["linked_path"] or ""
            remapped = self._remap_project_folder_path(
                linked_path,
                old_folder,
                new_folder,
            )
            if remapped != linked_path:
                connection.execute(
                    """
                    UPDATE project_assets
                    SET linked_path = ?
                    WHERE project_id = ? AND asset_id = ?
                    """,
                    (remapped, project.id, row["asset_id"]),
                )

        export_rows = connection.execute(
            """
            SELECT id, file_path
            FROM project_exports
            WHERE project_id = ?
            """,
            (project.id,),
        ).fetchall()
        for row in export_rows:
            file_path = row["file_path"] or ""
            remapped = self._remap_project_folder_path(
                file_path,
                old_folder,
                new_folder,
            )
            if remapped != file_path:
                remapped_path = Path(remapped)
                connection.execute(
                    """
                    UPDATE project_exports
                    SET file_path = ?,
                        normalized_file_path = ?,
                        filename = ?,
                        file_size = ?,
                        modified_at = ?
                    WHERE id = ?
                    """,
                    (
                        remapped,
                        normalize_file_path(remapped_path),
                        remapped_path.name,
                        safe_file_size(remapped_path),
                        safe_modified_at(remapped_path),
                        row["id"],
                    ),
                )

    def _remap_project_folder_path(
        self,
        value: str,
        old_folder: Path,
        new_folder: Path,
    ) -> str:
        if not value:
            return ""
        path = Path(value).expanduser()
        try:
            relative = path.resolve(strict=False).relative_to(old_folder)
        except ValueError:
            return value
        return str(new_folder / relative)

    def _path_is_relative_to(self, path: Path, parent: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
            return True
        except ValueError:
            return False

    def archive_project(self, project_id: int) -> Project:
        return self.publish_project(project_id)

    def restore_archived_project(self, project_id: int) -> Project:
        return self.restore_published_project(project_id)

    def remove_project(self, project_id: int, *, delete_files: bool = False) -> Project:
        project = self._require_project(project_id)
        if delete_files:
            self._delete_project_folder(project)
        if self.flow_project_id() == project_id:
            self.set_setting("flow_project_id", "")

        with self.database.connect() as connection:
            connection.execute(
                """
                DELETE FROM projects
                WHERE id = ?
                """,
                (project_id,),
            )

        self.log_activity(
            "project_removed",
            (
                f"{project.name} removed from EditFlow and project files deleted"
                if delete_files
                else f"{project.name} removed from EditFlow only"
            ),
        )
        return project

    def set_edited_video(self, project: Project, edited_video_path: Path) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before an edited video can be linked.")

        imported_edited = import_edited_video(
            edited_video_path,
            project.folder_path,
            self.edited_file_mode(),
        )
        return self.set_edited_video_reference(
            project,
            imported_edited,
            add_export=True,
        )

    def replace_raw_video(self, project: Project, raw_video_path: Path) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before a raw video can be replaced.")

        source = ensure_mp4(raw_video_path)
        project.folder_path.mkdir(parents=True, exist_ok=True)
        ensure_linked_assets_folder(project.folder_path)
        stored_raw = transfer_file_into_folder(
            source,
            project.folder_path,
            self.raw_file_mode(),
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET raw_video_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(stored_raw), project.id),
            )
        self.log_activity(
            "raw_video_replaced",
            f"{stored_raw.name} set as raw video for {project.name}",
            project_id=project.id,
        )
        return self._require_project(project.id)

    def replace_edited_video(self, project: Project, edited_video_path: Path) -> Project:
        return self.set_edited_video(project, edited_video_path)

    def remove_raw_video(
        self,
        project: Project,
        *,
        delete_file: bool = False,
    ) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before a raw video can be removed.")
        old_path = project.raw_video_path
        if delete_file and old_path.exists() and old_path.is_file():
            old_path.unlink()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET raw_video_path = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (project.id,),
            )
        self.log_activity(
            "raw_video_removed",
            (
                f"{old_path.name} removed from {project.name} and deleted"
                if delete_file
                else f"{old_path.name} unlinked from {project.name}"
            ),
            project_id=project.id,
        )
        return self._require_project(project.id)

    def remove_edited_video(
        self,
        project: Project,
        *,
        delete_file: bool = False,
    ) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before an edited video can be removed.")
        old_path = project.edited_video_path
        if delete_file and old_path is not None and old_path.exists() and old_path.is_file():
            old_path.unlink()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET edited_video_path = '', updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (project.id,),
            )
        self.log_activity(
            "edited_video_removed",
            (
                f"{old_path.name} removed from {project.name} and deleted"
                if delete_file and old_path is not None
                else f"Edited video unlinked from {project.name}"
            ),
            project_id=project.id,
        )
        return self._require_project(project.id)

    def add_export_file(self, project: Project, export_path: Path) -> ProjectExport:
        if project.id is None:
            raise ValueError("Project must be saved before an export can be added.")
        stored_export = import_edited_video(
            export_path,
            project.folder_path,
            self.edited_file_mode(),
        )
        return self.add_project_export(project.id, stored_export)

    def set_edited_video_reference(
        self,
        project: Project,
        edited_video_path: Path,
        *,
        add_export: bool = False,
    ) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before an edited video can be linked.")

        path = edited_video_path.expanduser()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET edited_video_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(path), project.id),
            )
        if add_export:
            self.add_project_export(project.id, path)
        self.log_activity(
            "edited_video_assigned",
            f"{path.name} assigned to {project.name}",
            project_id=project.id,
        )
        return self._require_project(project.id)

    def find_new_mp4_candidates(self, project: Project) -> list[Path]:
        if project.has_edited_video:
            return []
        return find_unassigned_mp4s(
            project.folder_path,
            project.raw_video_path,
            project.edited_video_path,
        )

    def update_notes(self, project: Project, notes: str) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before notes can be updated.")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (notes.strip(), project.id),
            )
        return self._require_project(project.id)

    def update_priority(self, project: Project, priority: bool) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before priority can be updated.")
        priority_value = 1 if priority else 0
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET priority = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (priority_value, project.id),
            )
        updated = self._require_project(project.id)
        self.log_activity(
            "project_priority_changed",
            f"{updated.name} priority {'enabled' if priority else 'disabled'}",
            project_id=project.id,
        )
        return updated

    def update_project_payment_amount(self, project: Project, amount: float) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before earnings can be updated.")
        amount = round(max(0.0, float(amount)), 2)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET estimated_payment = ?,
                    estimated_payment_custom = 1,
                    earned_amount = CASE
                        WHEN status = ? THEN ?
                        ELSE earned_amount
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (amount, PUBLISHED_STATUS, amount, project.id),
            )
        updated = self._require_project(project.id)
        self.log_activity(
            "project_payment_updated",
            f"{updated.name} earnings amount set to ${amount:.2f}",
            project_id=project.id,
        )
        return updated

    def update_video_type(self, project: Project, video_type: str) -> Project:
        if project.id is None:
            raise ValueError("Project must be saved before video type can be updated.")
        cleaned = video_type.strip()
        if not cleaned:
            raise ValueError("Video type is required.")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET video_type = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (cleaned, project.id),
            )
        updated = self._require_project(project.id)
        self.log_activity(
            "project_video_type_changed",
            f"{updated.name} video type set to {updated.video_type}",
            project_id=project.id,
        )
        return updated

    def add_project_revision(self, project: Project, note: str) -> ProjectRevision:
        if project.id is None:
            raise ValueError("Project must be saved before revisions can be added.")
        cleaned = note.strip()
        if not cleaned:
            raise ValueError("Revision note is required.")
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO project_revisions (project_id, note)
                VALUES (?, ?)
                """,
                (project.id, cleaned),
            )
            revision_id = int(cursor.lastrowid)
            connection.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project.id,),
            )
        revision = self._require_revision(revision_id)
        self.log_activity(
            "revision_received",
            f"Revision received for {project.name}: {cleaned}",
            project_id=project.id,
        )
        return revision

    def set_revision_completed(
        self,
        revision_id: int,
        completed: bool,
    ) -> ProjectRevision:
        revision = self._require_revision(revision_id)
        completed_value = 1 if completed else 0
        completed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if completed else ""
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE project_revisions
                SET completed = ?, completed_at = ?
                WHERE id = ?
                """,
                (completed_value, completed_at, revision_id),
            )
            connection.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (revision.project_id,),
            )
        updated = self._require_revision(revision_id)
        project = self.get_project(revision.project_id)
        if project is not None:
            event_type = "revision_completed" if completed else "revision_reopened"
            verb = "completed" if completed else "reopened"
            self.log_activity(
                event_type,
                f"Revision {verb} for {project.name}: {updated.note}",
                project_id=revision.project_id,
            )
        return updated

    def list_project_revisions(self, project_id: int) -> list[ProjectRevision]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM project_revisions
                WHERE project_id = ?
                ORDER BY completed ASC, created_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [ProjectRevision.from_row(row) for row in rows]

    def list_workflow_suggestions(
        self,
        project: Project,
        *,
        include_folder_scan: bool = True,
    ) -> list[WorkflowSuggestion]:
        if project.id is None:
            return []

        suggestions: list[WorkflowSuggestion] = []
        if project.has_edited_video and project.edited_video_path is not None:
            if not project.edited_video_path.exists():
                suggestions.append(
                    WorkflowSuggestion(
                        project.id,
                        project.name,
                        "Edited video is missing",
                        severity="warning",
                    )
                )
            elif project.status not in {"Need Upload", "Done"}:
                suggestions.append(
                    WorkflowSuggestion(
                        project.id,
                        project.name,
                        "Edited video detected. Move to Need Upload?",
                        target_status="Need Upload",
                    )
                )
        elif str(project.raw_video_path) and project.raw_video_path.exists():
            suggestions.append(
                WorkflowSuggestion(
                    project.id,
                    project.name,
                    "Raw exists and no edited video. Keep in Need Edit",
                    target_status="Need Edit" if project.status != "Need Edit" else None,
                )
            )
            candidates = (
                self.find_new_mp4_candidates(project)
                if include_folder_scan
                else []
            )
            if candidates:
                suggestions.append(
                    WorkflowSuggestion(
                        project.id,
                        project.name,
                        f"New video detected: {candidates[0].name}. Set as edited video?",
                    )
                )

        return suggestions

    def list_dashboard_suggestions(self, limit: int = 12) -> list[WorkflowSuggestion]:
        suggestions: list[WorkflowSuggestion] = []
        for project in self.list_projects():
            suggestions.extend(
                self.list_workflow_suggestions(project, include_folder_scan=False)
            )
            if len(suggestions) >= limit:
                return suggestions[:limit]
        return suggestions[:limit]

    def get_dashboard_counts(self) -> dict[str, int]:
        counts = {status: 0 for status in STATUSES}
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM projects
                GROUP BY status
                """
            ).fetchall()
        for row in rows:
            status = row["status"]
            if status in counts:
                counts[status] = int(row["count"] or 0)
        return counts

    def list_recent_detections(self, limit: int = 10) -> list[DetectionEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM detected_files
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [DetectionEntry.from_row(row) for row in rows]

    def list_broken_links(self, limit: int = 12) -> list[FileIssue]:
        issues: list[FileIssue] = []
        for project in self.list_projects():
            issues.extend(self.list_file_issues(project))
            if len(issues) >= limit:
                return issues[:limit]
        return issues[:limit]

    def list_project_timeline(self, project_id: int) -> list[TimelineEntry]:
        project = self._require_project(project_id)
        entries: list[TimelineEntry] = []
        if project.created_at:
            entries.append(
                TimelineEntry(
                    "Raw imported",
                    project.raw_video_name,
                    project.created_at,
                    "raw",
                )
            )

        for status, created_at in self._list_project_status_history(project_id):
            entries.append(
                TimelineEntry(
                    _status_timeline_label(status),
                    status,
                    created_at,
                    "status",
                )
            )

        for entry in self.list_project_activity(project_id, limit=100):
            if entry.event_type == "asset_linked":
                entries.append(
                    TimelineEntry("Asset linked", entry.message, entry.created_at, "asset")
                )
            elif entry.event_type == "edited_video_assigned":
                entries.append(
                    TimelineEntry(
                        "Edited video assigned",
                        entry.message,
                        entry.created_at,
                        "edited",
                    )
                )

        for export in self.list_project_exports(project_id):
            label = f"Export {export.version_label.upper()}"
            entries.append(
                TimelineEntry(label, export.filename, export.detected_at, "export")
            )

        for revision in self.list_project_revisions(project_id):
            entries.append(
                TimelineEntry(
                    "Revision received",
                    revision.note,
                    revision.created_at,
                    "revision",
                )
            )
            if revision.completed and revision.completed_at:
                entries.append(
                    TimelineEntry(
                        "Revision completed",
                        revision.note,
                        revision.completed_at,
                        "revision",
                    )
                )

        entries.sort(key=lambda item: item.created_at or "")
        return _dedupe_timeline(entries)

    def list_assets(self, search: str = "") -> list[Asset]:
        search = search.strip()
        with self.database.connect() as connection:
            if search:
                pattern = f"%{search}%"
                rows = connection.execute(
                    """
                    SELECT * FROM assets
                    WHERE name LIKE ?
                       OR client LIKE ?
                       OR category LIKE ?
                       OR file_path LIKE ?
                       OR EXISTS (
                           SELECT 1
                           FROM asset_tags
                           WHERE asset_tags.asset_id = assets.id
                             AND asset_tags.tag LIKE ?
                       )
                    ORDER BY updated_at DESC, id DESC
                    """,
                    (pattern, pattern, pattern, pattern, pattern),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM assets ORDER BY updated_at DESC, id DESC"
                ).fetchall()
        return [Asset.from_row(row) for row in rows]

    def sync_asset_library(self) -> None:
        root = self.asset_library_root()
        paths = [
            path
            for path in sorted(root.rglob("*"), key=lambda item: (len(item.parts), str(item).casefold()))
            if path.exists()
        ]
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id, file_path FROM assets").fetchall()
            missing_ids: list[int] = []
            for row in rows:
                path = Path(row["file_path"])
                if _path_is_relative_to(path, root) and not path.exists():
                    missing_ids.append(int(row["id"]))

            for path in paths:
                self._upsert_asset_record(
                    connection,
                    path,
                    name=_asset_name_for_path(path),
                    category=infer_asset_category(path),
                    preserve_metadata=True,
                )

        if missing_ids:
            self.delete_assets(missing_ids, delete_files=False)

    def import_asset_files(
        self,
        file_paths: list[Path],
        target_folder: Path | None = None,
    ) -> list[Asset]:
        imported: list[Asset] = []
        for file_path in file_paths:
            source = file_path.expanduser()
            if not source.exists() or not source.is_file():
                raise FileNotFoundError(f"Asset file not found: {source}")
            imported.append(
                self.create_asset(
                    file_path=source,
                    name=source.stem,
                    target_folder=target_folder,
                )
            )
        return imported

    def import_asset_folder(
        self,
        folder_path: Path,
        target_folder: Path | None = None,
    ) -> Asset:
        folder = folder_path.expanduser()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"Asset folder not found: {folder}")
        return self.create_asset(
            file_path=folder,
            name=folder.name,
            category="Collection",
            target_folder=target_folder,
        )

    def create_asset_folder(
        self,
        parent_folder: Path | None,
        name: str,
    ) -> Asset:
        folder_name = _clean_library_name(name)
        if not folder_name:
            raise ValueError("Folder name is required.")
        parent = self._asset_target_folder(parent_folder)
        folder = safe_child_path(parent, folder_name, is_folder=True)
        folder.mkdir(parents=True, exist_ok=False)
        with self.database.connect() as connection:
            asset_id = self._upsert_asset_record(
                connection,
                folder,
                name=folder.name,
                category="Collection",
            )
        self.log_activity("asset_folder_created", f"Asset folder created: {folder.name}")
        return self._require_asset(asset_id)

    def rename_asset(self, asset_id: int, new_name: str) -> Asset:
        asset = self._require_asset(asset_id)
        old_path = asset.file_path
        if not old_path.exists():
            raise FileNotFoundError(f"Asset not found: {old_path}")
        cleaned = _clean_library_name(new_name)
        if not cleaned:
            raise ValueError("New asset name is required.")
        if old_path.is_file() and not Path(cleaned).suffix:
            cleaned = f"{cleaned}{old_path.suffix}"
        target = safe_child_path(old_path.parent, cleaned, is_folder=old_path.is_dir())
        if normalize_file_path(target) == normalize_file_path(old_path):
            return asset

        old_path.rename(target)
        affected_ids = self._update_asset_path_references(old_path, target)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET name = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (_asset_name_for_path(target), asset_id),
            )
        self._refresh_project_asset_links(affected_ids or [asset_id])
        self.log_activity("asset_renamed", f"{asset.name} renamed to {target.name}", asset_id=asset_id)
        return self._require_asset(asset_id)

    def move_assets(
        self,
        asset_ids: list[int],
        destination_folder: Path,
    ) -> list[Asset]:
        destination = self._asset_target_folder(destination_folder)
        assets = self._top_level_assets(asset_ids)
        moved: list[Asset] = []
        affected_ids: list[int] = []
        for asset in assets:
            source = asset.file_path
            if not source.exists():
                raise FileNotFoundError(f"Asset not found: {source}")
            if source.parent.resolve() == destination.resolve():
                moved.append(asset)
                continue
            if source.is_dir() and _path_is_relative_to(destination, source):
                raise ValueError("Cannot move an asset folder into itself.")
            target = safe_child_path(destination, source.name, is_folder=source.is_dir())
            shutil.move(str(source), str(target))
            changed_ids = self._update_asset_path_references(source, target)
            affected_ids.extend(changed_ids)
            moved.append(self._require_asset(asset.id or 0))

        if affected_ids:
            self._refresh_project_asset_links(affected_ids)
        self.log_activity("assets_moved", f"Moved {len(moved)} asset(s) to {destination}")
        return moved

    def asset_usage_count(self, asset_ids: list[int]) -> int:
        expanded_ids = self._expanded_asset_ids(asset_ids)
        if not expanded_ids:
            return 0
        placeholders = ", ".join("?" for _id in expanded_ids)
        with self.database.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(DISTINCT project_id) AS count
                FROM project_assets
                WHERE asset_id IN ({placeholders})
                """,
                tuple(expanded_ids),
            ).fetchone()
        return int(row["count"] or 0) if row is not None else 0

    def delete_assets(
        self,
        asset_ids: list[int],
        *,
        delete_files: bool = True,
    ) -> None:
        expanded_ids = self._expanded_asset_ids(asset_ids)
        if not expanded_ids:
            return
        assets = [self._require_asset(asset_id) for asset_id in expanded_ids]
        top_assets = self._top_level_assets(asset_ids)

        self._remove_project_asset_links(expanded_ids)
        placeholders = ", ".join("?" for _id in expanded_ids)
        with self.database.connect() as connection:
            connection.execute(
                f"DELETE FROM asset_tags WHERE asset_id IN ({placeholders})",
                tuple(expanded_ids),
            )
            connection.execute(
                f"DELETE FROM project_assets WHERE asset_id IN ({placeholders})",
                tuple(expanded_ids),
            )
            connection.execute(
                f"DELETE FROM assets WHERE id IN ({placeholders})",
                tuple(expanded_ids),
            )

        if delete_files:
            for asset in sorted(
                top_assets,
                key=lambda item: len(item.file_path.parts),
                reverse=True,
            ):
                path = asset.file_path
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists() or path.is_symlink():
                    path.unlink()

        self.log_activity(
            "assets_deleted",
            f"Deleted {len(assets)} asset record(s) from the Asset Library",
        )

    def list_asset_folders(self) -> list[Asset]:
        root = self.asset_library_root()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM assets
                WHERE asset_type = 'folder'
                ORDER BY file_path COLLATE NOCASE
                """
            ).fetchall()
        folders = [
            Asset.from_row(row)
            for row in rows
            if _path_is_relative_to(Path(row["file_path"]), root)
        ]
        return folders

    def get_project_assets(self, project_id: int) -> list[Asset]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*
                FROM assets a
                INNER JOIN project_assets pa ON pa.asset_id = a.id
                WHERE pa.project_id = ?
                ORDER BY a.name COLLATE NOCASE
                """,
                (project_id,),
            ).fetchall()
        return [Asset.from_row(row) for row in rows]

    def get_asset(self, asset_id: int) -> Asset | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
        return Asset.from_row(row) if row else None

    def create_asset(
        self,
        *,
        file_path: Path,
        name: str,
        client: str = "",
        category: str = "",
        tags: str | list[str] | tuple[str, ...] = "",
        target_folder: Path | None = None,
    ) -> Asset:
        source = file_path.expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Asset not found: {source}")

        original_source = source.resolve()
        target = self._asset_target_folder(target_folder)
        with self.database.connect() as connection:
            existing_id = self._find_asset_id_by_path(connection, source)

        stored_source = self._store_asset_in_library(source, target)
        if stored_source != original_source:
            changed_ids = self._update_asset_path_references(
                original_source,
                stored_source,
            )
            self._refresh_project_asset_links(changed_ids)

        asset_name = name.strip() or _asset_name_for_path(stored_source)
        normalized_path = normalize_file_path(stored_source)
        asset_type = "folder" if stored_source.is_dir() else "file"
        category_value = category.strip() or infer_asset_category(stored_source)
        with self.database.connect() as connection:
            asset_id = existing_id or self._find_asset_id_by_path(connection, stored_source)
            if asset_id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO assets (
                        name, file_path, normalized_file_path, asset_type, client, category
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_name,
                        str(stored_source),
                        normalized_path,
                        asset_type,
                        client.strip(),
                        category_value,
                    ),
                )
                asset_id = int(cursor.lastrowid)
            else:
                connection.execute(
                    """
                    UPDATE assets
                    SET name = ?,
                        file_path = ?,
                        normalized_file_path = ?,
                        asset_type = ?,
                        client = ?,
                        category = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        asset_name,
                        str(stored_source),
                        normalized_path,
                        asset_type,
                        client.strip(),
                        category_value,
                        asset_id,
                    ),
                )

        if _normalize_tag_values(tags):
            self.set_asset_tags(asset_id, tags)
        if stored_source.is_dir():
            self._sync_asset_descendants(stored_source)

        return self._require_asset(asset_id)

    def get_or_create_asset_for_path(self, file_path: Path) -> Asset:
        source = file_path.expanduser()
        with self.database.connect() as connection:
            asset_id = self._find_asset_id_by_path(connection, source)
        if asset_id is not None:
            return self._require_asset(asset_id)
        return self.create_asset(
            file_path=source,
            name=source.stem if source.is_file() else source.name,
            category=infer_asset_category(source),
        )

    def get_asset_by_path(self, file_path: Path) -> Asset | None:
        source = file_path.expanduser()
        with self.database.connect() as connection:
            asset_id = self._find_asset_id_by_path(connection, source)
        return self._require_asset(asset_id) if asset_id is not None else None

    def link_asset(self, project: Project, asset: Asset) -> Project:
        if project.id is None or asset.id is None:
            raise ValueError("Project and asset must be saved before linking.")
        linked_path = ""
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT linked_path
                FROM project_assets
                WHERE project_id = ? AND asset_id = ?
                """,
                (project.id, asset.id),
            ).fetchone()
            current_link = existing["linked_path"] if existing is not None else ""
            current_link_path = Path(current_link) if current_link else None
            if current_link_path is not None and (
                current_link_path.exists() or current_link_path.is_symlink()
            ):
                linked_path = current_link
            else:
                try:
                    linked_path = str(
                        create_project_asset_link(project.folder_path, asset.file_path)
                    )
                except OSError as error:
                    self._file_warnings.append(
                        f"Linked {asset.name} in EditFlow, but could not create a project-side shortcut: {error}"
                    )
                    linked_path = current_link or ""
            connection.execute(
                """
                INSERT OR IGNORE INTO project_assets (project_id, asset_id, linked_path)
                VALUES (?, ?, ?)
                """,
                (project.id, asset.id, linked_path),
            )
            connection.execute(
                """
                UPDATE project_assets
                SET created_at = CURRENT_TIMESTAMP,
                    linked_path = ?
                WHERE project_id = ? AND asset_id = ?
                """,
                (linked_path, project.id, asset.id),
            )
            connection.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project.id,),
            )
        updated = self._require_project(project.id)
        self.log_activity(
            "asset_linked",
            f"{asset.name} linked to {project.name}",
            project_id=project.id,
            asset_id=asset.id,
        )
        return updated

    def link_assets(self, project: Project, assets: list[Asset]) -> Project:
        updated = project
        for asset in assets:
            updated = self.link_asset(updated, asset)
        return updated

    def unlink_asset(self, project: Project, asset: Asset) -> Project:
        if project.id is None or asset.id is None:
            raise ValueError("Project and asset must be saved before unlinking.")
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT linked_path
                FROM project_assets
                WHERE project_id = ? AND asset_id = ?
                """,
                (project.id, asset.id),
            ).fetchone()
            if row is not None and row["linked_path"]:
                try:
                    remove_project_asset_link(Path(row["linked_path"]))
                except OSError as error:
                    self._file_warnings.append(
                        f"Unlinked {asset.name}, but could not remove the project-side shortcut: {error}"
                    )
            connection.execute(
                """
                DELETE FROM project_assets
                WHERE project_id = ? AND asset_id = ?
                """,
                (project.id, asset.id),
            )
            connection.execute(
                "UPDATE projects SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (project.id,),
            )
        updated = self._require_project(project.id)
        self.log_activity(
            "asset_unlinked",
            f"{asset.name} unlinked from {project.name}",
            project_id=project.id,
            asset_id=asset.id,
        )
        return updated

    def get_recent_assets(self, limit: int = 10) -> list[Asset]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*, MAX(pa.created_at) AS last_linked_at
                FROM assets a
                INNER JOIN project_assets pa ON pa.asset_id = a.id
                GROUP BY a.id
                ORDER BY last_linked_at DESC, a.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [Asset.from_row(row) for row in rows]

    def get_most_used_assets(self, limit: int = 10) -> list[tuple[Asset, int]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       COUNT(pa.project_id) AS usage_count,
                       MAX(pa.created_at) AS last_linked_at
                FROM assets a
                INNER JOIN project_assets pa ON pa.asset_id = a.id
                GROUP BY a.id
                ORDER BY usage_count DESC, last_linked_at DESC, a.name COLLATE NOCASE
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            (Asset.from_row(row), int(row["usage_count"] or 0))
            for row in rows
        ]

    def asset_usage_counts(self, asset_ids: list[int]) -> dict[int, int]:
        if not asset_ids:
            return {}
        placeholders = ", ".join("?" for _id in asset_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT asset_id, COUNT(DISTINCT project_id) AS usage_count
                FROM project_assets
                WHERE asset_id IN ({placeholders})
                GROUP BY asset_id
                """,
                tuple(asset_ids),
            ).fetchall()
        return {
            int(row["asset_id"]): int(row["usage_count"] or 0)
            for row in rows
        }

    def favorite_asset_ids(self) -> set[int]:
        value = self.get_setting("favorite_asset_ids", "")
        ids: set[int] = set()
        for raw_value in value.split(","):
            try:
                asset_id = int(raw_value.strip())
            except ValueError:
                continue
            if asset_id > 0:
                ids.add(asset_id)
        return ids

    def set_asset_favorite(self, asset_id: int, favorite: bool) -> None:
        asset_ids = self.favorite_asset_ids()
        if favorite:
            asset_ids.add(asset_id)
        else:
            asset_ids.discard(asset_id)
        value = ",".join(str(existing_id) for existing_id in sorted(asset_ids))
        self.set_setting("favorite_asset_ids", value)

    def get_favorite_assets(self, limit: int = 20) -> list[Asset]:
        favorite_ids = list(self.favorite_asset_ids())
        if not favorite_ids:
            return []
        placeholders = ", ".join("?" for _id in favorite_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM assets
                WHERE id IN ({placeholders})
                """,
                tuple(favorite_ids),
            ).fetchall()
        assets_by_id = {
            int(row["id"]): Asset.from_row(row)
            for row in rows
        }
        return [
            assets_by_id[asset_id]
            for asset_id in favorite_ids[:limit]
            if asset_id in assets_by_id
        ]

    def list_asset_tags(self, asset_id: int) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT tag
                FROM asset_tags
                WHERE asset_id = ?
                ORDER BY tag COLLATE NOCASE
                """,
                (asset_id,),
            ).fetchall()
        return [row["tag"] for row in rows]

    def list_asset_tag_map(self, asset_ids: list[int]) -> dict[int, list[str]]:
        if not asset_ids:
            return {}
        placeholders = ", ".join("?" for _id in asset_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT asset_id, tag
                FROM asset_tags
                WHERE asset_id IN ({placeholders})
                ORDER BY tag COLLATE NOCASE
                """,
                tuple(asset_ids),
            ).fetchall()
        tag_map: dict[int, list[str]] = {}
        for row in rows:
            tag_map.setdefault(int(row["asset_id"]), []).append(row["tag"])
        return tag_map

    def set_asset_tags(
        self,
        asset_id: int,
        tags: str | list[str] | tuple[str, ...],
    ) -> None:
        normalized_tags = _normalize_tag_values(tags)
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM asset_tags WHERE asset_id = ?",
                (asset_id,),
            )
            connection.executemany(
                """
                INSERT INTO asset_tags (asset_id, tag)
                VALUES (?, ?)
                """,
                [(asset_id, tag) for tag in normalized_tags],
            )
            connection.execute(
                """
                UPDATE assets
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (asset_id,),
            )

    def suggest_assets_for_project(
        self,
        project: Project,
        limit: int = 8,
    ) -> list[AssetSuggestion]:
        if project.id is None:
            return []

        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       COUNT(pa.project_id) AS total_usage,
                       MAX(pa.created_at) AS last_linked_at,
                       SUM(
                           CASE
                               WHEN p.client_id = ?
                                    OR p.client = ? COLLATE NOCASE
                               THEN 1
                               ELSE 0
                           END
                       ) AS client_usage
                FROM assets a
                LEFT JOIN project_assets pa ON pa.asset_id = a.id
                LEFT JOIN projects p ON p.id = pa.project_id
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM project_assets linked
                    WHERE linked.project_id = ?
                      AND linked.asset_id = a.id
                )
                GROUP BY a.id
                """,
                (project.client_id, project.client, project.id),
            ).fetchall()
            tag_rows = connection.execute(
                """
                SELECT asset_id, tag
                FROM asset_tags
                ORDER BY tag COLLATE NOCASE
                """
            ).fetchall()

        tags_by_asset: dict[int, list[str]] = {}
        for tag_row in tag_rows:
            tags_by_asset.setdefault(int(tag_row["asset_id"]), []).append(tag_row["tag"])

        keywords = _keyword_tokens(project.name)
        suggestions: list[AssetSuggestion] = []
        for row in rows:
            asset = Asset.from_row(row)
            if asset.id is None:
                continue
            client_usage = int(row["client_usage"] or 0)
            total_usage = int(row["total_usage"] or 0)
            last_linked_at = row["last_linked_at"] or ""
            score = 0
            reasons: list[str] = []

            if client_usage > 0:
                score += 5
                client_name = project.client or "this client"
                reasons.append(
                    f"Used in {client_usage} {client_name} {_project_word(client_usage)}"
                )

            matched_tags = _matching_tags(keywords, tags_by_asset.get(asset.id, []))
            if matched_tags:
                score += 3
                reasons.append(f'Matches "{matched_tags[0]}"')

            if _is_recent_link(last_linked_at):
                score += 2
                reasons.append("Recently used")

            if total_usage >= 2:
                score += 1
                reasons.append(f"Used in {total_usage} total projects")

            if score > 0:
                suggestions.append(
                    AssetSuggestion(
                        asset=asset,
                        score=score,
                        reasons=reasons,
                        client_usage=client_usage,
                        total_usage=total_usage,
                        last_linked_at=last_linked_at,
                    )
                )

        suggestions.sort(
            key=lambda suggestion: (
                suggestion.score,
                suggestion.client_usage,
                suggestion.total_usage,
                suggestion.last_linked_at,
                suggestion.asset.name.casefold(),
            ),
            reverse=True,
        )
        return suggestions[:limit]

    def backup_data(self, destination: Path) -> Path:
        return backup_database(self.database.db_path, destination)

    def list_watched_folders(self, enabled_only: bool = False) -> list[WatchedFolder]:
        with self.database.connect() as connection:
            if enabled_only:
                rows = connection.execute(
                    """
                    SELECT * FROM watched_folders
                    WHERE enabled = 1
                    ORDER BY folder_type, path COLLATE NOCASE
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM watched_folders
                    ORDER BY folder_type, path COLLATE NOCASE
                    """
                ).fetchall()
        return [WatchedFolder.from_row(row) for row in rows]

    def add_watched_folder(self, path: Path, folder_type: str) -> WatchedFolder:
        if folder_type not in FOLDER_TYPE_LABELS:
            raise ValueError(f"Unknown watched folder type: {folder_type}")
        folder = path.expanduser()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")

        normalized = normalize_file_path(folder)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO watched_folders (
                    path, normalized_path, folder_type, enabled
                )
                VALUES (?, ?, ?, 1)
                ON CONFLICT(normalized_path, folder_type) DO UPDATE SET
                    path = excluded.path,
                    enabled = 1
                """,
                (str(folder), normalized, folder_type),
            )
            row = connection.execute(
                """
                SELECT * FROM watched_folders
                WHERE normalized_path = ? AND folder_type = ?
                """,
                (normalized, folder_type),
            ).fetchone()
        self.log_activity(
            "watched_folder_added",
            f"{FOLDER_TYPE_LABELS[folder_type]} watched: {folder}",
        )
        return WatchedFolder.from_row(row)

    def remove_watched_folder(self, folder_id: int) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM watched_folders WHERE id = ?",
                (folder_id,),
            ).fetchone()
            connection.execute(
                "DELETE FROM watched_folders WHERE id = ?",
                (folder_id,),
            )
        if row is not None:
            folder = WatchedFolder.from_row(row)
            self.log_activity(
                "watched_folder_removed",
                f"{folder.label} removed: {folder.path}",
            )

    def set_watched_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE watched_folders
                SET enabled = ?
                WHERE id = ?
                """,
                (1 if enabled else 0, folder_id),
            )

    def add_project_export(
        self,
        project_id: int,
        file_path: Path,
        version_label: str | None = None,
    ) -> ProjectExport:
        source = file_path.expanduser()
        normalized = normalize_file_path(source)
        label = version_label or detect_version_label(source)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id
                FROM project_exports
                WHERE project_id = ? AND normalized_file_path = ?
                """,
                (project_id, normalized),
            ).fetchone()
            values = (
                str(source),
                normalized,
                source.name,
                label,
                safe_file_size(source),
                safe_modified_at(source),
            )
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO project_exports (
                        project_id,
                        file_path,
                        normalized_file_path,
                        filename,
                        version_label,
                        file_size,
                        modified_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (project_id, *values),
                )
                export_id = int(cursor.lastrowid)
            else:
                export_id = int(row["id"])
                connection.execute(
                    """
                    UPDATE project_exports
                    SET file_path = ?,
                        normalized_file_path = ?,
                        filename = ?,
                        version_label = ?,
                        file_size = ?,
                        modified_at = ?
                    WHERE id = ?
                    """,
                    (*values, export_id),
                )
            row = connection.execute(
                "SELECT * FROM project_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
        export = ProjectExport.from_row(row)
        project = self.get_project(project_id)
        if project is not None:
            self.log_activity(
                "export_detected",
                f"{source.name} export detected for {project.name}",
                project_id=project_id,
            )
        return export

    def list_project_exports(self, project_id: int) -> list[ProjectExport]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM project_exports
                WHERE project_id = ?
                ORDER BY detected_at DESC, id DESC
                """,
                (project_id,),
            ).fetchall()
        return [ProjectExport.from_row(row) for row in rows]

    def get_export(self, export_id: int) -> ProjectExport | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
        return ProjectExport.from_row(row) if row else None

    def list_activity(self, limit: int = 20) -> list[ActivityEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM activity_log
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [ActivityEntry.from_row(row) for row in rows]

    def list_project_activity(
        self,
        project_id: int,
        limit: int = 50,
    ) -> list[ActivityEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM activity_log
                WHERE project_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [ActivityEntry.from_row(row) for row in rows]

    def log_activity(
        self,
        event_type: str,
        message: str,
        *,
        project_id: int | None = None,
        asset_id: int | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO activity_log (event_type, project_id, asset_id, message)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, project_id, asset_id, message),
            )

    def list_file_issues(self, project: Project) -> list[FileIssue]:
        issues: list[FileIssue] = []
        if not project.folder_path.exists() or not project.folder_path.is_dir():
            issues.append(
                FileIssue(
                    "project_folder",
                    "Project folder missing",
                    project.folder_path,
                    project_id=project.id,
                )
            )
        if str(project.raw_video_path) and not project.raw_video_path.exists():
            issues.append(
                FileIssue(
                    "raw_video",
                    "Raw video missing",
                    project.raw_video_path,
                    project_id=project.id,
                )
            )
        if project.edited_video_path is not None and not project.edited_video_path.exists():
            issues.append(
                FileIssue(
                    "edited_video",
                    "Edited video path broken",
                    project.edited_video_path,
                    project_id=project.id,
                )
            )

        if project.id is not None:
            for asset in self.get_project_assets(project.id):
                if not asset.file_path.exists():
                    issues.append(
                        FileIssue(
                            "asset",
                            f"Asset file not found: {asset.name}",
                            asset.file_path,
                            project_id=project.id,
                            asset_id=asset.id,
                        )
                    )
            for export in self.list_project_exports(project.id):
                if not export.file_path.exists():
                    issues.append(
                        FileIssue(
                            "export",
                            f"Export missing: {export.filename}",
                            export.file_path,
                            project_id=project.id,
                            export_id=export.id,
                        )
                    )
        return issues

    def relink_project_folder(self, project_id: int, folder_path: Path) -> Project:
        folder = folder_path.expanduser()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE projects
                SET folder_path = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(folder), project_id),
            )
        project = self._require_project(project_id)
        self.log_activity(
            "missing_file_relinked",
            f"Project folder relinked for {project.name}",
            project_id=project_id,
        )
        return project

    def relink_project_file(self, project_id: int, kind: str, file_path: Path) -> Project:
        if kind not in {"raw_video", "edited_video"}:
            raise ValueError(f"Unknown project file type: {kind}")
        source = file_path.expanduser()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"File not found: {source}")
        column = "raw_video_path" if kind == "raw_video" else "edited_video_path"
        with self.database.connect() as connection:
            connection.execute(
                f"""
                UPDATE projects
                SET {column} = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(source), project_id),
            )
        project = self._require_project(project_id)
        label = "Raw video" if kind == "raw_video" else "Edited video"
        self.log_activity(
            "missing_file_relinked",
            f"{label} relinked for {project.name}",
            project_id=project_id,
        )
        return project

    def relink_asset(self, asset_id: int, file_path: Path) -> Asset:
        source = file_path.expanduser()
        if not source.exists():
            raise FileNotFoundError(f"Asset not found: {source}")
        normalized = normalize_file_path(source)
        asset_type = "folder" if source.is_dir() else "file"
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET file_path = ?,
                    normalized_file_path = ?,
                    asset_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(source), normalized, asset_type, asset_id),
            )
        asset = self._require_asset(asset_id)
        self.log_activity(
            "missing_file_relinked",
            f"{asset.name} relinked",
            asset_id=asset_id,
        )
        return asset

    def relink_export(self, export_id: int, file_path: Path) -> ProjectExport:
        source = file_path.expanduser()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"File not found: {source}")
        normalized = normalize_file_path(source)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE project_exports
                SET file_path = ?,
                    normalized_file_path = ?,
                    filename = ?,
                    file_size = ?,
                    modified_at = ?
                WHERE id = ?
                """,
                (
                    str(source),
                    normalized,
                    source.name,
                    safe_file_size(source),
                    safe_modified_at(source),
                    export_id,
                ),
            )
            row = connection.execute(
                "SELECT * FROM project_exports WHERE id = ?",
                (export_id,),
            ).fetchone()
        export = ProjectExport.from_row(row)
        self.log_activity(
            "missing_file_relinked",
            f"{export.filename} export relinked",
            project_id=export.project_id,
        )
        return export

    def scan_for_file_detections(self, limit: int = 20) -> list[dict[str, object]]:
        detections: list[dict[str, object]] = []
        for folder in self.list_watched_folders(enabled_only=True):
            detections.extend(self._scan_watched_folder(folder, limit - len(detections)))
            if len(detections) >= limit:
                return detections[:limit]
        detections.extend(self._scan_project_folders_for_exports(limit - len(detections)))
        return detections[:limit]

    def mark_detection_status(self, file_path: Path, status: str) -> None:
        normalized = normalize_file_path(file_path)
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE detected_files
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE normalized_file_path = ?
                """,
                (status, normalized),
            )

    def suggest_project_for_export(self, file_path: Path) -> Project | None:
        projects = self.list_projects()
        return self._best_project_for_export(file_path, projects)

    def _scan_watched_folder(
        self,
        folder: WatchedFolder,
        remaining: int,
    ) -> list[dict[str, object]]:
        if remaining <= 0 or not folder.path.exists() or not folder.path.is_dir():
            return []

        detections: list[dict[str, object]] = []
        paths: list[Path]
        if folder.folder_type in {"raw", "export"}:
            paths = list_mp4_files(folder.path)
        else:
            paths = [
                item
                for item in folder.path.iterdir()
                if item.is_file()
            ]

        projects = self.list_projects() if folder.folder_type == "export" else []
        for path in paths:
            if len(detections) >= remaining:
                break
            detection_type = folder.folder_type
            project = (
                self._best_project_for_export(path, projects)
                if detection_type == "export"
                else None
            )
            detection = self._register_detection(
                path=path,
                detection_type=detection_type,
                watched_folder_id=folder.id,
                project_id=project.id if project else None,
            )
            if detection is not None:
                detection["project"] = project
                detection["folder"] = folder
                detections.append(detection)
        return detections

    def _scan_project_folders_for_exports(self, remaining: int) -> list[dict[str, object]]:
        if remaining <= 0:
            return []
        detections: list[dict[str, object]] = []
        projects = self.list_projects()
        for project in projects:
            if len(detections) >= remaining:
                break
            if not project.folder_path.exists() or not project.folder_path.is_dir():
                continue
            known = {normalize_file_path(project.raw_video_path)}
            if project.edited_video_path is not None:
                known.add(normalize_file_path(project.edited_video_path))
            for export in self.list_project_exports(project.id or 0):
                known.add(normalize_file_path(export.file_path))

            for path in list_mp4_files(project.folder_path):
                if len(detections) >= remaining:
                    break
                if normalize_file_path(path) in known:
                    continue
                detection = self._register_detection(
                    path=path,
                    detection_type="export",
                    watched_folder_id=None,
                    project_id=project.id,
                )
                if detection is not None:
                    detection["project"] = project
                    detection["folder"] = None
                    detections.append(detection)
        return detections

    def _register_detection(
        self,
        *,
        path: Path,
        detection_type: str,
        watched_folder_id: int | None,
        project_id: int | None,
    ) -> dict[str, object] | None:
        normalized = normalize_file_path(path)
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM detected_files
                WHERE normalized_file_path = ?
                """,
                (normalized,),
            ).fetchone()
            if existing is not None:
                if existing["status"] == "pending":
                    return {
                        "file_path": path,
                        "detection_type": existing["detection_type"],
                        "project_id": existing["project_id"],
                        "watched_folder_id": existing["watched_folder_id"],
                    }
                return None

            connection.execute(
                """
                INSERT INTO detected_files (
                    file_path,
                    normalized_file_path,
                    detection_type,
                    watched_folder_id,
                    project_id
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(path),
                    normalized,
                    detection_type,
                    watched_folder_id,
                    project_id,
                ),
            )

        if detection_type == "raw":
            self.log_activity(
                "raw_video_detected",
                f"{path.name} raw video detected",
            )
        elif detection_type == "export":
            self.log_activity(
                "export_detected",
                f"{path.name} possible export detected",
                project_id=project_id,
            )
        elif detection_type == "asset":
            self.log_activity(
                "asset_detected",
                f"{path.name} asset detected",
            )

        return {
            "file_path": path,
            "detection_type": detection_type,
            "project_id": project_id,
            "watched_folder_id": watched_folder_id,
        }

    def _best_project_for_export(
        self,
        file_path: Path,
        projects: list[Project],
    ) -> Project | None:
        filename = file_path.stem.casefold()
        parent = normalize_file_path(file_path.parent)
        best_project: Project | None = None
        best_score = 0.0

        for project in projects:
            score = 0.0
            project_name = project.name.casefold()
            client = project.client.casefold()

            if project_name and project_name in filename:
                score += 0.55
            score += SequenceMatcher(None, filename, project_name).ratio() * 0.45

            if client and client in filename:
                score += 0.2
            if normalize_file_path(project.folder_path) == parent:
                score += 0.5
            if project.updated_at:
                score += 0.05

            if score > best_score:
                best_score = score
                best_project = project

        return best_project if best_score >= 0.45 else None

    def _list_client_patterns(self) -> list[ClientPattern]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM client_patterns
                ORDER BY confidence DESC, id DESC
                """
            ).fetchall()
        return [ClientPattern.from_row(row) for row in rows]

    def _upsert_client_pattern(
        self,
        connection: sqlite3.Connection,
        client_id: int,
        pattern: str,
        pattern_type: str,
        confidence: float,
    ) -> None:
        cleaned = pattern.strip()
        if not cleaned:
            return
        row = connection.execute(
            """
            SELECT id, confidence
            FROM client_patterns
            WHERE client_id = ?
              AND pattern = ? COLLATE NOCASE
              AND pattern_type = ?
            """,
            (client_id, cleaned, pattern_type),
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO client_patterns (
                    client_id, pattern, pattern_type, confidence
                )
                VALUES (?, ?, ?, ?)
                """,
                (client_id, cleaned, pattern_type, confidence),
            )
            return

        new_confidence = min(0.98, max(float(row["confidence"] or 0), confidence) + 0.06)
        connection.execute(
            """
            UPDATE client_patterns
            SET confidence = ?
            WHERE id = ?
            """,
            (new_confidence, row["id"]),
        )

    def _asset_target_folder(self, target_folder: Path | None = None) -> Path:
        root = self.asset_library_root().resolve()
        folder = target_folder.expanduser().resolve() if target_folder else root
        if not _path_is_relative_to(folder, root):
            raise ValueError("Asset folder must stay inside the EditFlow Asset Library.")
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Asset target is not a folder: {folder}")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _store_asset_in_library(self, source: Path, target_folder: Path) -> Path:
        root = self.asset_library_root().resolve()
        source = source.expanduser().resolve()
        if _path_is_relative_to(source, root):
            return source
        if source.is_dir():
            return move_asset_folder_into_library(source, target_folder)
        if source.is_file():
            return copy_asset_file_into_library(source, target_folder)
        raise FileNotFoundError(f"Asset not found: {source}")

    def _sync_asset_descendants(self, folder: Path) -> None:
        paths = [
            path
            for path in sorted(folder.rglob("*"), key=lambda item: (len(item.parts), str(item).casefold()))
            if path.exists()
        ]
        if not paths:
            return
        with self.database.connect() as connection:
            for path in paths:
                self._upsert_asset_record(
                    connection,
                    path,
                    name=_asset_name_for_path(path),
                    category=infer_asset_category(path),
                    preserve_metadata=True,
                )

    def _upsert_asset_record(
        self,
        connection: sqlite3.Connection,
        path: Path,
        *,
        name: str,
        category: str,
        client: str = "",
        preserve_metadata: bool = False,
    ) -> int:
        asset_type = "folder" if path.is_dir() else "file"
        normalized = normalize_file_path(path)
        asset_id = self._find_asset_id_by_path(connection, path)
        if asset_id is None:
            cursor = connection.execute(
                """
                INSERT INTO assets (
                    name, file_path, normalized_file_path, asset_type, client, category
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    str(path),
                    normalized,
                    asset_type,
                    client.strip(),
                    category,
                ),
            )
            return int(cursor.lastrowid)

        if preserve_metadata:
            connection.execute(
                """
                UPDATE assets
                SET file_path = ?,
                    normalized_file_path = ?,
                    asset_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (str(path), normalized, asset_type, asset_id),
            )
        else:
            connection.execute(
                """
                UPDATE assets
                SET name = ?,
                    file_path = ?,
                    normalized_file_path = ?,
                    asset_type = ?,
                    client = ?,
                    category = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    name,
                    str(path),
                    normalized,
                    asset_type,
                    client.strip(),
                    category,
                    asset_id,
                ),
            )
        return asset_id

    def _update_asset_path_references(self, old_path: Path, new_path: Path) -> list[int]:
        old_path = old_path.expanduser().resolve()
        new_path = new_path.expanduser().resolve()
        affected: list[tuple[int, Path, Path]] = []
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id, file_path FROM assets").fetchall()
            for row in rows:
                current = Path(row["file_path"])
                if not _path_is_same_or_child(current, old_path):
                    continue
                relative = Path()
                if normalize_file_path(current) != normalize_file_path(old_path):
                    relative = current.relative_to(old_path)
                updated_path = new_path / relative
                affected.append((int(row["id"]), current, updated_path))

            for asset_id, _current, updated_path in affected:
                connection.execute(
                    """
                    UPDATE assets
                    SET file_path = ?,
                        normalized_file_path = ?,
                        asset_type = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        str(updated_path),
                        normalize_file_path(updated_path),
                        "folder" if updated_path.is_dir() else "file",
                        asset_id,
                    ),
                )
        return [asset_id for asset_id, _current, _updated in affected]

    def _expanded_asset_ids(self, asset_ids: list[int]) -> list[int]:
        if not asset_ids:
            return []
        selected = [self._require_asset(asset_id) for asset_id in dict.fromkeys(asset_ids)]
        expanded: set[int] = {asset.id for asset in selected if asset.id is not None}
        folders = [asset.file_path for asset in selected if asset.is_collection]
        if not folders:
            return sorted(expanded)

        with self.database.connect() as connection:
            rows = connection.execute("SELECT id, file_path FROM assets").fetchall()
        for row in rows:
            path = Path(row["file_path"])
            if any(_path_is_same_or_child(path, folder) for folder in folders):
                expanded.add(int(row["id"]))
        return sorted(expanded)

    def _top_level_assets(self, asset_ids: list[int]) -> list[Asset]:
        assets = [self._require_asset(asset_id) for asset_id in dict.fromkeys(asset_ids)]
        top_level: list[Asset] = []
        for asset in assets:
            if any(
                other.id != asset.id
                and other.is_collection
                and _path_is_same_or_child(asset.file_path, other.file_path)
                for other in assets
            ):
                continue
            top_level.append(asset)
        return top_level

    def _remove_project_asset_links(self, asset_ids: list[int]) -> None:
        if not asset_ids:
            return
        placeholders = ", ".join("?" for _id in asset_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT linked_path
                FROM project_assets
                WHERE asset_id IN ({placeholders})
                """,
                tuple(asset_ids),
            ).fetchall()
        for row in rows:
            linked_path = row["linked_path"] or ""
            if not linked_path:
                continue
            try:
                remove_project_asset_link(Path(linked_path))
            except OSError as error:
                self._file_warnings.append(
                    f"Could not remove project-side asset shortcut: {error}"
                )

    def _refresh_project_asset_links(self, asset_ids: list[int]) -> None:
        if not asset_ids:
            return
        placeholders = ", ".join("?" for _id in asset_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    pa.project_id,
                    pa.asset_id,
                    pa.linked_path,
                    p.folder_path,
                    a.file_path,
                    a.name
                FROM project_assets pa
                INNER JOIN projects p ON p.id = pa.project_id
                INNER JOIN assets a ON a.id = pa.asset_id
                WHERE pa.asset_id IN ({placeholders})
                """,
                tuple(asset_ids),
            ).fetchall()

        for row in rows:
            old_link = row["linked_path"] or ""
            if old_link:
                try:
                    remove_project_asset_link(Path(old_link))
                except OSError:
                    pass
            linked_path = ""
            try:
                linked_path = str(
                    create_project_asset_link(
                        Path(row["folder_path"]),
                        Path(row["file_path"]),
                    )
                )
            except OSError as error:
                self._file_warnings.append(
                    f"Updated {row['name']} in EditFlow, but could not refresh a project-side shortcut: {error}"
                )

            with self.database.connect() as connection:
                connection.execute(
                    """
                    UPDATE project_assets
                    SET linked_path = ?
                    WHERE project_id = ? AND asset_id = ?
                    """,
                    (linked_path, row["project_id"], row["asset_id"]),
                )

    def _next_project_sort_order(
        self,
        connection: sqlite3.Connection,
        status: str,
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sort_order), -100) + 100 AS next_order
            FROM projects
            WHERE status = ?
            """,
            (status,),
        ).fetchone()
        return int(row["next_order"] if row is not None else 0)

    def _delete_project_folder(self, project: Project) -> None:
        folder_value = str(project.folder_path).strip()
        if not folder_value or folder_value == ".":
            raise ValueError("Project folder path is empty.")

        folder = project.folder_path.expanduser()
        if not folder.exists():
            return
        if folder.is_symlink():
            raise ValueError("Refusing to delete a project folder symlink.")
        if not folder.is_dir():
            raise ValueError(f"Project folder is not a directory: {folder}")

        resolved = folder.resolve()
        protected_paths = {
            Path.home().resolve(),
            (Path.home() / "Videos").resolve(),
            self.projects_root().resolve(),
            self.asset_library_root().resolve(),
        }
        if resolved.parent == resolved or resolved in protected_paths:
            raise ValueError(f"Refusing to delete a protected folder: {resolved}")

        linked_assets_folder = resolved / LINKED_ASSETS_FOLDER_NAME
        raw_inside = self._project_file_is_inside_folder(
            project.raw_video_path,
            resolved,
        )
        edited_inside = (
            project.edited_video_path is not None
            and self._project_file_is_inside_folder(project.edited_video_path, resolved)
        )
        if not linked_assets_folder.exists() and not raw_inside and not edited_inside:
            raise ValueError(
                "Project folder does not look like an EditFlow project folder."
            )

        shutil.rmtree(resolved)

    def _project_file_is_inside_folder(self, file_path: Path, folder: Path) -> bool:
        value = str(file_path).strip()
        if not value or value == ".":
            return False
        try:
            resolved_file = file_path.expanduser().resolve()
        except OSError:
            return False
        return _path_is_same_or_child(resolved_file, folder)

    def _require_project(self, project_id: int) -> Project:
        project = self.get_project(project_id)
        if project is None:
            raise LookupError(f"Project not found: {project_id}")
        return project

    def _file_mode_setting(self, key: str) -> str:
        value = self.get_setting(key, "copy")
        return value if value in {"copy", "move"} else "copy"

    def _set_file_mode_setting(self, key: str, value: str) -> None:
        if value not in {"copy", "move"}:
            raise ValueError(f"Unknown file mode: {value}")
        self.set_setting(key, value)

    def _earning_rate_setting(self, video_type_key: str) -> float:
        setting_key = self.EARNING_RATE_KEYS.get(video_type_key)
        if setting_key is None:
            return 0.0
        value = self.get_setting(setting_key, f"{self.DEFAULT_EARNING_RATE:.2f}")
        try:
            return max(0.0, float(value))
        except ValueError:
            return self.DEFAULT_EARNING_RATE

    def _decorate_project_payment(self, project: Project) -> Project:
        if (
            project.estimated_payment <= 0
            and not project.estimated_payment_custom
            and project.status != PUBLISHED_STATUS
        ):
            project.estimated_payment = self.estimated_payment_for_video_type(
                project.video_type
            )
        return project

    def _require_revision(self, revision_id: int) -> ProjectRevision:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM project_revisions WHERE id = ?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Revision not found: {revision_id}")
        return ProjectRevision.from_row(row)

    def _record_status_history(self, project_id: int, status: str) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT status
                FROM project_status_history
                WHERE project_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if row is not None and row["status"] == status:
                return
            connection.execute(
                """
                INSERT INTO project_status_history (project_id, status)
                VALUES (?, ?)
                """,
                (project_id, status),
            )

    def _list_project_status_history(self, project_id: int) -> list[tuple[str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, created_at
                FROM project_status_history
                WHERE project_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (project_id,),
            ).fetchall()
        return [(row["status"], row["created_at"] or "") for row in rows]

    def _require_asset(self, asset_id: int) -> Asset:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise LookupError(f"Asset not found: {asset_id}")
        return Asset.from_row(row)

    def _find_asset_id_by_path(
        self,
        connection: sqlite3.Connection,
        source: Path,
    ) -> int | None:
        normalized_path = normalize_file_path(source)
        row = connection.execute(
            """
            SELECT id FROM assets
            WHERE normalized_file_path = ? OR file_path = ?
            ORDER BY normalized_file_path = ? DESC
            LIMIT 1
            """,
            (normalized_path, str(source), normalized_path),
        ).fetchone()
        if row is not None:
            return int(row["id"])

        legacy_rows = connection.execute(
            """
            SELECT id, file_path
            FROM assets
            WHERE normalized_file_path = ''
            """
        ).fetchall()
        for legacy_row in legacy_rows:
            try:
                legacy_normalized = normalize_file_path(Path(legacy_row["file_path"]))
            except OSError:
                continue
            if legacy_normalized != normalized_path:
                continue
            connection.execute(
                """
                UPDATE assets
                SET normalized_file_path = ?
                WHERE id = ?
                """,
                (normalized_path, legacy_row["id"]),
            )
            return int(legacy_row["id"])
        return None


def _normalize_match_text(value: str) -> str:
    cleaned = re.sub(r"[_\-\.]+", " ", value.casefold())
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_tag_values(tags: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(tags, str):
        raw_tags = tags.split(",")
    else:
        raw_tags = list(tags)

    normalized: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        cleaned = _normalize_match_text(str(tag))
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def _keyword_tokens(value: str) -> set[str]:
    ignored = {
        "ad",
        "edit",
        "final",
        "project",
        "raw",
        "video",
        "v",
    }
    return {
        token
        for token in _normalize_match_text(value).split()
        if len(token) >= 3 and not token.isdigit() and token not in ignored
    }


def _matching_tags(project_keywords: set[str], tags: list[str]) -> list[str]:
    matches: list[str] = []
    for tag in tags:
        normalized_tag = _normalize_match_text(tag)
        tag_tokens = {
            token
            for token in normalized_tag.split()
            if len(token) >= 3 and not token.isdigit()
        }
        if tag_tokens & project_keywords:
            matches.append(tag)
            continue
        if normalized_tag and normalized_tag in " ".join(sorted(project_keywords)):
            matches.append(tag)
    return matches


def _video_type_key(video_type: str) -> str:
    normalized = _normalize_match_text(video_type)
    if normalized == "ugc":
        return "ugc"
    if normalized == "personal brand":
        return "personal_brand"
    return ""


def _is_recent_link(value: str, days: int = 30) -> bool:
    if not value:
        return False
    try:
        linked_at = datetime.fromisoformat(value)
    except ValueError:
        return False
    return datetime.now() - linked_at <= timedelta(days=days)


def _project_word(count: int) -> str:
    return "project" if count == 1 else "projects"


def _status_timeline_label(status: str) -> str:
    labels = {
        "Need Edit": "Need edit",
        "Editing": "Editing started",
        "Need Upload": "Ready for upload",
        "Done": "Done",
        PUBLISHED_STATUS: "Published",
        "Archived": "Published",
    }
    return labels.get(status, f"Moved to {status}")


def _dedupe_timeline(entries: list[TimelineEntry]) -> list[TimelineEntry]:
    deduped: list[TimelineEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries:
        key = (entry.label, entry.detail, entry.created_at)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _contains_pattern(text: str, pattern: str) -> bool:
    if not text or not pattern:
        return False
    return f" {pattern} " in f" {text} "


def _asset_name_for_path(path: Path) -> str:
    return path.name if path.is_dir() else path.stem


def _clean_library_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(parent.expanduser().resolve())
        return True
    except ValueError:
        return False


def _path_is_same_or_child(path: Path, parent: Path) -> bool:
    path = path.expanduser().resolve()
    parent = parent.expanduser().resolve()
    return path == parent or _path_is_relative_to(path, parent)


def _infer_client_name_from_path(path: Path) -> tuple[str, float, str] | None:
    generic = {
        "asset",
        "assets",
        "audio",
        "broll",
        "client",
        "clients",
        "edit",
        "edited",
        "export",
        "exports",
        "final",
        "footage",
        "graphics",
        "project",
        "projects",
        "raw",
        "video",
        "videos",
    }
    parts = [part.strip(" .") for part in path.parts[:-1] if part.strip(" .")]
    normalized_parts = [_normalize_match_text(part) for part in parts]

    for index, part in enumerate(normalized_parts[:-1]):
        if part in {"client", "clients"}:
            candidate = parts[index + 1]
            if _normalize_match_text(candidate) not in generic:
                return candidate, 0.78, "client folder structure"

    for index in range(len(parts) - 1, -1, -1):
        normalized = normalized_parts[index]
        if normalized in generic:
            continue
        if index + 1 < len(normalized_parts) and normalized_parts[index + 1] in {"raw", "exports", "export", "assets", "asset"}:
            return parts[index], 0.72, "parent folder near media folder"

    first_token = re.split(r"[\s_\-.]+", path.stem.strip())[0]
    normalized_token = _normalize_match_text(first_token)
    if len(normalized_token) >= 3 and normalized_token not in generic:
        return first_token, 0.62, "filename prefix"
    return None
