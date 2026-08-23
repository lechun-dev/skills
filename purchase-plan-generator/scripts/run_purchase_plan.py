#!/usr/bin/env python3
import argparse
import json
import math
import os
import subprocess
import sys
import urllib.request
import requests
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


API_URL = "https://scmapi.lechun.cc/lechun-bi/commonSql/execute"
DING_ID = "030966113929310965"

PURCHASE_PLAN_BASE_ID = "a9E05BDRVQ6rpEjdt2YK16RKJ63zgkYA"
EOQ_BASE_ID = "G1DKw2zgV2RglZAbFBKRk9DlVB5r9YAn"
EOQ_TABLE_ID = "hERWDMS"
DEFAULT_NOTIFY_USERS = "030966113929310965"
DEFAULT_NOTIFY_ROBOT_CODE = "dingvdcze6qot9oisdgl"
HORIZON_WEEKS = 13
WRITE_BATCH_KEYS = 30

EOQ_FIELD_IDS = {
    "物料编码": "atJLEMM",
    "物料名称": "dXcFKv3",
    "最小订购量（MOQ）": "Ru6Nisp",
    "最优采购量": "dvqPAul",
    "采购提前期（天）": "EajQCSf",
}


class SkillError(RuntimeError):
    pass


@dataclass
class WeeklyTable:
    table_id: str
    table_name: str
    field_ids: Dict[str, str]
    week_dates: List[str]


@dataclass
class EoqRecord:
    material_code: str
    material_name: str
    moq: int
    eoq: int
    lead_days: int


