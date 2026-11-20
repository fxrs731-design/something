import logging
from collections import deque
from datetime import datetime
from pathlib import Path


class LauncherLogger:
    def __init__(self, log_dir: Path, max_memory_lines: int = 500):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.log_dir / "f1_classic_launcher.log"
        self.memory_buffer = deque(maxlen=max_memory_lines)

        self._logger = logging.getLogger("F1ClassicLauncher")
        self._logger.setLevel(logging.DEBUG)
        self._logger.handlers.clear()

        fh = logging.FileHandler(self.log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh.setFormatter(formatter)
        self._logger.addHandler(fh)

    def _record(self, level: str, message: str):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {"timestamp": ts, "level": level, "message": message}
        self.memory_buffer.append(entry)

    def info(self, message: str):
        self._logger.info(message)
        self._record("INFO", message)

    def warning(self, message: str):
        self._logger.warning(message)
        self._record("WARNING", message)

    def error(self, message: str):
        self._logger.error(message)
        self._record("ERROR", message)

    def get_recent(self, limit: int = 200) -> list[dict]:
        items = list(self.memory_buffer)
        return items[-limit:]
