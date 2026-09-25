from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


STATUSES: tuple[str, ...] = (
    "Need Edit",
    "Editing",
    "Need Upload",
    "Done",
)
PUBLISHED_STATUS = "Published"
LEGACY_ARCHIVED_STATUS = "Archived"
ARCHIVED_STATUS = PUBLISHED_STATUS
ALL_STATUSES: tuple[str, ...] = (
    *STATUSES,
    PUBLISHED_STATUS,
    LEGACY_ARCHIVED_STATUS,
)
WORKFLOW_STAGE_ICONS: dict[str, str] = {
    "Need Edit": "✂️",
    "Editing": "🎬",
    "Need Upload": "⬆️",
    "Done": "✅",
    PUBLISHED_STATUS: "🌐",
}


def workflow_stage_label(status: str) -> str:
    icon = WORKFLOW_STAGE_ICONS.get(status, "")
    return f"{icon} {status}" if icon else status

FOLDER_TYPES: tuple[tuple[str, str], ...] = (
    ("raw", "Raw Video Inbox"),
    ("export", "Export Folder"),
    ("asset", "Asset Folder"),
)

FOLDER_TYPE_LABELS = dict(FOLDER_TYPES)


def _path_exists(path: Path | None) -> bool:
    if path is None:
        return False
    return bool(path.name and path.suffix) and (path.exists() or path.is_symlink())


def project_has_existing_raw_video(project: "Project") -> bool:
    return _path_exists(project.raw_video_path)


def project_has_existing_edited_video(project: "Project") -> bool:
    return _path_exists(project.edited_video_path)


def project_ready_for_status_transition(
    project: "Project",
    from_status: str,
) -> tuple[bool, str]:
    if project.open_revision_count > 0:
        return False, "Complete revision notes before moving this card forward."

    if from_status == "Need Edit":
        if not project_has_existing_raw_video(project):
            return False, "Add a raw video before moving to Editing."
        if project.asset_count <= 0:
            return False, "Link at least one asset before moving to Editing."
        return True, "Ready to move to Editing."

    if from_status == "Editing":
        if not project_has_existing_edited_video(project):
            return False, "Add an edited video before moving to Need Upload."
        return True, "Ready to move to Need Upload."

    if from_status == "Need Upload":
        if not project_has_existing_edited_video(project):
            return False, "Add an edited video before moving to Done."
        return True, "Ready to move to Done."

    return False, ""


def workflow_move_block_reason(project: "Project", target_status: str) -> str | None:
    if project.status not in STATUSES or target_status not in STATUSES:
        return None

    current_index = STATUSES.index(project.status)
    target_index = STATUSES.index(target_status)
    if (
        project.open_revision_count > 0
        and target_status == "Need Edit"
        and project.status != "Need Edit"
    ):
        return "Complete revision notes before moving this card back to Need Edit."
    if target_index <= current_index:
        return None

    for status in STATUSES[current_index:target_index]:
        ready, message = project_ready_for_status_transition(project, status)
        if not ready:
            return message
    return None


