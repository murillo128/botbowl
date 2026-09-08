"""Validate repository-local links in user-facing Markdown."""
import argparse
from pathlib import Path
import re
from urllib.parse import unquote


LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")


def markdown_files(root: Path):
    yield root / "README.md"
    yield root / "CHANGELOG.md"
    yield root / "THIRD_PARTY_NOTICES.md"
    yield from sorted((root / "docs").rglob("*.md"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve()
    failures = []
    checked = 0
    for document in markdown_files(root):
        text = document.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip().strip("<>").split(maxsplit=1)[0]
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target:
                continue
            checked += 1
            path = (document.parent / target).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                failures.append(f"{document.relative_to(root)}: link escapes repository: {target}")
                continue
            if not path.exists():
                line = text.count("\n", 0, match.start()) + 1
                failures.append(f"{document.relative_to(root)}:{line}: missing {target}")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"checked {checked} repository-local Markdown links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
