from __future__ import annotations

from pathlib import Path
import re
import shutil
from datetime import datetime


LINKED_ASSETS_FOLDER_NAME = "Linked Assets"
FILE_OPERATION_MODES = ("copy", "move")


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "Untitled Video"
    if cleaned.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned} Project"
    return cleaned


def ensure_mp4(path: Path) -> Path:
    video_path = path.expanduser()
    if video_path.suffix.casefold() != ".mp4":
        raise ValueError("Choose an MP4 video file.")
    if not video_path.exists() or not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    return video_path


def normalize_file_path(path: Path) -> str:
    return str(path.expanduser().resolve()).casefold()


def is_mp4_file(path: Path) -> bool:
    return path.suffix.casefold() == ".mp4"


def is_image_file(path: Path) -> bool:
    return path.suffix.casefold() in {
        ".bmp",
        ".gif",
        ".jpeg",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }


def is_video_file(path: Path) -> bool:
    return path.suffix.casefold() in {
        ".mp4",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".webm",
    }


def infer_asset_category(path: Path) -> str:
    if path.is_dir():
        return "Collection"
    suffix = path.suffix.casefold()
    if is_image_file(path):
        return "Image"
    if is_video_file(path):
        return "Video"
    if suffix in {".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".wav", ".wma"}:
        return "Audio"
    return "Resource"


def safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def safe_modified_at(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""


def detect_version_label(path: Path) -> str:
    name = path.stem.casefold()
    version_match = re.search(r"\bv(?:ersion)?\s*0?(\d{1,2})\b", name)
    if version_match:
        return f"V{int(version_match.group(1)):02d}"
    loose_match = re.search(r"\b0?(\d{1,2})\b", name)
    if loose_match and any(token in name for token in ("edit", "export", "cut")):
        return f"V{int(loose_match.group(1)):02d}"
    if re.search(r"\bfinal\b", name):
        return "Final"
    return "Unknown"


def list_mp4_files(folder: Path) -> list[Path]:
    base = folder.expanduser()
    if not base.exists() or not base.is_dir():
        return []
    files: list[Path] = []
    for path in base.iterdir():
        if path.is_file() and is_mp4_file(path):
            files.append(path)
    return sorted(
        files,
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )


def default_projects_root() -> Path:
    return Path.home() / "Videos" / "EditFlow Projects"


def default_asset_library_root() -> Path:
    return Path.home() / "Videos" / "EditFlow" / "Assets"


def ensure_asset_library_root(asset_root: Path) -> Path:
    root = asset_root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_linked_assets_folder(project_folder: Path) -> Path:
    folder = project_folder.expanduser() / LINKED_ASSETS_FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def create_project_folder(projects_root: Path, project_name: str) -> Path:
    root = projects_root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    folder = root / sanitize_folder_name(project_name)
    folder = _deduplicate_folder_path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    ensure_linked_assets_folder(folder)
    return folder


def import_raw_video(
    raw_video_path: Path,
    project_name: str,
    projects_root: Path | None = None,
    mode: str = "copy",
) -> tuple[Path, Path]:
    source = ensure_mp4(raw_video_path)
    if projects_root is None:
        projects_root = source.parent

    project_folder = create_project_folder(projects_root, project_name)
    return project_folder, transfer_file_into_folder(source, project_folder, mode)


def import_edited_video(
    edited_video_path: Path,
    project_folder: Path,
    mode: str = "copy",
) -> Path:
    source = ensure_mp4(edited_video_path)
    target_folder = project_folder.expanduser()
    target_folder.mkdir(parents=True, exist_ok=True)
    ensure_linked_assets_folder(target_folder)
    return transfer_file_into_folder(source, target_folder, mode)


def find_unassigned_mp4s(
    project_folder: Path,
    raw_video_path: Path,
    edited_video_path: Path | None,
) -> list[Path]:
    folder = project_folder.expanduser()
    if not folder.exists() or not folder.is_dir():
        return []

    known_paths = {normalize_file_path(raw_video_path)}
    if edited_video_path is not None:
        known_paths.add(normalize_file_path(edited_video_path))

    candidates: list[Path] = []
    for path in folder.glob("*.mp4"):
        try:
            normalized = normalize_file_path(path)
        except OSError:
            continue
        if normalized not in known_paths and path.is_file():
            candidates.append(path)

    return sorted(
        candidates,
        key=lambda item: item.stat().st_mtime if item.exists() else 0,
        reverse=True,
    )


def backup_database(db_path: Path, destination: Path) -> Path:
    source = db_path.expanduser().resolve()
    target = destination.expanduser()
    if target.is_dir() or str(target).endswith(("\\", "/")):
        target = target / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def copy_file_into_folder(source: Path, target_folder: Path) -> Path:
    source = source.expanduser().resolve()
    target_folder = target_folder.expanduser().resolve()
    target = target_folder / source.name

    if source == target:
        return source

    target = _deduplicate_path(target)
    shutil.copy2(source, target)
    return target


def move_file_into_folder(source: Path, target_folder: Path) -> Path:
    source = source.expanduser().resolve()
    target_folder = target_folder.expanduser().resolve()
    target = target_folder / source.name

    if source == target:
        return source

    target = _deduplicate_path(target)
    shutil.move(str(source), str(target))
    return target


def transfer_file_into_folder(source: Path, target_folder: Path, mode: str) -> Path:
    if mode not in FILE_OPERATION_MODES:
        raise ValueError(f"Unknown file operation mode: {mode}")
    if mode == "move":
        return move_file_into_folder(source, target_folder)
    return copy_file_into_folder(source, target_folder)


def copy_asset_file_into_library(source: Path, target_folder: Path) -> Path:
    source = source.expanduser().resolve()
    target_folder = target_folder.expanduser().resolve()
    target_folder.mkdir(parents=True, exist_ok=True)
    return copy_file_into_folder(source, target_folder)


def move_asset_folder_into_library(source: Path, target_folder: Path) -> Path:
    source = source.expanduser().resolve()
    target_folder = target_folder.expanduser().resolve()
    target_folder.mkdir(parents=True, exist_ok=True)
    target = _deduplicate_folder_path(target_folder / source.name)

    if source == target:
        return source
    if _is_relative_to(target, source):
        raise ValueError("Cannot move an asset folder into itself.")

    shutil.move(str(source), str(target))
    return target


def safe_child_path(parent: Path, name: str, *, is_folder: bool) -> Path:
    parent = parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / name
    return _deduplicate_folder_path(candidate) if is_folder else _deduplicate_path(candidate)


def create_project_asset_link(project_folder: Path, asset_path: Path) -> Path:
    source = asset_path.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"Asset file not found: {source}")

    linked_assets_folder = ensure_linked_assets_folder(project_folder)
    target = _deduplicate_link_path(linked_assets_folder / source.name)
    try:
        target.symlink_to(source, target_is_directory=source.is_dir())
        return target
    except OSError as symlink_error:
        shortcut = _deduplicate_link_path(linked_assets_folder / f"{source.name}.url")
        try:
            _create_file_url_shortcut(shortcut, source)
            return shortcut
        except OSError as shortcut_error:
            raise OSError(
                f"{symlink_error}; fallback shortcut failed: {shortcut_error}"
            ) from symlink_error


def remove_project_asset_link(link_path: Path | None) -> None:
    if link_path is None or not str(link_path):
        return
    path = link_path.expanduser()
    if path.is_symlink() or (path.suffix.casefold() == ".url" and path.is_file()):
        path.unlink()


def _create_file_url_shortcut(shortcut_path: Path, source: Path) -> None:
    shortcut_path.write_text(
        "\n".join(
            [
                "[InternetShortcut]",
                f"URL={source.as_uri()}",
                "IconFile=C:\\Windows\\System32\\shell32.dll",
                f"IconIndex={3 if source.is_dir() else 0}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _deduplicate_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem} {index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an available file name for {path}")


def _deduplicate_folder_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(2, 1000):
        candidate = path.with_name(f"{path.name} {index}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an available folder name for {path}")


def _deduplicate_link_path(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path

    suffix = path.suffix
    stem = path.stem if suffix else path.name
    for index in range(2, 1000):
        candidate_name = f"{stem} {index}{suffix}"
        candidate = path.with_name(candidate_name)
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
    raise FileExistsError(f"Could not find an available link name for {path}")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
