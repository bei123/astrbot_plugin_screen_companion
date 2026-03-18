from __future__ import annotations

import argparse
import sys
from pathlib import Path


DEFAULT_EXTENSIONS = {
    ".py",
    ".js",
    ".css",
    ".html",
    ".md",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".txt",
}

SUSPICIOUS_SNIPPETS = [
    "Placeholder",
    "????",
    "鍔",
    "瑙",
    "鏃",
    "璁",
    "闀",
    "楹",
]

SKIP_DIR_NAMES = {
    ".git",
    "__pycache__",
    "venv",
    "env",
    ".idea",
    ".vscode",
}

SKIP_FILE_NAMES = {
    "check_text_health.py",
}


def should_scan(path: Path, extensions: set[str]) -> bool:
    if not path.is_file():
        return False
    if any(part in SKIP_DIR_NAMES for part in path.parts):
        return False
    if path.name in SKIP_FILE_NAMES:
        return False
    return path.suffix.lower() in extensions


def iter_targets(root: Path, extensions: set[str]) -> list[Path]:
    if root.is_file():
        return [root] if should_scan(root, extensions) else []
    return [path for path in root.rglob("*") if should_scan(path, extensions)]


def find_suspicious_lines(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if any(snippet in line for snippet in SUSPICIOUS_SNIPPETS):
            hits.append((lineno, line.strip()))
    return hits


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Scan repository text files for likely mojibake and placeholder text."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["."],
        help="Files or directories to scan. Defaults to current directory.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code when any suspicious text is found.",
    )
    args = parser.parse_args()

    extensions = set(DEFAULT_EXTENSIONS)
    roots = [Path(path).resolve() for path in args.paths]
    had_issue = False

    for root in roots:
        for path in iter_targets(root, extensions):
            try:
                raw = path.read_bytes()
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                had_issue = True
                print(f"[encoding] {display_path(path)}: invalid UTF-8 ({exc})")
                continue

            hits = find_suspicious_lines(text)
            if not hits:
                continue

            had_issue = True
            print(f"[suspicious] {display_path(path)}")
            for lineno, line in hits[:10]:
                print(f"  L{lineno}: {line}")
            if len(hits) > 10:
                print(f"  ... and {len(hits) - 10} more line(s)")

    if not had_issue:
        print("No suspicious text or encoding issues found.")
        return 0
    return 1 if args.strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
