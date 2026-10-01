"""Presidential campaign promise tracker."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVE = ROOT / "archive" / "docs"
BUILD = ROOT / "build"
DB_PATH = BUILD / "presidential.db"


def main() -> None:
    from .cli import main as _main
    _main()
