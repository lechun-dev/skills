#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_DIR.parent.parent
LOG_DIR = WORKSPACE_ROOT / "work-diary"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成当日日报 Markdown。")
    parser.add_argument("--date", help="目标日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument("--stdout", action="store_true", help="同时输出到标准输出。")
    return parser.parse_args()


def load_entries(log_path: Path) -> list[dict]:
    entries: list[dict] = []
    if not log_path.exists():
        return entries
    with log_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entries.append(json.loads(line))
    return entries


def build_markdown(target_date: str, entries: list[dict]) -> str:
    repo_counter = Counter()
    type_counter = Counter()
    failed_entries = []
    lines = [f"# {target_date} 日报", ""]

    if not entries:
        lines.extend(
            [
                "## 今日完成",
                "",
                "- 今日暂无结构化工作记录。",
                "",
            ]
        )
        return "\n".join(lines)

    lines.extend(["## 今日完成", ""])
    for entry in entries:
        repo = entry.get("repo") or "unknown"
        repo_counter[repo] += 1
        type_counter[entry.get("type") or "note"] += 1
        branch = entry.get("branch")
        result = entry.get("result")
        suffix = []
        if branch:
            suffix.append(f"branch={branch}")
        if result and result != "done":
            suffix.append(f"result={result}")
            failed_entries.append(entry)
        extra = f" ({', '.join(suffix)})" if suffix else ""
        lines.append(f"- [{entry.get('time', '')}] {repo}: {entry.get('summary', '')}{extra}")
        files = entry.get("files") or []
        if files:
            lines.append(f"  涉及文件: {', '.join(files)}")
        details = entry.get("details")
        if details:
            lines.append(f"  说明: {details}")

    lines.extend(["", "## 仓库统计", ""])
    for repo, count in repo_counter.most_common():
        lines.append(f"- {repo}: {count} 条")

    lines.extend(["", "## 动作统计", ""])
    for action_type, count in type_counter.most_common():
        lines.append(f"- {action_type}: {count} 条")

    lines.extend(["", "## 风险与阻塞", ""])
    if failed_entries:
        for entry in failed_entries:
            lines.append(
                f"- {entry.get('repo')}: {entry.get('summary')} (result={entry.get('result')})"
            )
    else:
        lines.append("- 今日无失败或阻塞记录。")

    lines.extend(
        [
            "",
            "## 明日计划",
            "",
            "- 按今日未完成项和新增需求继续推进。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    target_date = args.date or date.today().isoformat()
    log_path = LOG_DIR / f"{target_date}.ndjson"
    report_path = LOG_DIR / f"{target_date}.md"
    entries = load_entries(log_path)
    markdown = build_markdown(target_date, entries)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path.write_text(markdown, encoding="utf-8")

    print(f"log_path={log_path}")
    print(f"report_path={report_path}")
    print(f"entry_count={len(entries)}")
    if args.stdout:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
