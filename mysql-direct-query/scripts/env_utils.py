#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path


def parse_dotenv_line(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None, None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if value and len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value


def find_workspace_env(start: Path | None = None) -> Path | None:
    current = (start or Path(__file__).resolve()).parent
    for candidate_dir in [current, *current.parents]:
        candidate = candidate_dir / ".env"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_workspace_env(start: Path | None = None) -> Path | None:
    dotenv_path = find_workspace_env(start)
    if not dotenv_path:
        return None
    for raw_line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        key, value = parse_dotenv_line(raw_line)
        if not key or key in os.environ:
            continue
        os.environ[key] = value
    return dotenv_path
