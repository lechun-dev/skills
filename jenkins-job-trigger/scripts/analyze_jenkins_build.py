#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import urllib.request

from env_utils import load_workspace_env


load_workspace_env()


FAILURE_PATTERNS = [
    r"BUILD FAILURE",
    r"(?<!NO )ERROR",
    r"Exception",
    r"Caused by:",
    r"Failed to start",
    r"port already in use",
    r"OutOfMemoryError",
    r"Connection refused",
    r"No such file",
    r"permission denied",
]


def auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_opener(user: str, password: str) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ("Authorization", auth_header(user, password)),
        ("User-Agent", "codex-jenkins-analyze/1.0"),
    ]
    return opener


def fetch_json(opener: urllib.request.OpenerDirector, url: str) -> dict:
    with opener.open(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(opener: urllib.request.OpenerDirector, url: str) -> str:
    with opener.open(url, timeout=60) as resp:
        return resp.read().decode("utf-8", "ignore")


def encoded_job_path(job: str) -> str:
    return "/".join(f"job/{part}" for part in job.split("/"))


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def find_matches(patterns, text: str):
    matches = []
    for pattern in patterns:
        for line in text.splitlines():
            if re.search(pattern, line, re.IGNORECASE):
                matches.append(line.strip())
    return matches


def extract_error_summary(lines):
    error_indices = []
    for idx, line in enumerate(lines):
        if re.search(r"BUILD FAILURE|ERROR|Exception|Caused by:|Failed to start|OutOfMemoryError|Connection refused|No such file|permission denied", line, re.IGNORECASE):
            error_indices.append(idx)

    if error_indices:
        start = max(error_indices[-1] - 8, 0)
        end = min(error_indices[-1] + 12, len(lines))
        return lines[start:end]

    return lines[-80:]


def normalize_status(build_result: str | None) -> str:
    if build_result in {"SUCCESS", "FAILURE", "ABORTED", "UNSTABLE", "NOT_BUILT"}:
        return build_result
    return "UNKNOWN"


def main():
    parser = argparse.ArgumentParser(description="Analyze Jenkins build log")
    parser.add_argument("--url", default=os.getenv("JENKINS_URL"), help="Jenkins base URL")
    parser.add_argument("--user", default=os.getenv("JENKINS_USER"), help="Jenkins username")
    parser.add_argument("--password", default=os.getenv("JENKINS_PASSWORD"), help="Jenkins password")
    parser.add_argument("--job", required=True, help="Job name")
    parser.add_argument("--build-number", required=True, help="Build number")
    parser.add_argument("--tail", type=int, default=120, help="Number of tail lines to print in summary")
    args = parser.parse_args()

    if not args.url or not args.user or not args.password:
        raise SystemExit("缺少 Jenkins 连接信息，请提供 URL/用户名/密码。")

    opener = build_opener(args.user, args.password)
    base_url = normalize_base_url(args.url)
    job_path = encoded_job_path(args.job)
    build_api = f"{base_url}/{job_path}/{args.build_number}/api/json"
    console_url = f"{base_url}/{job_path}/{args.build_number}/consoleText"

    build_data = fetch_json(opener, build_api)
    console_text = fetch_text(opener, console_url)
    lines = console_text.splitlines()

    failure_markers = find_matches(FAILURE_PATTERNS, console_text)
    final_status = normalize_status(build_data.get("result"))
    summary_lines = extract_error_summary(lines)
    if args.tail > 0:
        summary_lines = summary_lines[-args.tail:]

    print(f"job={args.job}")
    print(f"build_number={args.build_number}")
    print(f"status={final_status}")
    print(f"build_url={build_data.get('url')}")
    if final_status != "SUCCESS" and failure_markers:
        print(f"failure_marker={failure_markers[-1]}")
    if final_status != "SUCCESS":
        print("error_summary_start")
        for line in summary_lines:
            print(line)
        print("error_summary_end")


if __name__ == "__main__":
    main()