@dataclass(slots=True)
class Project:
    id: int | None
    name: str
    client: str
    status: str
    folder_path: Path
    raw_video_path: Path
    video_type: str = ""
    client_id: int | None = None
    edited_video_path: Path | None = None
    notes: str = ""
    asset_count: int = 0
    open_revision_count: int = 0
    priority: bool = False
    in_flow: bool = False
    sort_order: int = 0
    estimated_payment: float = 0.0
    estimated_payment_custom: bool = False
    earned_amount: float = 0.0
    earned_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def raw_video_name(self) -> str:
        if not self.raw_video_path.name or not self.raw_video_path.suffix:
            return "No raw video"
        return self.raw_video_path.name

    @property
    def edited_video_name(self) -> str:
        if self.edited_video_path is None:
            return "Not linked"
        return self.edited_video_path.name

    @property
    def has_edited_video(self) -> bool:
        return self.edited_video_path is not None and str(self.edited_video_path) != ""

    @classmethod
    def from_row(cls, row: Any) -> "Project":
        edited_value = _row_get(row, "edited_video_path", "") or ""
        status = _row_get(row, "status", STATUSES[0]) or STATUSES[0]
        if status == LEGACY_ARCHIVED_STATUS:
            status = PUBLISHED_STATUS
        if status not in ALL_STATUSES:
            status = STATUSES[0]

        return cls(
            id=_row_get(row, "id"),
            name=_row_get(row, "name", ""),
            client=_row_get(row, "client", ""),
            status=status,
            video_type=_row_get(row, "video_type", "") or "",
            folder_path=Path(_row_get(row, "folder_path", "")),
            raw_video_path=Path(_row_get(row, "raw_video_path", "")),
            client_id=_row_get(row, "client_id"),
            edited_video_path=Path(edited_value) if edited_value else None,
            notes=_row_get(row, "notes", "") or "",
            asset_count=int(_row_get(row, "asset_count", 0) or 0),
            open_revision_count=int(_row_get(row, "open_revision_count", 0) or 0),
            priority=bool(_row_get(row, "priority", 0)),
            in_flow=bool(_row_get(row, "in_flow", 0)),
            sort_order=int(_row_get(row, "sort_order", 0) or 0),
            estimated_payment=float(_row_get(row, "estimated_payment", 0.0) or 0.0),
            estimated_payment_custom=bool(_row_get(row, "estimated_payment_custom", 0)),
            earned_amount=float(_row_get(row, "earned_amount", 0.0) or 0.0),
            earned_at=_row_get(row, "earned_at", "") or "",
            created_at=_row_get(row, "created_at", "") or "",
            updated_at=_row_get(row, "updated_at", "") or "",
        )


@dataclass(slots=True)
class Client:
    id: int | None
    name: str
    default_asset_folder: Path | None = None
    default_export_folder: Path | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "Client":
        asset_folder = _row_get(row, "default_asset_folder", "") or ""
        export_folder = _row_get(row, "default_export_folder", "") or ""
        return cls(
            id=_row_get(row, "id"),
            name=_row_get(row, "name", "") or "",
            default_asset_folder=Path(asset_folder) if asset_folder else None,
            default_export_folder=Path(export_folder) if export_folder else None,
            created_at=_row_get(row, "created_at", "") or "",
        )


@dataclass(slots=True)
class ClientPattern:
    id: int | None
    client_id: int
    pattern: str
    pattern_type: str
    confidence: float
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ClientPattern":
        return cls(
            id=_row_get(row, "id"),
            client_id=int(_row_get(row, "client_id", 0) or 0),
            pattern=_row_get(row, "pattern", "") or "",
            pattern_type=_row_get(row, "pattern_type", "") or "",
            confidence=float(_row_get(row, "confidence", 0.0) or 0.0),
            created_at=_row_get(row, "created_at", "") or "",
        )


@dataclass(slots=True)
class ClientSuggestion:
    client: Client
    confidence: float
    reason: str


@dataclass(slots=True)
class ClientSummary:
    client: Client
    active_projects: int = 0
    completed_projects: int = 0


@dataclass(slots=True)
class Asset:
    id: int | None
    name: str
    file_path: Path
    asset_type: str = "file"
    client: str = ""
    category: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def filename(self) -> str:
        return self.file_path.name if str(self.file_path) else ""

    @property
    def is_collection(self) -> bool:
        return self.asset_type == "folder"

    @classmethod
    def from_row(cls, row: Any) -> "Asset":
        return cls(
            id=_row_get(row, "id"),
            name=_row_get(row, "name", ""),
            file_path=Path(_row_get(row, "file_path", "")),
            asset_type=_row_get(row, "asset_type", "file") or "file",
            client=_row_get(row, "client", "") or "",
            category=_row_get(row, "category", "") or "",
            created_at=_row_get(row, "created_at", "") or "",
            updated_at=_row_get(row, "updated_at", "") or "",
        )