@dataclass
class PlanRows:
    material_code: str
    material_name: str
    warehouse: str
    demand: Dict[str, int]
    inventory: Dict[str, int]
    purchase: Dict[str, int]
    rows: List[Dict[str, object]]
    warnings: List[str]
    anomalies: List[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成原料周采购计划并写入钉钉 AI 表格")
    parser.add_argument("--material-code", help="仅处理指定物料编码")
    parser.add_argument("--warehouse", help="仅处理指定仓库")
    parser.add_argument("--week-start", help="手工指定周一日期，格式 YYYY-MM-DD")
    parser.add_argument("--force", action="store_true", help="强制重算并覆盖本周已有记录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写表、不发通知")
    parser.add_argument("--audit-only", action="store_true", help="全量巡检模式，只做 dry-run 并输出异常")
    parser.add_argument("--limit-items", type=int, help="仅处理前 N 个物料仓库组合，便于调试")
    parser.add_argument("--report-file", help="将 markdown 报告写入指定文件")
    parser.add_argument("--no-notify", action="store_true", help="生成完成后不发送 DING")
    parser.add_argument("--verbose", action="store_true", help="输出更多调试信息")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.audit_only:
        args.dry_run = True
        args.no_notify = True
    week_start = parse_week_start(args.week_start)
    horizon = build_horizon(week_start)

    log(args, f"开始生成采购计划，周一={week_start.isoformat()}")
    demand_rows = fetch_rows(7)
    log(args, f"毛需求行数={len(demand_rows)}")
    material_rows = fetch_rows(2)
    log(args, f"原料主数据行数={len(material_rows)}")
    eoq_map = load_eoq_map()
    log(args, f"EOQ 记录数={len(eoq_map)}")

    demand_groups = group_demands(demand_rows, horizon)
    material_names = build_material_name_map(material_rows, demand_rows)

    keys = sorted(demand_groups.keys())
    if args.material_code:
        keys = [key for key in keys if key[0] == args.material_code]
    if args.warehouse:
        keys = [key for key in keys if key[1] == args.warehouse]
    keys = [key for key in keys if sum(demand_groups[key].values()) > 0]
    if args.limit_items:
        keys = keys[: args.limit_items]
    if not keys:
        raise SkillError("未找到符合条件的物料仓库组合")
    log(args, f"待处理物料仓库组合数={len(keys)}")

    weekly_table = get_or_create_weekly_table(week_start, args.dry_run, args.verbose)
    log(args, f"目标周表={weekly_table.table_name}({weekly_table.table_id})")
    existing_keys = load_existing_plan_keys(weekly_table.table_id, weekly_table.field_ids)
    log(args, f"当前周表已有组合数={len(existing_keys)}")

    stock_cache: Dict[str, List[Dict[str, object]]] = {}
    transit_cache: Dict[str, List[Dict[str, object]]] = {}

    create_records: List[Dict[str, object]] = []
    update_records: List[Dict[str, object]] = []
    skipped: List[Tuple[str, str]] = []
    failures: List[Dict[str, str]] = []
    all_warnings: List[str] = []
    all_anomalies: List[str] = []
    generated: List[PlanRows] = []

    for material_code, warehouse in keys:
        log(args, f"处理 {material_code} / {warehouse}")
        existing_record_ids = existing_keys.get((material_code, warehouse), [])
        if existing_record_ids and not args.force and not args.audit_only:
            skipped.append((material_code, warehouse))
            continue
        try:
            eoq = eoq_map.get(material_code)
            if not eoq:
                raise SkillError("EOQ 表中缺少该物料记录")
            material_name = material_names.get(material_code) or eoq.material_name or material_code
            stock_rows = stock_cache.setdefault(material_code, fetch_rows(4, {"matCode": material_code}))
            transit_rows = transit_cache.setdefault(material_code, fetch_rows(5, {"matCode": material_code}))
            plan = build_plan_rows(
                material_code=material_code,
                material_name=material_name,
                warehouse=warehouse,
                horizon=horizon,
                demand_by_week=demand_groups[(material_code, warehouse)],
                stock_rows=stock_rows,
                transit_rows=transit_rows,
                eoq=eoq,
                field_ids=weekly_table.field_ids,
            )
            generated.append(plan)
            all_warnings.extend(plan.warnings)
            all_anomalies.extend(plan.anomalies)
            if existing_record_ids and args.force:
                if len(existing_record_ids) != 3:
                    raise SkillError(f"当前周表中已有 {len(existing_record_ids)} 行，预期为 3 行")
                sorted_existing = sort_record_ids_by_type(existing_record_ids, weekly_table, plan.rows)
                for record_id, row in zip(sorted_existing, plan.rows):
                    update_records.append({"recordId": record_id, "cells": row["cells"]})
            else:
                create_records.extend(plan.rows)
        except Exception as exc:
            failures.append(
                {"materialCode": material_code, "warehouse": warehouse, "error": str(exc)}
            )

    if args.verbose or args.dry_run:
        preview = {
            "weekStart": week_start.isoformat(),
            "tableName": weekly_table.table_name,
            "createPreview": create_records[:6],
            "updatePreview": update_records[:6],
            "skipped": skipped[:20],
            "failures": failures[:20],
            "anomalies": all_anomalies[:20],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))

    if not args.dry_run:
        if create_records:
            batch_write_records("create", weekly_table.table_id, create_records)
        if update_records:
            batch_write_records("update", weekly_table.table_id, update_records)

    report = render_report(
        week_start=week_start,
        weekly_table=weekly_table,
        processed_keys=keys,
        created_row_count=len(create_records),
        updated_row_count=len(update_records),
        skipped=skipped,
        failures=failures,
        warnings=all_warnings,
        anomalies=all_anomalies,
        generated=generated,
    )
    if args.report_file:
        with open(args.report_file, "w", encoding="utf-8") as handle:
            handle.write(report)

    bot_message_sent = False
    if not args.dry_run and not args.no_notify:
        bot_message_sent = send_markdown_bot_message(report, week_start)

    summary = {
        "weekStart": week_start.isoformat(),
        "tableName": weekly_table.table_name,
        "tableId": weekly_table.table_id,
        "keyCount": len(keys),
        "generatedKeyCount": len(generated),
        "createdRowCount": len(create_records),
        "updatedRowCount": len(update_records),
        "skippedKeyCount": len(skipped),
        "failedKeyCount": len(failures),
        "warningCount": len(all_warnings),
        "anomalyCount": len(all_anomalies),
        "botMessageSent": bot_message_sent,
        "dryRun": args.dry_run,
        "auditOnly": args.audit_only,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n" + report)
    return 0 if not failures else 1


def parse_week_start(value: Optional[str]) -> date:
    if value:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    else:
        today = date.today()
        parsed = today - timedelta(days=today.weekday())
    if parsed.weekday() != 0:
        raise SkillError(f"周起始日期必须是周一: {parsed.isoformat()}")
    return parsed


def build_horizon(week_start: date) -> List[date]:
    return [week_start + timedelta(days=7 * offset) for offset in range(HORIZON_WEEKS)]


def fetch_rows(sql_id: int, params: Optional[Dict[str, object]] = None) -> List[Dict[str, object]]:
    payload = {"sqlId": sql_id, "dingId": DING_ID}
    if params:
        payload["params"] = params
    raw = request_payload(payload)
    data = json.loads(raw)
    value = data.get("value")
    if not isinstance(value, dict):
        raise SkillError(f"SQL {sql_id} 返回格式异常，缺少 value 对象")
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise SkillError(f"SQL {sql_id} 返回格式异常，缺少 rows 数组")
    return rows


def request_payload(payload: Dict[str, object]) -> str:
    payload_text = json.dumps(payload, ensure_ascii=False)
    body = payload_text.encode("utf-8")
    api_key = os.getenv("LECHUN_API_KEY")
    for use_api_key in ([False, True] if api_key else [False]):
        raw = try_request_payload(body, payload_text, api_key if use_api_key else None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if parsed.get("status") == 200 and isinstance(parsed.get("value"), dict):
            return raw
    raise SkillError(f"SCM 接口请求失败: {payload_text}")


def try_request_payload(body: bytes, payload_text: str, api_key: Optional[str]) -> Optional[str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Caller-Key"] = api_key
    try:
        response = requests.post(API_URL, data=body, headers=headers, timeout=120)
        if response.ok and response.text:
            return response.text
    except Exception:
        pass

    command = [
        "curl",
        "-sS",
        "--max-time",
        "120",
        API_URL,
        "-H",
        "Content-Type: application/json",
        "--data",
        payload_text,
    ]
    if api_key:
        command.extend(["-H", f"X-Caller-Key: {api_key}"])
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode == 0 and process.stdout:
        return process.stdout

    request = urllib.request.Request(API_URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.read().decode("utf-8")
    except Exception:
        pass
    return None


def load_eoq_map() -> Dict[str, EoqRecord]:
    cursor = None
    seen = set()
    mapping: Dict[str, EoqRecord] = {}
    fields = ",".join(EOQ_FIELD_IDS.values())
    while True:
        command = [
            "dws",
            "aitable",
            "record",
            "query",
            "--base-id",
            EOQ_BASE_ID,
            "--table-id",
            EOQ_TABLE_ID,
            "--field-ids",
            fields,
            "--limit",
            "100",
            "--format",
            "json",
        ]
        if cursor:
            command.extend(["--cursor", cursor])
        payload = run_dws(command)
        data = payload.get("data") or {}
        records = data.get("records") or []
        for record in records:
            cells = record.get("cells") or {}
            material_code = str(cells.get(EOQ_FIELD_IDS["物料编码"]) or "").strip()
            if not material_code:
                continue
            mapping[material_code] = EoqRecord(
                material_code=material_code,
                material_name=str(cells.get(EOQ_FIELD_IDS["物料名称"]) or "").strip(),
                moq=max(0, normalize_int(cells.get(EOQ_FIELD_IDS["最小订购量（MOQ）"]))),
                eoq=max(0, normalize_int(cells.get(EOQ_FIELD_IDS["最优采购量"]))),
                lead_days=normalize_int(cells.get(EOQ_FIELD_IDS["采购提前期（天）"])),
            )
        next_cursor = data.get("nextCursor")
        if not next_cursor or next_cursor in seen or len(records) < 100:
            break
        seen.add(next_cursor)
        cursor = next_cursor
    return mapping


def build_material_name_map(
    material_rows: Iterable[Dict[str, object]], demand_rows: Iterable[Dict[str, object]]
) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for row in material_rows:
        code = str(row.get("物料编码") or "").strip()
        name = str(row.get("物料名称") or "").strip()
        if code and name and code not in result:
            result[code] = name
    for row in demand_rows:
        code = str(row.get("物料编码") or "").strip()
        name = str(row.get("物料名称") or "").strip()
        if code and name and code not in result:
            result[code] = name
    return result


def group_demands(
    rows: Iterable[Dict[str, object]], horizon: Sequence[date]
) -> Dict[Tuple[str, str], Dict[str, Decimal]]:
    horizon_set = {item.isoformat() for item in horizon}
    grouped: Dict[Tuple[str, str], Dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for row in rows:
        material_code = str(row.get("物料编码") or "").strip()
        warehouse = str(row.get("仓库") or "").strip()
        week = normalize_week_key(row.get("日期"))
        if not material_code or not warehouse or week not in horizon_set:
            continue
        grouped[(material_code, warehouse)][week] += normalize_decimal(row.get("计划数量"))
    return grouped


def get_or_create_weekly_table(week_start: date, dry_run: bool, verbose: bool) -> WeeklyTable:
    target_name = f"采购计划-{week_start.isoformat()}"
    existing = get_existing_weekly_table(target_name)
    if existing:
        if dry_run:
            return merge_expected_structure(existing, week_start)
        return ensure_weekly_table_structure(existing, week_start, verbose)
    if dry_run:
        return build_expected_weekly_table(target_name, week_start)
    template = find_latest_weekly_table()
    created = create_weekly_table(target_name, week_start, verbose)
    if verbose:
        print(
            json.dumps(
                {
                    "action": "createWeeklyTable",
                    "tableName": target_name,
                    "templateTable": template.table_name if template else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return created


def get_existing_weekly_table(target_name: str) -> Optional[WeeklyTable]:
    payload = run_dws(
        [
            "dws",
            "aitable",
            "table",
            "get",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--format",
            "json",
        ]
    )
    tables = (payload.get("data") or {}).get("tables") or []
    for table in tables:
        if table.get("tableName") == target_name:
            return table_to_weekly_table(table)
    return None


def find_latest_weekly_table() -> Optional[WeeklyTable]:
    payload = run_dws(
        [
            "dws",
            "aitable",
            "table",
            "get",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--format",
            "json",
        ]
    )
    tables = (payload.get("data") or {}).get("tables") or []
    weekly_tables = [table_to_weekly_table(table) for table in tables if str(table.get("tableName", "")).startswith("采购计划-")]
    if not weekly_tables:
        return None
    return max(weekly_tables, key=lambda item: item.table_name)


def build_expected_weekly_table(target_name: str, week_start: date) -> WeeklyTable:
    weeks = [week_start + timedelta(days=7 * index) for index in range(HORIZON_WEEKS)]
    field_ids = {"序号": "序号", "物料编码": "物料编码", "物料名称": "物料名称", "类型": "类型", "仓库": "仓库"}
    for week in weeks:
        field_ids[week.isoformat()] = week.isoformat()
    return WeeklyTable(
        table_id="DRY_RUN",
        table_name=target_name,
        field_ids=field_ids,
        week_dates=[week.isoformat() for week in weeks],
    )


def merge_expected_structure(existing: WeeklyTable, week_start: date) -> WeeklyTable:
    expected = build_expected_weekly_table(existing.table_name, week_start)
    field_ids = dict(expected.field_ids)
    for name, field_id in existing.field_ids.items():
        field_ids[name] = field_id
    return WeeklyTable(
        table_id=existing.table_id,
        table_name=existing.table_name,
        field_ids=field_ids,
        week_dates=expected.week_dates,
    )


def create_weekly_table(target_name: str, week_start: date, verbose: bool) -> WeeklyTable:
    weeks = [week_start + timedelta(days=7 * index) for index in range(HORIZON_WEEKS)]
    initial_fields = [
        {"fieldName": "序号", "type": "text"},
        {"fieldName": "物料编码", "type": "text"},
        {"fieldName": "物料名称", "type": "text"},
        {"fieldName": "类型", "type": "text"},
    ]
    initial_fields.extend({"fieldName": week.isoformat(), "type": "text"} for week in weeks[:11])
    create_payload = run_dws(
        [
            "dws",
            "aitable",
            "table",
            "create",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--name",
            target_name,
            "--fields",
            json.dumps(initial_fields, ensure_ascii=False),
            "--format",
            "json",
        ]
    )
    table_id = (create_payload.get("data") or {}).get("tableId")
    if not table_id:
        raise SkillError(f"建采购计划表失败，返回中缺少 tableId: {json.dumps(create_payload, ensure_ascii=False)}")
    if verbose:
        print(json.dumps({"action": "createdTable", "tableId": table_id}, ensure_ascii=False, indent=2))
    payload = run_dws(
        [
            "dws",
            "aitable",
            "table",
            "get",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--table-ids",
            table_id,
            "--format",
            "json",
        ]
    )
    tables = (payload.get("data") or {}).get("tables") or []
    if not tables:
        raise SkillError(f"新建采购计划表后无法读取结构: {table_id}")
    return ensure_weekly_table_structure(table_to_weekly_table(tables[0]), week_start, verbose)


def ensure_weekly_table_structure(table: WeeklyTable, week_start: date, verbose: bool) -> WeeklyTable:
    expected_weeks = [week_start + timedelta(days=7 * index) for index in range(HORIZON_WEEKS)]
    missing_fields = []
    for week in expected_weeks:
        name = week.isoformat()
        if name not in table.field_ids:
            missing_fields.append({"fieldName": name, "type": "text"})
    if "仓库" not in table.field_ids:
        missing_fields.append({"fieldName": "仓库", "type": "text"})
    if missing_fields:
        run_dws(
            [
                "dws",
                "aitable",
                "field",
                "create",
                "--base-id",
                PURCHASE_PLAN_BASE_ID,
                "--table-id",
                table.table_id,
                "--fields",
                json.dumps(missing_fields, ensure_ascii=False),
                "--format",
                "json",
            ]
        )
        if verbose:
            print(
                json.dumps(
                    {
                        "action": "patchWeeklyTableFields",
                        "tableId": table.table_id,
                        "missingFields": [item["fieldName"] for item in missing_fields],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        payload = run_dws(
            [
                "dws",
                "aitable",
                "table",
                "get",
                "--base-id",
                PURCHASE_PLAN_BASE_ID,
                "--table-ids",
                table.table_id,
                "--format",
                "json",
            ]
        )
        tables = (payload.get("data") or {}).get("tables") or []
        if not tables:
            raise SkillError(f"补齐周表字段后无法重新读取结构: {table.table_id}")
        table = table_to_weekly_table(tables[0])
    return table


def table_to_weekly_table(table: Dict[str, object]) -> WeeklyTable:
    table_id = str(table.get("tableId") or "")
    table_name = str(table.get("tableName") or "")
    field_ids: Dict[str, str] = {}
    week_dates: List[str] = []
    for field in table.get("fields") or []:
        field_name = str(field.get("fieldName") or "")
        field_id = str(field.get("fieldId") or "")
        if not field_name or not field_id:
            continue
        field_ids[field_name] = field_id
        if is_date_text(field_name):
            week_dates.append(field_name)
    week_dates.sort()
    return WeeklyTable(table_id=table_id, table_name=table_name, field_ids=field_ids, week_dates=week_dates)


def is_date_text(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load_existing_plan_keys(table_id: str, field_ids: Dict[str, str]) -> Dict[Tuple[str, str], List[str]]:
    if table_id == "DRY_RUN":
        return {}
    cursor = None
    seen = set()
    records_by_key: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    query_field_ids = ",".join(
        [field_ids["物料编码"], field_ids["仓库"], field_ids["类型"]]
    )
    while True:
        command = [
            "dws",
            "aitable",
            "record",
            "query",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--table-id",
            table_id,
            "--field-ids",
            query_field_ids,
            "--limit",
            "100",
            "--format",
            "json",
        ]
        if cursor:
            command.extend(["--cursor", cursor])
        payload = run_dws(command)
        data = payload.get("data") or {}
        records = data.get("records") or []
        for record in records:
            cells = record.get("cells") or {}
            material_code = str(cells.get(field_ids["物料编码"]) or "").strip()
            warehouse = str(cells.get(field_ids["仓库"]) or "").strip()
            record_id = str(record.get("recordId") or "")
            if material_code and warehouse and record_id:
                records_by_key[(material_code, warehouse)].append(record_id)
        next_cursor = data.get("nextCursor")
        if not next_cursor or next_cursor in seen or len(records) < 100:
            break
        seen.add(next_cursor)
        cursor = next_cursor
    return records_by_key


def build_plan_rows(
    material_code: str,
    material_name: str,
    warehouse: str,
    horizon: Sequence[date],
    demand_by_week: Dict[str, Decimal],
    stock_rows: Sequence[Dict[str, object]],
    transit_rows: Sequence[Dict[str, object]],
    eoq: EoqRecord,
    field_ids: Dict[str, str],
) -> PlanRows:
    if eoq.lead_days <= 0:
        raise SkillError("采购提前期必须为正整数")
    week_keys = [item.isoformat() for item in horizon]
    demand_series = {week: quantize_int(demand_by_week.get(week, Decimal("0"))) for week in week_keys}
    initial_stock = quantize_int(compute_initial_stock(stock_rows, warehouse, horizon[0]))
    transit_series = compute_transit_by_week(transit_rows, warehouse, week_keys)
    purchase_series = {week: 0 for week in week_keys}
    inventory_series: Dict[str, int] = {}
    warnings: List[str] = []
    anomalies: List[str] = []

    available_end = initial_stock
    planned_arrivals = {week: 0 for week in week_keys}
    lead_weeks = max(1, math.ceil(eoq.lead_days / 7))

    for index, week in enumerate(week_keys):
        available_start = available_end + transit_series.get(week, 0) + planned_arrivals.get(week, 0)
        demand_qty = demand_series.get(week, 0)
        if available_start < demand_qty:
            gap = compute_gap(index, week_keys, available_start, demand_series, transit_series, planned_arrivals)
            qty = apply_procurement_rule(gap, eoq.moq, eoq.eoq)
            planned_arrivals[week] += qty
            order_index = index - lead_weeks
            if order_index < 0:
                purchase_series[week_keys[0]] += qty
                warnings.append(
                    f"{material_code}/{warehouse} 在 {week} 出现缺口，需要提前于计划开始前下单，已记入首周采购 {qty}"
                )
            else:
                purchase_series[week_keys[order_index]] += qty
            available_start += qty
        available_end = available_start - demand_qty
        inventory_series[week] = quantize_int(Decimal(available_end))

    anomalies.extend(validate_plan(material_code, warehouse, demand_series, inventory_series, purchase_series))

    rows = [
        {
            "type": "需求数量",
            "cells": build_cells(field_ids, material_code, material_name, warehouse, "需求数量", demand_series, "1"),
        },
        {
            "type": "库存数量",
            "cells": build_cells(field_ids, material_code, material_name, warehouse, "库存数量", inventory_series, "2"),
        },
        {
            "type": "采购数量",
            "cells": build_cells(field_ids, material_code, material_name, warehouse, "采购数量", purchase_series, "3"),
        },
    ]
    return PlanRows(
        material_code=material_code,
        material_name=material_name,
        warehouse=warehouse,
        demand=demand_series,
        inventory=inventory_series,
        purchase=purchase_series,
        rows=rows,
        warnings=warnings,
        anomalies=anomalies,
    )


def validate_plan(
    material_code: str,
    warehouse: str,
    demand_series: Dict[str, int],
    inventory_series: Dict[str, int],
    purchase_series: Dict[str, int],
) -> List[str]:
    anomalies: List[str] = []
    for week, value in demand_series.items():
        if value < 0:
            anomalies.append(f"{material_code}/{warehouse} 在 {week} 出现负需求: {value}")
    for week, value in inventory_series.items():
        if value < 0:
            anomalies.append(f"{material_code}/{warehouse} 在 {week} 出现负库存: {value}")
    for week, value in purchase_series.items():
        if value < 0:
            anomalies.append(f"{material_code}/{warehouse} 在 {week} 出现负采购: {value}")
    return anomalies


def compute_initial_stock(rows: Sequence[Dict[str, object]], warehouse: str, week_start: date) -> Decimal:
    total = Decimal("0")
    for row in rows:
        if str(row.get("仓库") or "").strip() != warehouse:
            continue
        expiry = normalize_optional_date(row.get("到期日期"))
        if expiry and expiry < week_start:
            continue
        total += normalize_decimal(row.get("台账"))
    return total


def compute_transit_by_week(
    rows: Sequence[Dict[str, object]], warehouse: str, week_keys: Sequence[str]
) -> Dict[str, int]:
    horizon_set = set(week_keys)
    result = {week: 0 for week in week_keys}
    for row in rows:
        if str(row.get("仓库") or "").strip() != warehouse:
            continue
        qty = normalize_decimal(row.get("计划在途量"))
        if qty <= 0:
            continue
        week = normalize_week_key(row.get("要求到货日期"))
        if week in horizon_set:
            result[week] += quantize_int(qty)
    return result


def compute_gap(
    start_index: int,
    week_keys: Sequence[str],
    available_start: int,
    demand_series: Dict[str, int],
    transit_series: Dict[str, int],
    planned_arrivals: Dict[str, int],
) -> int:
    balance = available_start - demand_series.get(week_keys[start_index], 0)
    min_balance = balance
    for week in week_keys[start_index + 1 :]:
        balance = balance + transit_series.get(week, 0) + planned_arrivals.get(week, 0) - demand_series.get(week, 0)
        min_balance = min(min_balance, balance)
        if balance >= 0 and min_balance >= 0:
            break
    return max(0, -min_balance)


def apply_procurement_rule(gap: int, moq: int, eoq: int) -> int:
    moq = max(0, moq)
    eoq = max(moq, eoq) if eoq > 0 else moq
    if eoq <= moq:
        return max(gap, moq)
    threshold = Decimal(moq) + Decimal("0.3") * Decimal(eoq - moq)
    if Decimal(gap) <= threshold:
        return max(gap, moq)
    return max(gap, eoq)


def build_cells(
    field_ids: Dict[str, str],
    material_code: str,
    material_name: str,
    warehouse: str,
    row_type: str,
    values: Dict[str, int],
    seq: str,
) -> Dict[str, object]:
    cells = {
        field_ids["序号"]: seq,
        field_ids["物料编码"]: material_code,
        field_ids["物料名称"]: material_name,
        field_ids["类型"]: row_type,
        field_ids["仓库"]: warehouse,
    }
    for week, value in values.items():
        field_id = field_ids.get(week)
        if field_id:
            cells[field_id] = str(value)
    return cells


def sort_record_ids_by_type(record_ids: Sequence[str], weekly_table: WeeklyTable, rows: Sequence[Dict[str, object]]) -> List[str]:
    payload = run_dws(
        [
            "dws",
            "aitable",
            "record",
            "query",
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--table-id",
            weekly_table.table_id,
            "--record-ids",
            ",".join(record_ids),
            "--field-ids",
            weekly_table.field_ids["类型"],
            "--format",
            "json",
        ]
    )
    existing_by_type = {}
    for record in (payload.get("data") or {}).get("records") or []:
        row_type = str((record.get("cells") or {}).get(weekly_table.field_ids["类型"]) or "").strip()
        if row_type:
            existing_by_type[row_type] = str(record.get("recordId") or "")
    ordered = []
    for row in rows:
        row_type = row["type"]
        record_id = existing_by_type.get(row_type)
        if not record_id:
            raise SkillError(f"当前周表中缺少类型 {row_type} 对应的历史记录，无法执行覆盖更新")
        ordered.append(record_id)
    return ordered


def batch_write_records(mode: str, table_id: str, records: Sequence[Dict[str, object]]) -> None:
    chunk_size = WRITE_BATCH_KEYS * 3
    for start in range(0, len(records), chunk_size):
        chunk = records[start : start + chunk_size]
        command = [
            "dws",
            "aitable",
            "record",
            mode,
            "--base-id",
            PURCHASE_PLAN_BASE_ID,
            "--table-id",
            table_id,
            "--records",
            json.dumps(chunk, ensure_ascii=False),
            "--format",
            "json",
        ]
        run_dws(command)


def run_dws(command: Sequence[str]) -> Dict[str, object]:
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        raise SkillError(
            f"dws 执行失败: {' '.join(command)}\nstdout:\n{process.stdout}\nstderr:\n{process.stderr}"
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise SkillError(f"dws 输出不是合法 JSON: {exc}\n原始输出:\n{process.stdout}") from exc
    if payload.get("status") != "success":
        raise SkillError(f"dws 返回失败: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def render_report(
    week_start: date,
    weekly_table: WeeklyTable,
    processed_keys: Sequence[Tuple[str, str]],
    created_row_count: int,
    updated_row_count: int,
    skipped: Sequence[Tuple[str, str]],
    failures: Sequence[Dict[str, str]],
    warnings: Sequence[str],
    anomalies: Sequence[str],
    generated: Sequence[PlanRows],
) -> str:
    period_end = week_start + timedelta(days=7 * (HORIZON_WEEKS - 1))
    url = f"https://docs.dingtalk.com/i/nodes/{PURCHASE_PLAN_BASE_ID}?iframeQuery=sheetId%3D{weekly_table.table_id}"
    lines = [
        f"# 采购计划生成报告 - {week_start.isoformat()}",
        "",
        f"- 周期范围：{week_start.isoformat()} 至 {period_end.isoformat()}",
        f"- 计划表：[{weekly_table.table_name}]({url})",
        f"- 处理物料仓库组合：{len(processed_keys)}",
        f"- 成功生成组合：{len(generated)}",
        f"- 新增行数：{created_row_count}",
        f"- 更新行数：{updated_row_count}",
        f"- 跳过组合：{len(skipped)}",
        f"- 失败组合：{len(failures)}",
        f"- 风险/预警：{len(warnings)}",
        f"- 异常：{len(anomalies)}",
        "",
        "## 执行摘要",
        "",
    ]
    if generated:
        total_purchase = sum(sum(plan.purchase.values()) for plan in generated)
        lines.extend(
            [
                f"- 计划总采购量：{total_purchase}",
                f"- 涉及仓库数：{len({plan.warehouse for plan in generated})}",
                f"- 涉及物料数：{len({plan.material_code for plan in generated})}",
            ]
        )
    else:
        lines.append("- 本次没有生成新的采购计划行。")
    lines.extend(["", "## 风险与异常", ""])
    if not failures and not warnings and not anomalies:
        lines.append("- 无异常。")
    else:
        for warning in warnings[:20]:
            lines.append(f"- {warning}")
        for anomaly in anomalies[:50]:
            lines.append(f"- 异常：{anomaly}")
        for failure in failures[:20]:
            lines.append(
                f"- 失败：{failure['materialCode']} / {failure['warehouse']} -> {failure['error']}"
            )
    lines.extend(["", "## 跳过组合", ""])
    if skipped:
        for material_code, warehouse in skipped[:20]:
            lines.append(f"- {material_code} / {warehouse}")
    else:
        lines.append("- 无。")
    return "\n".join(lines)


def send_markdown_bot_message(report: str, week_start: date) -> bool:
    title = f"采购计划生成报告-{week_start.isoformat()}"
    payload = run_dws(
        [
            "dws",
            "chat",
            "message",
            "send-by-bot",
            "--robot-code",
            DEFAULT_NOTIFY_ROBOT_CODE,
            "--users",
            DEFAULT_NOTIFY_USERS,
            "--title",
            title,
            "--text",
            report,
            "--format",
            "json",
        ]
    )
    return bool(payload.get("success"))


def log(args: argparse.Namespace, message: str) -> None:
    if args.verbose:
        print(f"[purchase-plan] {message}", file=sys.stderr)


def normalize_decimal(value: object) -> Decimal:
    if value in (None, "", "null"):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def normalize_int(value: object) -> int:
    return quantize_int(normalize_decimal(value))


def quantize_int(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def normalize_optional_date(value: object) -> Optional[date]:
    if value in (None, "", "null"):
        return None
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def normalize_week_key(value: object) -> str:
    parsed = datetime.strptime(str(value), "%Y-%m-%d").date()
    monday = parsed - timedelta(days=parsed.weekday())
    return monday.isoformat()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SkillError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
