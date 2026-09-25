# EditFlow

Lightweight Windows desktop organizer for video editing jobs.

Each job starts from one raw MP4. EditFlow creates a normal project folder, copies the raw video into it, and lets you track the job through:

- Need Edit
- Editing
- Need Upload
- Done

Edited MP4 files are copied into the same project folder. Assets are stored separately and linked through SQLite, so unlinking an asset never deletes the original file.

## V2

- Drag project cards between workflow columns.
- Drop files onto project cards to set edited MP4s or link assets.
- Drop asset-library items onto cards to link them without copying files.
- Detect new MP4s in the selected project folder and ask before assigning them.
- Cache lightweight thumbnails for videos and image assets.
- Search projects and filter by client/status.
- Show recently linked assets.
- Back up the local SQLite database from Settings.

## V3.1

- Add watched folders for raw inboxes, export folders, and asset folders.
- Show advisory in-app detections for new raw videos, possible exports, and assets.
- Store export history per project without copying or moving export files.
- Display file-health warnings for missing raw, edited, asset, folder, and export paths.
- Relink broken paths while preserving project, asset, and export records.
- Track recent activity for useful editing-organization events.

## V3.2

- Add client profiles with optional default asset and export folders.
- Suggest clients during raw MP4 import from filename, folder structure, prior projects, and learned patterns.
- Learn simple deterministic filename/folder patterns from confirmed client choices.
- Add a Clients screen with active/completed counts, recent assets, recent exports, and defaults.

## V3.3

- Add optional asset tags for deterministic project keyword matching.
- Suggest useful assets in the project details panel with visible reasons.
- Rank suggestions by same-client usage, matching tags, recent use, and total use.
- Show Recent Assets and Most Used Assets in the Asset Library.
- Confirm user intent before linking suggested or dragged assets.

## V3.4

- Record client revision feedback and mark revisions complete.
- Suggest workflow status actions without automatically moving cards.
- Add an actionable Dashboard with workflow counts, detections, suggestions, broken links, and activity.
- Generate a project timeline from raw imports, status history, linked assets, exports, revisions, and done states.

## V3.5

- Center project creation around a configured Projects Root.
- Drop raw MP4 files onto Need Edit to create project folders and cards.
- Store raw and edited videos directly inside the project folder with safe filename conflict handling.
- Add Settings controls for copy/move behavior for raw and edited files.
- Register multiple asset files or a folder collection from the Asset Library.
- Link assets through SQLite and create project-side symlinks inside `Linked Assets/` when the OS allows it.
- Remove only project-side links when unlinking assets; original asset files are never deleted.
- Support dropped files/folders on project cards, including edited-video replacement/export choices.

## Managed Asset Library

- Store imported asset files and folders inside the configured EditFlow Asset Library root.
- Import files, import folders, create folders, rename, move, delete, multi-select, and open assets in Explorer from the Assets tab.
- Move imported folders into the Asset Library while preserving their folder structure.
- Drag one asset, multiple assets, or folder collections onto project cards to link them.
- Keep project links as SQLite relationships plus project-side shortcuts; assets are never copied into project folders.
- Rename or move assets inside the library while preserving existing project relationships.
- Confirm before deleting linked assets and remove only the library asset records, project relationships, and project-side shortcuts.

## Publish

- Right-click any project card and choose `Revision` to record required client feedback, move the card back to Editing, and mark it amber until the revision is completed.
- Hover the card note icon to preview notes, or click it to edit notes quickly.
- Cards only show note and priority icons when notes or priority are present; right-click cards for folder, note, priority, asset-link, revision, and publish actions.
- Right-click a Done project card and choose `Mark as Publish` after the client has posted it.
- Published projects leave the active Kanban board and appear on the Publish page.
- Restore published projects back to Done when needed.
- Publish and restore only update SQLite status records; project folders and media files are never moved or deleted.

## Run

```powershell
pip install -r requirements.txt
python main.py
```

Project metadata is stored in `data/editflow.db`.

## App Artwork

- Put the app logo at `assets/logos/editflow_logo.png`.
- Put reusable UI icons in `assets/icons/`.
- Imported editing assets are managed in the configured EditFlow Asset Library folder, not in the app artwork folder.