@dataclass(slots=True)
class AssetSuggestion:
    asset: Asset
    score: int
    reasons: list[str]
    client_usage: int = 0
    total_usage: int = 0
    last_linked_at: str = ""


@dataclass(slots=True)
class WatchedFolder:
    id: int | None
    path: Path
    folder_type: str
    enabled: bool = True
    created_at: str = ""

    @property
    def label(self) -> str:
        return FOLDER_TYPE_LABELS.get(self.folder_type, self.folder_type)

    @classmethod
    def from_row(cls, row: Any) -> "WatchedFolder":
        return cls(
            id=_row_get(row, "id"),
            path=Path(_row_get(row, "path", "")),
            folder_type=_row_get(row, "folder_type", "asset") or "asset",
            enabled=bool(_row_get(row, "enabled", 1)),
            created_at=_row_get(row, "created_at", "") or "",
        )


@dataclass(slots=True)
class ProjectExport:
    id: int | None
    project_id: int
    file_path: Path
    filename: str
    detected_at: str
    version_label: str = "Unknown"
    file_size: int = 0
    modified_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ProjectExport":
        return cls(
            id=_row_get(row, "id"),
            project_id=int(_row_get(row, "project_id", 0) or 0),
            file_path=Path(_row_get(row, "file_path", "")),
            filename=_row_get(row, "filename", "") or "",
            detected_at=_row_get(row, "detected_at", "") or "",
            version_label=_row_get(row, "version_label", "Unknown") or "Unknown",
            file_size=int(_row_get(row, "file_size", 0) or 0),
            modified_at=_row_get(row, "modified_at", "") or "",
        )


@dataclass(slots=True)
class ProjectRevision:
    id: int | None
    project_id: int
    note: str
    created_at: str = ""
    completed: bool = False
    completed_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ProjectRevision":
        return cls(
            id=_row_get(row, "id"),
            project_id=int(_row_get(row, "project_id", 0) or 0),
            note=_row_get(row, "note", "") or "",
            created_at=_row_get(row, "created_at", "") or "",
            completed=bool(_row_get(row, "completed", 0)),
            completed_at=_row_get(row, "completed_at", "") or "",
        )


@dataclass(slots=True)
class ActivityEntry:
    id: int | None
    event_type: str
    message: str
    project_id: int | None = None
    asset_id: int | None = None
    created_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ActivityEntry":
        return cls(
            id=_row_get(row, "id"),
            event_type=_row_get(row, "event_type", "") or "",
            message=_row_get(row, "message", "") or "",
            project_id=_row_get(row, "project_id"),
            asset_id=_row_get(row, "asset_id"),
            created_at=_row_get(row, "created_at", "") or "",
        )


@dataclass(slots=True)
class DetectionEntry:
    id: int | None
    file_path: Path
    detection_type: str
    status: str
    project_id: int | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "DetectionEntry":
        return cls(
            id=_row_get(row, "id"),
            file_path=Path(_row_get(row, "file_path", "")),
            detection_type=_row_get(row, "detection_type", "") or "",
            status=_row_get(row, "status", "") or "",
            project_id=_row_get(row, "project_id"),
            created_at=_row_get(row, "created_at", "") or "",
            updated_at=_row_get(row, "updated_at", "") or "",
        )


@dataclass(slots=True)
class FileIssue:
    kind: str
    label: str
    path: Path
    project_id: int | None = None
    asset_id: int | None = None
    export_id: int | None = None

    @property
    def can_relink(self) -> bool:
        return self.kind in {
            "project_folder",
            "raw_video",
            "edited_video",
            "asset",
            "export",
        }


@dataclass(slots=True)
class WorkflowSuggestion:
    project_id: int
    project_name: str
    message: str
    target_status: str | None = None
    severity: str = "info"


@dataclass(slots=True)
class TimelineEntry:
    label: str
    detail: str = ""
    created_at: str = ""
    kind: str = ""


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        if key in row.keys():
            return row[key]
    except AttributeError:
        return getattr(row, key, default)
    return default
