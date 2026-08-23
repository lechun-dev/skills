#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_DIR.parent.parent
LOG_DIR = WORKSPACE_ROOT / "work-diary"


def run_git(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def resolve_repo(repo_arg: str | None) -> tuple[Path, str | None, str | None]:
    repo_path = Path(repo_arg).resolve() if repo_arg else Path.cwd().resolve()
    git_root = run_git(repo_path, "rev-parse", "--show-toplevel")
    if git_root:
        repo_root = Path(git_root).resolve()
        branch = run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
        if branch == "HEAD":
            branch = None
        return repo_root, repo_root.name, branch
    return repo_path, repo_path.name, None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="追加一条当日工作日志。")
    parser.add_argument("--summary", required=True, help="本次动作一句话总结。")
    parser.add_argument("--type", default="note", help="日志类型，例如 code_change/deploy/test/analysis。")
    parser.add_argument("--repo", help="仓库路径，默认当前目录。")
    parser.add_argument("--branch", help="显式指定分支名。")
    parser.add_argument(
        "--files",
        action="append",
        default=[],
        help="涉及文件路径，可重复传入。",
    )
    parser.add_argument("--result", default="done", help="结果，例如 done/failed/blocked。")
    parser.add_argument("--details", help="补充说明。")
    parser.add_argument("--date", help="日志日期，格式 YYYY-MM-DD，默认今天。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root, repo_name, detected_branch = resolve_repo(args.repo)
    now = datetime.now()
    log_date = args.date or now.strftime("%Y-%m-%d")

    entry = {
        "time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": log_date,
        "repo": repo_name,
        "repo_path": str(repo_root),
        "branch": args.branch or detected_branch,
        "type": args.type,
        "summary": args.summary.strip(),
        "files": args.files,
        "result": args.result,
    }
    if args.details:
        entry["details"] = args.details.strip()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{log_date}.ndjson"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"log_path={log_path}")
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
