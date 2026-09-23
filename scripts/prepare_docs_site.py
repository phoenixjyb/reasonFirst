from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILD_SRC = ROOT / ".site-src"
DOCS = ROOT / "docs"
WEBSITE = ROOT / "website"

ROOT_MARKDOWN = (
    "README.md",
    "README_CN.md",
    "SECURITY.md",
    "SECURITY_CN.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING_CN.md",
    "CHANGELOG.md",
)


def main() -> None:
    if BUILD_SRC.exists():
        shutil.rmtree(BUILD_SRC)
    BUILD_SRC.mkdir()

    shutil.copytree(DOCS, BUILD_SRC / "docs")

    for name in ROOT_MARKDOWN:
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, BUILD_SRC / name)

    for source in WEBSITE.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(WEBSITE)
        target = BUILD_SRC / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    print(f"Prepared MkDocs source at {BUILD_SRC}")


if __name__ == "__main__":
    main()
