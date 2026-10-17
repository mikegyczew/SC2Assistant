import os
import sys
from pathlib import Path


def main() -> None:
    project_dir = Path(__file__).resolve().parent.parent
    venv_packages = (
        project_dir
        / ".venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )

    if venv_packages.is_dir():
        sys.path.insert(0, str(venv_packages))

    from src.overlay import main as run_overlay

    run_overlay()


if __name__ == "__main__":
    main()
