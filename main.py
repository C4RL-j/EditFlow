from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.database import Database
from core.project_service import ProjectService
from ui.main_window import MainWindow


APP_ROOT = Path(__file__).resolve().parent
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", APP_ROOT))
DATA_ROOT = Path(sys.executable).resolve().parent / "data" if getattr(sys, "frozen", False) else APP_ROOT / "data"
DB_PATH = DATA_ROOT / "editflow.db"
LOGO_PATH = RESOURCE_ROOT / "assets" / "logos" / "Logo.png"


def main() -> int:
    app = QApplication(sys.argv)
    if LOGO_PATH.exists():
        app.setWindowIcon(QIcon(str(LOGO_PATH)))
    database = Database(DB_PATH)
    project_service = ProjectService(database)
    window = MainWindow(project_service)
    window.showNormal()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
