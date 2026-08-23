#!/usr/bin/env python3
import argparse
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


API_URL = "https://scmapi.lechun.cc/lechun-bi/commonSql/execute"
API_PAYLOAD = {"sqlId": 2, "dingId": "030966113929310965"}

BASE_ID = "G1DKw2zgV2RglZAbFBKRk9DlVB5r9YAn"
TABLE_ID = "hERWDMS"

FIELD_IDS = {
    "物料编码": "atJLEMM",
    "物料名称": "dXcFKv3",
    "采购供应商": "ebuXfax",
    "最小订购量（MOQ）": "Ru6Nisp",
    "最优采购量": "dvqPAul",
    "是否价格拐点": "SRjBa4i",
    "采购提前期（天）": "EajQCSf",
    "采购梯度": "46Xrw1r",
    "说明": "YTbuCoh",
}

PRICE_SPLIT_RE = re.compile(r"[,，;\n]+")
TIER_RE = re.compile(
    r"^\s*(?P<lower>\d+)\s*(?:-\s*(?P<upper>\d+)|\+)?\s*:\s*(?P<price>\d+(?:\.\d+)?)\s*$"
)


class SkillError(RuntimeError):
    pass


@dataclass
class PriceTier:
    lower: int
    upper: Optional[int]
    price: Decimal


