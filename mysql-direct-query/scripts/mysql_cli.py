#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import pymysql

from env_utils import load_workspace_env


SCRIPT_PATH = Path(__file__).resolve()
READ_ONLY_PREFIXES = {"select", "show", "describe", "desc", "explain", "with"}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_$-]+$")
QUALIFIED_TABLE_RE = re.compile(r"`?[A-Za-z0-9_$-]+`?\s*\.\s*`?[A-Za-z0-9_$-]+`?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="直连 MySQL 的命令行工具。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env", choices=["prod", "test"], required=True, help="数据库环境。")
    common.add_argument("--schema", help="schema 名称。")

    subparsers.add_parser("schemas", parents=[common], help="列出 schema。")

    tables_parser = subparsers.add_parser("tables", parents=[common], help="列出表。")
    tables_parser.add_argument("--like", help="按表名模糊过滤。")

    describe_parser = subparsers.add_parser("describe", parents=[common], help="查看表结构。")
    describe_parser.add_argument("--table", required=True, help="表名。")

    query_parser = subparsers.add_parser("query", parents=[common], help="执行 SQL。")
    sql_group = query_parser.add_mutually_exclusive_group(required=True)
    sql_group.add_argument("--sql", help="直接传入 SQL。")
    sql_group.add_argument("--file", help="从文件读取 SQL。")

    return parser.parse_args()


def env_prefix(env_name: str) -> str:
    return "MYSQL_PROD" if env_name == "prod" else "MYSQL_TEST"


def get_connection_config(env_name: str) -> dict[str, object]:
    prefix = env_prefix(env_name)
    required = ["HOST", "PORT", "USER", "PASSWORD"]
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        env_key = f"{prefix}_{key}"
        value = os.environ.get(env_key)
        if value is None or value == "":
            missing.append(env_key)
            continue
        values[key] = value
    if missing:
        raise SystemExit(f"缺少数据库环境变量: {', '.join(missing)}")
    return {
        "host": values["HOST"],
        "port": int(values["PORT"]),
        "user": values["USER"],
        "password": values["PASSWORD"],
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": False,
    }


def read_sql(args: argparse.Namespace) -> str:
    if args.sql:
        return args.sql.strip()
    return Path(args.file).read_text(encoding="utf-8").strip()


def strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    lines = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or stripped.startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def ensure_prod_read_only(sql: str) -> None:
    cleaned = strip_sql_comments(sql).strip().rstrip(";").strip()
    if not cleaned:
        raise SystemExit("SQL 不能为空。")
    if ";" in cleaned:
        raise SystemExit("线上库只允许单条只读 SQL，禁止多语句执行。")
    first_token = cleaned.split(None, 1)[0].lower()
    if first_token not in READ_ONLY_PREFIXES:
        raise SystemExit(f"线上库只允许只读 SQL，当前语句类型不允许: {first_token}")


def ensure_qualified_table_reference(sql: str) -> None:
    cleaned = strip_sql_comments(sql)
    if not QUALIFIED_TABLE_RE.search(cleaned):
        raise SystemExit("SQL 必须显式使用 库名.表名，禁止依赖默认库。")


def ensure_identifier(name: str, label: str) -> str:
    if not name:
        raise SystemExit(f"{label} 不能为空。")
    if not IDENTIFIER_RE.fullmatch(name):
        raise SystemExit(f"{label} 非法: {name}")
    return name


def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def require_schema(schema: str | None) -> str:
    if not schema:
        raise SystemExit("该命令必须显式传入 --schema。")
    return ensure_identifier(schema, "schema")


def run_schemas(conn: pymysql.connections.Connection) -> None:
    sql = """
    SELECT schema_name
    FROM information_schema.schemata
    ORDER BY schema_name
    """
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    print_json({"count": len(rows), "rows": rows})


def run_tables(conn: pymysql.connections.Connection, schema: str, like: str | None) -> None:
    schema = require_schema(schema)
    sql = """
    SELECT table_name, table_type, engine, table_rows, create_time, update_time, table_comment
    FROM information_schema.tables
    WHERE table_schema = %s
    """
    params: list[object] = [schema]
    if like:
        sql += " AND table_name LIKE %s"
        params.append(like)
    sql += " ORDER BY table_name"
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    print_json({"schema": schema, "count": len(rows), "rows": rows})


def run_describe(conn: pymysql.connections.Connection, schema: str, table: str) -> None:
    schema = require_schema(schema)
    table = ensure_identifier(table, "table")
    sql = f"SHOW FULL COLUMNS FROM `{table}` FROM `{schema}`"
    with conn.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    print_json({"schema": schema, "table": table, "count": len(rows), "rows": rows})


def run_query(conn: pymysql.connections.Connection, env_name: str, sql: str) -> None:
    if env_name == "prod":
        ensure_prod_read_only(sql)
    ensure_qualified_table_reference(sql)
    with conn.cursor() as cursor:
        affected = cursor.execute(sql)
        if cursor.description:
            rows = cursor.fetchall()
            conn.rollback()
            print_json({"row_count": len(rows), "rows": rows})
            return
        conn.commit()
        print_json({"affected_rows": affected, "message": "SQL executed successfully"})


def main() -> int:
    load_workspace_env(SCRIPT_PATH)
    args = parse_args()
    sql_text = read_sql(args) if args.command == "query" else None
    if args.command == "query":
        ensure_qualified_table_reference(sql_text)
        if args.env == "prod":
            ensure_prod_read_only(sql_text)
    conn_cfg = get_connection_config(args.env)

    connection = pymysql.connect(**conn_cfg)
    try:
        if args.command == "schemas":
            run_schemas(connection)
        elif args.command == "tables":
            run_tables(connection, args.schema, args.like)
        elif args.command == "describe":
            run_describe(connection, args.schema, args.table)
        elif args.command == "query":
            run_query(connection, args.env, sql_text)
        else:
            raise SystemExit(f"不支持的命令: {args.command}")
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
