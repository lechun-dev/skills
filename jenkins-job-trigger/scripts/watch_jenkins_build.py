#!/usr/bin/env python3
import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request

from env_utils import load_workspace_env


load_workspace_env()


def auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_opener(user: str, password: str) -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ("Authorization", auth_header(user, password)),
        ("User-Agent", "codex-jenkins-watch/1.0"),
    ]
    return opener


def fetch_json(opener: urllib.request.OpenerDirector, url: str) -> dict:
    with opener.open(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize_queue_api_url(queue_url: str) -> str:
    queue_url = queue_url.rstrip("/")
    if queue_url.endswith("/api/json"):
        return queue_url
    return f"{queue_url}/api/json"


def build_api_url(build_url: str) -> str:
    return f"{build_url.rstrip('/')}/api/json"


def normalize_base_url(url: str) -> str:
    return url.rstrip("/")


def parse_kv_pairs(items):
    data = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"非法参数格式: {item}，应为 key=value")
        key, value = item.split("=", 1)
        data[key] = value
    return data


def encoded_job_path(job: str) -> str:
    return "/".join(f"job/{part}" for part in job.split("/"))


def parameter_map(build_data: dict) -> dict:
    for action in build_data.get("actions") or []:
        if not action:
            continue
        params = action.get("parameters") or []
        if params:
            return {item.get("name"): item.get("value") for item in params}
    return {}


def match_build_params(build_data: dict, expected: dict) -> bool:
    if not expected:
        return True
    params = parameter_map(build_data)
    for key, value in expected.items():
        if params.get(key) != value:
            return False
    return True


def print_build_status(data: dict):
    full_display_name = data.get("fullDisplayName")
    url = data.get("url")
    building = data.get("building")
    result = data.get("result")
    print(f"build_status name={full_display_name!r} building={building} result={result}")
    if data.get("number") is not None:
        print(f"build_number={data.get('number')}")
    if not building:
        print(f"final_url={url}")


def watch_build_api(opener, build_api: str, poll: float, deadline: float):
    while time.time() < deadline:
        data = fetch_json(opener, build_api)
        print_build_status(data)
        if not data.get("building"):
            return
        time.sleep(poll)
    raise SystemExit("超时：构建在指定时间内未结束。")


def build_filter_ok(build_data: dict, expected_params: dict, min_build_number: int | None) -> bool:
    if min_build_number is not None:
        build_number = build_data.get("number")
        if build_number is None or int(build_number) < int(min_build_number):
            return False
    return match_build_params(build_data, expected_params)


def main():
    parser = argparse.ArgumentParser(description="Watch Jenkins queue item and build result")
    parser.add_argument("--queue-url", help="Queue item URL or queue api json URL")
    parser.add_argument("--url", default=os.getenv("JENKINS_URL"), help="Jenkins base URL")
    parser.add_argument("--job", help="Job name, used when watching latest build directly")
    parser.add_argument(
        "--match-param",
        action="append",
        default=[],
        help="Expected build parameter key=value, repeatable",
    )
    parser.add_argument(
        "--min-build-number",
        type=int,
        help="Only accept builds whose number is greater than or equal to this value",
    )
    parser.add_argument("--user", default=os.getenv("JENKINS_USER"), help="Jenkins username")
    parser.add_argument("--password", default=os.getenv("JENKINS_PASSWORD"), help="Jenkins password")
    parser.add_argument("--poll", type=float, default=3, help="Polling interval seconds, default 3")
    parser.add_argument("--timeout", type=int, default=1800, help="Timeout seconds, default 1800")
    args = parser.parse_args()

    if not args.user or not args.password:
        raise SystemExit("缺少 Jenkins 凭据，请提供 --user --password 或设置环境变量。")
    if not args.queue_url and not args.job:
        raise SystemExit("至少提供 --queue-url 或 --job 其一。")

    opener = build_opener(args.user, args.password)
    deadline = time.time() + args.timeout
    expected_params = parse_kv_pairs(args.match_param)

    if args.queue_url:
        queue_api = normalize_queue_api_url(args.queue_url)
        print(f"queue_api={queue_api}")
        executable_url = None
        while time.time() < deadline:
            try:
                data = fetch_json(opener, queue_api)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print("queue_missing=404")
                    break
                raise
            if data.get("cancelled"):
                raise SystemExit("队列项已取消。")
            executable = data.get("executable")
            why = data.get("why")
            if executable and executable.get("url"):
                executable_url = executable["url"]
                print(f"build_url={executable_url}")
                print(f"build_number={executable.get('number')}")
                watch_build_api(opener, build_api_url(executable_url), args.poll, deadline)
                return
            print(f"queue_waiting={why or 'waiting'}")
            time.sleep(args.poll)
        if not args.job:
            raise SystemExit("队列项不可用，且未提供 --job 作为兜底跟踪。")

    base_url = normalize_base_url(args.url)
    job_api = f"{base_url}/{encoded_job_path(args.job)}/api/json?tree=lastBuild[number,url]"
    print(f"job_api={job_api}")
    found_build_url = None
    while time.time() < deadline:
        job_data = fetch_json(opener, job_api)
        last_build = job_data.get("lastBuild")
        if last_build and last_build.get("url"):
            candidate_api = build_api_url(last_build["url"])
            candidate_data = fetch_json(opener, candidate_api)
            if build_filter_ok(candidate_data, expected_params, args.min_build_number):
                found_build_url = last_build["url"]
                print(f"build_url={found_build_url}")
                print(f"build_number={last_build.get('number')}")
                watch_build_api(opener, candidate_api, args.poll, deadline)
                return
            print(
                f"last_build_not_match expected={expected_params} "
                f"min_build_number={args.min_build_number} actual_build_number={candidate_data.get('number')} "
                f"actual_params={parameter_map(candidate_data)}"
            )
        else:
            print("last_build_missing=true")
        time.sleep(args.poll)

    raise SystemExit("超时：未找到匹配参数的构建，或构建未在指定时间内结束。")


if __name__ == "__main__":
    main()