@dataclass
class SupplierChoice:
    row: Dict[str, object]
    recommended_qty: int
    turning_point: bool
    explanation: str
    effective_price: Decimal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算 EOQ 建议值并写入钉钉 AI 表格")
    parser.add_argument("--material-code", help="仅处理指定物料编码")
    parser.add_argument("--force", action="store_true", help="强制重算并覆盖已存在记录")
    parser.add_argument("--dry-run", action="store_true", help="只预览结果，不写入 AI 表格")
    parser.add_argument("--limit-materials", type=int, help="仅处理前 N 个物料，便于调试")
    parser.add_argument("--verbose", action="store_true", help="输出更多调试信息")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = fetch_material_rows()
    grouped = group_material_rows(rows)

    if args.material_code:
        grouped = {args.material_code: grouped.get(args.material_code, [])}
        if not grouped[args.material_code]:
            raise SkillError(f"未在原料主数据中找到物料编码 {args.material_code}")

    material_codes = [code for code, items in grouped.items() if items]
    if args.limit_materials:
        material_codes = material_codes[: args.limit_materials]
        grouped = {code: grouped[code] for code in material_codes}

    existing_map = load_existing_record_map()

    to_create = []
    to_update = []
    skipped = []
    failed = []

    for material_code in material_codes:
        item_rows = grouped[material_code]
        existing_record = existing_map.get(material_code)
        if existing_record and not args.force:
            skipped.append(material_code)
            continue

        try:
            choice = choose_best_supplier(item_rows)
            cells = build_cells(choice)
            if existing_record and args.force:
                to_update.append({"recordId": existing_record, "cells": cells})
            else:
                to_create.append({"cells": cells})
        except Exception as exc:  # pragma: no cover - 运行期保护
            failed.append({"materialCode": material_code, "error": str(exc)})

    if args.verbose or args.dry_run:
        preview = {
            "create": to_create[:5],
            "update": to_update[:5],
            "skipped": skipped[:20],
            "failed": failed[:20],
        }
        print(json.dumps(preview, ensure_ascii=False, indent=2))

    if not args.dry_run:
        if to_create:
            batch_write_records("create", to_create)
        if to_update:
            batch_write_records("update", to_update)

    summary = {
        "materialCount": len(material_codes),
        "createdCount": len(to_create),
        "updatedCount": len(to_update),
        "skippedCount": len(skipped),
        "failedCount": len(failed),
        "failedMaterials": failed,
        "dryRun": args.dry_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


def fetch_material_rows() -> List[Dict[str, object]]:
    raw = request_material_payload()
    data = json.loads(raw)
    value = data.get("value")
    if not isinstance(value, dict):
        raise SkillError(
            "原料主数据接口返回格式异常，value 不是对象:"
            f" value_type={type(value).__name__}, keys={list(data.keys())[:10]}, raw={raw[:300]}"
        )
    rows = value.get("rows")
    if not isinstance(rows, list):
        raise SkillError("原料主数据接口返回格式异常，未找到 rows 数组")
    if not rows:
        raise SkillError("原料主数据接口返回空 rows，无法计算 EOQ")
    return rows


def request_material_payload() -> str:
    payload_text = json.dumps(API_PAYLOAD, ensure_ascii=False)
    payload = payload_text.encode("utf-8")
    api_key = os.getenv("LECHUN_API_KEY")
    for use_api_key in ([True, False] if api_key else [False]):
        raw = try_request_material_payload(payload, payload_text, api_key if use_api_key else None)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if parsed.get("status") == 200 and isinstance(parsed.get("value"), dict):
            return raw
    raise SkillError("原料主数据接口请求失败，未拿到有效采购数据")


def try_request_material_payload(payload: bytes, payload_text: str, api_key: Optional[str]) -> Optional[str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Caller-Key"] = api_key
    request = urllib.request.Request(API_URL, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8")
    except Exception:
        pass

    curl_command = [
        "curl",
        "-sS",
        API_URL,
        "-H",
        "Content-Type: application/json",
        "--data",
        payload_text,
    ]
    if api_key:
        curl_command.extend(["-H", f"X-Caller-Key: {api_key}"])
    process = subprocess.run(curl_command, capture_output=True, text=True, check=False)
    if process.returncode != 0:
        return None
    return process.stdout


def group_material_rows(rows: Iterable[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        material_code = str(row.get("物料编码") or "").strip()
        if not material_code:
            continue
        grouped[material_code].append(row)
    return dict(grouped)


def load_existing_record_map() -> Dict[str, str]:
    cursor = None
    seen_cursors = set()
    result: Dict[str, str] = {}
    while True:
        command = [
            "dws",
            "aitable",
            "record",
            "query",
            "--base-id",
            BASE_ID,
            "--table-id",
            TABLE_ID,
            "--field-ids",
            FIELD_IDS["物料编码"],
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
            code = str((record.get("cells") or {}).get(FIELD_IDS["物料编码"]) or "").strip()
            record_id = record.get("recordId")
            if code and record_id and code not in result:
                result[code] = record_id
        next_cursor = data.get("nextCursor")
        if not next_cursor or next_cursor in seen_cursors or len(records) < 100:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return result


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


def choose_best_supplier(rows: Sequence[Dict[str, object]]) -> SupplierChoice:
    candidates = [evaluate_supplier(row) for row in rows]
    return min(
        candidates,
        key=lambda item: (
            item.effective_price,
            item.recommended_qty,
            normalize_int(item.row.get("采购提前期（天）"), default=999999),
            str(item.row.get("采购供应商") or ""),
        ),
    )


def evaluate_supplier(row: Dict[str, object]) -> SupplierChoice:
    tiers = parse_price_ladders(str(row.get("采购梯度") or ""))
    if not tiers:
        raise SkillError(f"物料 {row.get('物料编码')} 的采购梯度无法解析: {row.get('采购梯度')}")

    moq = normalize_int(row.get("最小订购量（MOQ）"), default=0)
    shelf_life = normalize_int(row.get("保质期(days)"), default=None)
    turning_index = find_turning_tier_index(tiers)

    if turning_index is not None:
        recommended_qty = max(moq, tiers[turning_index].lower)
        turning_point = True
        reason = f"检测到第{turning_index + 1}个阶梯出现性价比拐点，推荐量取该阶梯起订量。"
    elif len(tiers) > 1:
        recommended_qty = max(moq, tiers[-1].lower)
        turning_point = False
        reason = "未检测到明显价格拐点，按最大阶梯起始量推荐。"
    else:
        recommended_qty = max(moq, tiers[0].lower)
        turning_point = False
        reason = "采购梯度仅有一个阶梯，按 MOQ 与首阶梯起始量较大值推荐。"

    short_shelf_override = False
    if shelf_life is not None and shelf_life <= 15 and recommended_qty > moq:
        recommended_qty = max(moq, tiers[0].lower)
        short_shelf_override = True

    effective_price = resolve_effective_price(tiers, recommended_qty)
    explanation = build_explanation(
        supplier=str(row.get("采购供应商") or ""),
        recommended_qty=recommended_qty,
        turning_point=turning_point,
        reason=reason,
        short_shelf_override=short_shelf_override,
        shelf_life=shelf_life,
    )
    return SupplierChoice(
        row=row,
        recommended_qty=recommended_qty,
        turning_point=turning_point,
        explanation=explanation,
        effective_price=effective_price,
    )


def parse_price_ladders(raw_value: str) -> List[PriceTier]:
    tiers: List[PriceTier] = []
    for part in PRICE_SPLIT_RE.split(raw_value.strip()):
        if not part.strip():
            continue
        match = TIER_RE.match(part.strip())
        if not match:
            continue
        lower = int(match.group("lower"))
        upper = int(match.group("upper")) if match.group("upper") else None
        try:
            price = Decimal(match.group("price"))
        except InvalidOperation:
            continue
        tiers.append(PriceTier(lower=lower, upper=upper, price=price))
    tiers.sort(key=lambda item: item.lower)
    return tiers


def find_turning_tier_index(tiers: Sequence[PriceTier]) -> Optional[int]:
    if len(tiers) < 3:
        return None
    deltas: List[Decimal] = []
    for left, right in zip(tiers, tiers[1:]):
        if left.price <= 0:
            deltas.append(Decimal("0"))
        else:
            deltas.append((left.price - right.price) / left.price)
    for index in range(1, len(deltas)):
        if deltas[index] < Decimal("0.3") * deltas[index - 1]:
            return index
    return None


def resolve_effective_price(tiers: Sequence[PriceTier], quantity: int) -> Decimal:
    selected = tiers[0]
    for tier in tiers:
        upper_ok = tier.upper is None or quantity <= tier.upper
        if quantity >= tier.lower and upper_ok:
            selected = tier
    return selected.price


def build_explanation(
    supplier: str,
    recommended_qty: int,
    turning_point: bool,
    reason: str,
    short_shelf_override: bool,
    shelf_life: Optional[int],
) -> str:
    pieces = [f"供应商{supplier}的推荐采购量为{recommended_qty}。", reason]
    if turning_point:
        pieces.append("价格拐点判断结果为是。")
    else:
        pieces.append("价格拐点判断结果为否。")
    if short_shelf_override and shelf_life is not None:
        pieces.append(f"该物料保质期为{shelf_life}天，按短保策略优先采用更小批量高频采购。")
    return "".join(pieces)


def build_cells(choice: SupplierChoice) -> Dict[str, object]:
    row = choice.row
    return {
        FIELD_IDS["物料编码"]: str(row.get("物料编码") or ""),
        FIELD_IDS["物料名称"]: str(row.get("物料名称") or ""),
        FIELD_IDS["采购供应商"]: str(row.get("采购供应商") or ""),
        FIELD_IDS["最小订购量（MOQ）"]: normalize_int(row.get("最小订购量（MOQ）"), default=0),
        FIELD_IDS["最优采购量"]: choice.recommended_qty,
        FIELD_IDS["是否价格拐点"]: "是" if choice.turning_point else "否",
        FIELD_IDS["采购提前期（天）"]: normalize_int(row.get("采购提前期（天）"), default=0),
        FIELD_IDS["采购梯度"]: str(row.get("采购梯度") or ""),
        FIELD_IDS["说明"]: choice.explanation,
    }


def batch_write_records(action: str, records: Sequence[Dict[str, object]]) -> None:
    for start in range(0, len(records), 100):
        batch = list(records[start : start + 100])
        command = [
            "dws",
            "aitable",
            "record",
            action,
            "--base-id",
            BASE_ID,
            "--table-id",
            TABLE_ID,
            "--records",
            json.dumps(batch, ensure_ascii=False),
            "--format",
            "json",
            "--yes",
        ]
        run_dws(command)


def normalize_int(value: object, default: Optional[int]) -> Optional[int]:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return default
        return int(value)
    text = str(value).strip()
    if not text or text.lower() == "null":
        return default
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return default


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SkillError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
