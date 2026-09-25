from __future__ import annotations

from pathlib import Path
import re

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class SoundEffects(QObject):
    def __init__(self, sfx_folder: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.sfx_folder = sfx_folder or Path(__file__).resolve().parent.parent / "assets" / "sfx"
        self._players: dict[str, tuple[QMediaPlayer, QAudioOutput]] = {}
        self._load_sounds()

    def play(self, name: str) -> None:
        player_pair = self._players.get(name)
        if player_pair is None:
            return
        player, _audio = player_pair
        player.stop()
        player.setPosition(0)
        player.play()

    def _load_sounds(self) -> None:
        if not self.sfx_folder.exists() or not self.sfx_folder.is_dir():
            return

        for path in self.sfx_folder.iterdir():
            if not path.is_file() or path.suffix.casefold() not in {".mp3", ".wav"}:
                continue
            alias = self._alias_for_path(path)
            if alias is None:
                continue
            audio_output = QAudioOutput(self)
            audio_output.setVolume(0.65)
            player = QMediaPlayer(self)
            player.setAudioOutput(audio_output)
            player.setSource(QUrl.fromLocalFile(str(path)))
            self._players[alias] = (player, audio_output)

    def _alias_for_path(self, path: Path) -> str | None:
        name = re.sub(r"[^a-z0-9]+", "_", path.stem.casefold()).strip("_")
        if "pickup" in name and "card" in name:
            return "pickup_card"
        if "drop" in name and "card" in name:
            return "drop_card"
        if "mark" in name:
            return "mark"
        if "publish" in name:
            return "publish"
        if "warning" in name:
            return "warning"
        return None
