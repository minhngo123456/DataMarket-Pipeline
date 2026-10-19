"""Copy only runtime sources into build/lambda before running sam build."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "build/lambda"
    if destination.resolve() != ROOT / "build/lambda":
        raise ValueError("build/lambda must stay inside the project, without symlinks")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("marketdata", "services"):
        shutil.copytree(
            ROOT / name,
            destination / name,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "README.md"),
        )
    shutil.copyfile(ROOT / "requirements.txt", destination / "requirements.txt")
    print(destination)


if __name__ == "__main__":
    main()
