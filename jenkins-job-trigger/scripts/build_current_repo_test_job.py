#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from env_utils import load_workspace_env


load_workspace_env()


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
JOB_MAP_PATH = SKILL_DIR / "references" / "job_map.json"
TRIGGER_SCRIPT = SCRIPT_DIR / "trigger_jenkins_job.py"
WATCH_SCRIPT = SCRIPT_DIR / "watch_jenkins_build.py"
ANALYZE_SCRIPT = SCRIPT_DIR / "analyze_jenkins_build.py"


def run_git(args, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_repo_root(cwd: Path) -> Path:
    return Path(run_git(["rev-parse", "--show-toplevel"], cwd))


def load_job_map() -> dict:
    return json.loads(JOB_MAP_PATH.read_text(encoding="utf-8"))


def resolve_repo_entry(job_map: dict, repo_name: str) -> dict:
    entry = job_map.get(repo_name)
    if entry is None:
        known = ", ".join(sorted(k for k in job_map if not k.startswith("_")))
        raise SystemExit(f"仓库 {repo_name!r} 未配置测试 job 映射。已配置仓库：{known}")

    if isinstance(entry, str):
        return {
            "test_job": entry,
            "product_job": None,
            "status": "legacy",
            "note": "",
        }

    if not isinstance(entry, dict):
        raise SystemExit(f"仓库 {repo_name!r} 的 Jenkins 映射格式非法：{type(entry).__name__}")

    return {
        "test_job": entry.get("test_job"),
        "product_job": entry.get("product_job"),
        "status": entry.get("status"),
        "note": entry.get("note", ""),
        "confidence": entry.get("confidence"),
    }


def require_test_job(repo_name: str, entry: dict) -> str:
    test_job = entry.get("test_job")
    if test_job:
        return test_job

    note = (entry.get("note") or "").strip()
    if note:
        raise SystemExit(f"仓库 {repo_name!r} 该项目无需 Jenkins 部署，或需要手动选择。说明：{note}")
    raise SystemExit(f"仓库 {repo_name!r} 该项目无需 Jenkins 部署，或需要手动选择。")


def require_env_job(repo_name: str, entry: dict, env: str) -> str:
    key = "test_job" if env == "test" else "product_job"
    job = entry.get(key)
    if job:
        return job

    note = (entry.get("note") or "").strip()
    env_label = "测试环境" if env == "test" else "线上环境"
    if note:
        raise SystemExit(f"仓库 {repo_name!r} 的{env_label} Jenkins job 未配置，或需要手动选择。说明：{note}")
    raise SystemExit(f"仓库 {repo_name!r} 的{env_label} Jenkins job 未配置，或需要手动选择。")


def to_jenkins_branch(branch: str, prefix: str) -> str:
    if not prefix:
        return branch
    if branch.startswith(prefix):
        return branch
    return f"{prefix}{branch}"


def build_trigger_command(job: str, branch_value: str) -> list[str]:
    return [
        sys.executable,
        str(TRIGGER_SCRIPT),
        "build",
        "--job",
        job,
        "--param",
        f"branch={branch_value}",
    ]


def build_watch_command(job: str, branch_value: str) -> list[str]:
    return [
        sys.executable,
        str(WATCH_SCRIPT),
        "--job",
        job,
        "--match-param",
        f"branch={branch_value}",
    ]


def run_and_capture(command: list[str]) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result.stdout


def extract_build_number(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("build_number="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("未能从 Jenkins 跟踪结果中提取 build_number。")


def extract_queue_url(output: str) -> str | None:
    for line in output.splitlines():
        if line.startswith("队列地址: "):
            return line.split(":", 1)[1].strip()
    return None


def build_queue_watch_command(job: str, branch_value: str, queue_url: str, min_build_number: int | None) -> list[str]:
    command = [
        sys.executable,
        str(WATCH_SCRIPT),
        "--queue-url",
        queue_url,
        "--job",
        job,
        "--match-param",
        f"branch={branch_value}",
    ]
    if min_build_number is not None:
        command.extend(["--min-build-number", str(min_build_number)])
    return command


def get_job_next_build_number(job: str) -> int | None:
    import base64
    import urllib.request

    jenkins_url = os.getenv("JENKINS_URL")
    jenkins_user = os.getenv("JENKINS_USER")
    jenkins_password = os.getenv("JENKINS_PASSWORD")
    if not jenkins_url or not jenkins_user or not jenkins_password:
        return None

    base_url = jenkins_url.rstrip("/")
    encoded_job = "/".join(f"job/{part}" for part in job.split("/"))
    url = f"{base_url}/{encoded_job}/api/json?tree=nextBuildNumber"
    token = base64.b64encode(f"{jenkins_user}:{jenkins_password}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {token}",
            "User-Agent": "codex-jenkins-next-build/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("nextBuildNumber")


def build_analyze_command(job: str, build_number: str) -> list[str]:
    return [
        sys.executable,
        str(ANALYZE_SCRIPT),
        "--job",
        job,
        "--build-number",
        build_number,
    ]


def main():
    parser = argparse.ArgumentParser(description="Trigger mapped Jenkins job for current repo branch")
    parser.add_argument("command", choices=["show", "build", "build-watch"], help="show derived values, trigger build, or trigger+watch+analyze")
    parser.add_argument("--repo", help="Repo path, default current working directory")
    parser.add_argument(
        "--env",
        choices=["test", "product"],
        default="test",
        help="Target Jenkins environment, default test",
    )
    parser.add_argument(
        "--branch",
        required=True,
        help="Git branch name to deploy, for example test or feature/demo",
    )
    parser.add_argument(
        "--branch-prefix",
        default="origin/",
        help="Prefix for Jenkins branch parameter, default origin/",
    )
    args = parser.parse_args()

    cwd = Path(args.repo).resolve() if args.repo else Path.cwd()
    repo_root = get_repo_root(cwd)
    repo_name = repo_root.name
    job_map = load_job_map()
    entry = resolve_repo_entry(job_map, repo_name)
    job_name = require_env_job(repo_name, entry, args.env)

    branch = args.branch.strip()
    if not branch:
        raise SystemExit("部署分支不能为空，请显式传入 --branch。")
    jenkins_branch = to_jenkins_branch(branch, args.branch_prefix)

    print(f"repo_root={repo_root}")
    print(f"repo_name={repo_name}")
    print(f"env={args.env}")
    print(f"status={entry.get('status')}")
    if entry.get("confidence"):
        print(f"confidence={entry.get('confidence')}")
    print(f"git_branch={branch}")
    print(f"jenkins_job={job_name}")
    if entry.get("product_job"):
        print(f"product_job={entry.get('product_job')}")
    if entry.get("note"):
        print(f"note={entry.get('note')}")
    print(f"jenkins_branch={jenkins_branch}")

    if args.command == "show":
        return

    next_build_number = None
    if args.command == "build-watch":
        try:
            next_build_number = get_job_next_build_number(job_name)
        except Exception:
            next_build_number = None

    trigger_command = build_trigger_command(job_name, jenkins_branch)
    trigger_result = subprocess.run(trigger_command, capture_output=True, text=True, check=True)
    if trigger_result.stdout:
        print(trigger_result.stdout, end="" if trigger_result.stdout.endswith("\n") else "\n")
    if trigger_result.stderr:
        print(trigger_result.stderr, file=sys.stderr, end="" if trigger_result.stderr.endswith("\n") else "\n")

    if args.command == "build-watch":
        queue_url = extract_queue_url(trigger_result.stdout)
        if queue_url:
            watch_command = build_queue_watch_command(job_name, jenkins_branch, queue_url, next_build_number)
        else:
            watch_command = build_watch_command(job_name, jenkins_branch)
            if next_build_number is not None:
                watch_command.extend(["--min-build-number", str(next_build_number)])
        watch_output = run_and_capture(watch_command)
        build_number = extract_build_number(watch_output)
        analyze_command = build_analyze_command(job_name, build_number)
        subprocess.run(analyze_command, check=True)


if __name__ == "__main__":
    main()
