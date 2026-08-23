#!/usr/bin/env python3
import argparse
import base64
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from env_utils import load_workspace_env


load_workspace_env()


def normalize_url(url: str) -> str:
    return url.rstrip("/")


def auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def build_opener(user: str, password: str) -> urllib.request.OpenerDirector:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener.addheaders = [
        ("Authorization", auth_header(user, password)),
        ("User-Agent", "codex-jenkins-job-trigger/1.0"),
    ]
    return opener


def fetch_json(opener: urllib.request.OpenerDirector, url: str) -> dict:
    with opener.open(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_crumb(opener: urllib.request.OpenerDirector, base_url: str):
    crumb_url = f"{base_url}/crumbIssuer/api/json"
    try:
        data = fetch_json(opener, crumb_url)
        field = data.get("crumbRequestField")
        crumb = data.get("crumb")
        if field and crumb:
            return field, crumb
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 403):
            raise
    except Exception:
        pass
    return None, None


def parse_job_params(actions):
    results = []
    for action in actions or []:
        if not action:
            continue
        for definition in action.get("parameterDefinitions", []) or []:
            item = {
                "name": definition.get("name"),
                "type": definition.get("_class", ""),
                "default": None,
                "choices": definition.get("choices") or [],
                "description": definition.get("description") or "",
            }
            default_value = definition.get("defaultParameterValue") or {}
            if "value" in default_value:
                item["default"] = default_value.get("value")
            results.append(item)
    return results


def get_job_metadata(opener, base_url: str, job: str):
    encoded_job = "/".join(f"job/{urllib.parse.quote(part)}" for part in job.split("/"))
    api_url = (
        f"{base_url}/{encoded_job}/api/json"
        "?tree=name,fullName,actions[parameterDefinitions[name,_class,description,choices,defaultParameterValue[value]]]"
    )
    return fetch_json(opener, api_url)


def format_params(params):
    if not params:
        return "该 Job 未暴露参数定义，或当前账号无权读取参数。"
    lines = []
    for item in params:
        choices = ""
        if item["choices"]:
            choices = f" choices={item['choices']}"
        default = ""
        if item["default"] is not None:
            default = f" default={item['default']!r}"
        desc = ""
        if item["description"]:
            desc = f" desc={item['description']!r}"
        lines.append(f"- {item['name']} ({item['type']}){default}{choices}{desc}")
    return "\n".join(lines)


def parse_kv_pairs(items):
    data = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"非法参数格式: {item}，应为 key=value")
        key, value = item.split("=", 1)
        data[key] = value
    return data


def trigger_build(opener, base_url: str, job: str, params: dict):
    encoded_job = "/".join(f"job/{urllib.parse.quote(part)}" for part in job.split("/"))
    endpoint = "buildWithParameters" if params else "build"
    url = f"{base_url}/{encoded_job}/{endpoint}"
    data = None
    headers = {}
    if params:
        data = urllib.parse.urlencode(params).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    crumb_field, crumb = fetch_crumb(opener, base_url)
    if crumb_field and crumb:
        headers[crumb_field] = crumb
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with opener.open(req, timeout=20) as resp:
            return resp.status, resp.headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise SystemExit(f"触发失败: HTTP {exc.code}\n{body[:1000]}")


def main():
    parser = argparse.ArgumentParser(description="Query and trigger Jenkins jobs")
    parser.add_argument("--url", default=os.getenv("JENKINS_URL"), help="Jenkins base URL")
    parser.add_argument("--user", default=os.getenv("JENKINS_USER"), help="Jenkins username")
    parser.add_argument("--password", default=os.getenv("JENKINS_PASSWORD"), help="Jenkins password")

    subparsers = parser.add_subparsers(dest="command", required=True)

    params_parser = subparsers.add_parser("params", help="List parameter definitions for a job")
    params_parser.add_argument("--job", required=True, help="Job name, support folder path like a/b/c")

    build_parser = subparsers.add_parser("build", help="Trigger a Jenkins job")
    build_parser.add_argument("--job", required=True, help="Job name, support folder path like a/b/c")
    build_parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Build parameter, repeatable, format key=value",
    )

    args = parser.parse_args()

    if not args.url or not args.user or not args.password:
        raise SystemExit("缺少 Jenkins 连接信息，请提供 --url --user --password 或设置环境变量。")

    base_url = normalize_url(args.url)
    opener = build_opener(args.user, args.password)

    if args.command == "params":
        metadata = get_job_metadata(opener, base_url, args.job)
        params = parse_job_params(metadata.get("actions"))
        print(f"Job: {metadata.get('fullName') or metadata.get('name') or args.job}")
        print(format_params(params))
        return

    if args.command == "build":
        params = parse_kv_pairs(args.param)
        status, headers = trigger_build(opener, base_url, args.job, params)
        queue_item = headers.get("Location", "")
        print(f"已触发构建，HTTP {status}")
        if queue_item:
            print(f"队列地址: {queue_item}")
            print(f"队列接口: {queue_item.rstrip('/')}/api/json")
        if params:
            print(f"参数: {json.dumps(params, ensure_ascii=False)}")
        return


if __name__ == "__main__":
    main()
