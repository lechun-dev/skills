#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib import error, parse, request
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile


API_URL = "http://scmapi.lechun.cc/lechun-bi/commonSql/execute"
API_SQL_ID = 8
API_TIMEOUT_SECONDS = 30
CUSTOMER_CODE = "KH00001"
LOW_TEMP_WAREHOUSES: List[Tuple[str, str, Decimal]] = [
    ("京东北京仓", "00022", Decimal("3")),
    ("京东上海仓", "00023", Decimal("2")),
    ("松帆武汉低温仓", "00046", Decimal("1")),
]
PROMOTION_DAYS = ((6, 18), (11, 11))


@dataclass(frozen=True)
class ProductSales:
    product_id: str
    product_name: str
    plan_type: str
    quantity: Decimal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate WeChat low-temperature forecast Excel from commonSql sqlId=8."
    )
    parser.add_argument(
        "--today",
        default=date.today().isoformat(),
        help="Anchor day for the run, format YYYY-MM-DD. Defaults to today.",
    )
    parser.add_argument(
        "--api-url",
        default=API_URL,
        help="commonSql execute endpoint. Defaults to lechun-bi commonSql.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("LECHUN_API_KEY"),
        help="API key for X-Caller-Key. Defaults to LECHUN_API_KEY env.",
    )
    parser.add_argument(
        "--output-xlsx",
        required=True,
        help="Output Excel path, e.g. /tmp/wechat-low-temp-forecast.xlsx",
    )
    parser.add_argument(
        "--compare-xlsx",
        help="Optional comparison Excel path with product and warehouse names.",
    )
    parser.add_argument(
        "--output-json",
        help="Optional debug json path for forecast detail.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=API_TIMEOUT_SECONDS,
        help="HTTP timeout seconds. Defaults to 30.",
    )
    return parser.parse_args()


def parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def daterange(start_day: date, end_day: date) -> Iterable[date]:
    current = start_day
    while current <= end_day:
        yield current
        current += timedelta(days=1)


def week_range(anchor: date) -> Tuple[date, date]:
    week_start = anchor - timedelta(days=anchor.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def format_api_day(value: date) -> str:
    return value.isoformat()


def format_excel_day(value: date) -> str:
    return f"{value.year}/{value.month}/{value.day}"


def decimal_round(value: Decimal, digits: str = "0.01") -> Decimal:
    return value.quantize(Decimal(digits), rounding=ROUND_HALF_UP)


def decimal_ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding="ROUND_CEILING"))


def promotion_anchor_in_window(start_day: date, end_day: date) -> date | None:
    for current in daterange(start_day, end_day):
        if (current.month, current.day) in PROMOTION_DAYS:
            return current
    return None


def subtract_ranges(
    base_start: date, base_end: date, exclusions: List[Tuple[date, date]]
) -> List[Tuple[date, date]]:
    segments = [(base_start, base_end)]
    for exclusion_start, exclusion_end in sorted(exclusions):
        updated: List[Tuple[date, date]] = []
        for segment_start, segment_end in segments:
            if exclusion_end < segment_start or exclusion_start > segment_end:
                updated.append((segment_start, segment_end))
                continue
            if exclusion_start > segment_start:
                updated.append((segment_start, exclusion_start - timedelta(days=1)))
            if exclusion_end < segment_end:
                updated.append((exclusion_end + timedelta(days=1), segment_end))
        segments = updated
    return [item for item in segments if item[0] <= item[1]]


def promotion_week_exclusions(start_day: date, end_day: date) -> List[Tuple[date, date]]:
    exclusions: List[Tuple[date, date]] = []
    for year in range(start_day.year, end_day.year + 1):
        for month, day_value in PROMOTION_DAYS:
            promo_day = date(year, month, day_value)
            week_start, week_end = week_range(promo_day)
            if week_end < start_day or week_start > end_day:
                continue
            exclusions.append((max(week_start, start_day), min(week_end, end_day)))
    return exclusions


def choose_reference_window(today: date) -> Tuple[date, date, str]:
    forecast_start = today + timedelta(days=1)
    forecast_end = today + timedelta(days=7)
    promotion_day = promotion_anchor_in_window(forecast_start, forecast_end)
    if promotion_day:
        last_year_day = date(today.year - 1, promotion_day.month, promotion_day.day)
        week_start, week_end = week_range(last_year_day)
        reason = f"promotion_week:{promotion_day.month:02d}-{promotion_day.day:02d}"
        return week_start, week_end, reason

    lookback_end = today - timedelta(days=1)
    lookback_start = today - timedelta(days=60)
    segments = subtract_ranges(
        lookback_start,
        lookback_end,
        promotion_week_exclusions(lookback_start, lookback_end),
    )
    if not segments:
        raise RuntimeError("No valid history window after excluding promotion weeks.")

    selected = segments[-1]
    if len(segments) >= 2 and span_days(selected) < 15:
        selected = segments[-2]
    reason = "regular_window"
    return selected[0], selected[1], reason


def span_days(start_day: date, end_day: date | None = None) -> int:
    target_end = end_day or start_day
    return (target_end - start_day).days + 1


def fetch_sales(
    api_url: str,
    api_key: str,
    start_day: date,
    end_day: date,
    timeout_seconds: int,
) -> List[ProductSales]:
    if not api_key:
        raise RuntimeError("Missing API key. Provide --api-key or set LECHUN_API_KEY.")
    payload = {
        "sqlId": API_SQL_ID,
        "params": {
            "startDate": format_api_day(start_day),
            "endDate": format_api_day(end_day),
        },
    }
    url = f"{api_url}?sqlId={API_SQL_ID}"
    req = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Caller-Key": api_key,
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc

    result = json.loads(body)
    if not result.get("success"):
        raise RuntimeError(result.get("message") or "commonSql execute failed")
    rows = result.get("value", {}).get("rows") or []

    aggregated: Dict[Tuple[str, str], ProductSales] = {}
    for row in rows:
        product_id = str(row.get("PRODUCT_ID", "")).strip()
        if not product_id:
            continue
        plan_type = "" if row.get("PLAN_TYPE") is None else str(row.get("PLAN_TYPE")).strip()
        key = (product_id, plan_type)
        qty = Decimal(str(row.get("QUANTITY", "0")))
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = ProductSales(
                product_id=product_id,
                product_name=str(row.get("PRODUCT_NAME", "")).strip(),
                plan_type=plan_type,
                quantity=qty,
            )
        else:
            aggregated[key] = ProductSales(
                product_id=existing.product_id,
                product_name=existing.product_name or str(row.get("PRODUCT_NAME", "")).strip(),
                plan_type=existing.plan_type,
                quantity=existing.quantity + qty,
            )
    return sorted(aggregated.values(), key=lambda item: (item.plan_type, item.product_id))


def build_forecast_rows(
    today: date,
    products: List[ProductSales],
    reference_start: date,
    reference_end: date,
) -> List[dict]:
    day_count = Decimal(span_days(reference_start, reference_end))
    ratio_total = sum((ratio for _, _, ratio in LOW_TEMP_WAREHOUSES), Decimal("0"))
    rows: List[dict] = []
    for product in products:
        daily_avg = product.quantity / day_count
        rounded_daily_avg = decimal_round(daily_avg)
        for pickup_day in daterange(today + timedelta(days=1), today + timedelta(days=7)):
            for warehouse_name, warehouse_code, ratio in LOW_TEMP_WAREHOUSES:
                allocated_qty = decimal_ceil(daily_avg * ratio / ratio_total)
                rows.append(
                    {
                        "客户": CUSTOMER_CODE,
                        "物品": product.product_id,
                        "计划分类": product.plan_type,
                        "仓库": warehouse_code,
                        "提货日期": format_excel_day(pickup_day),
                        "数量": allocated_qty,
                        "产品名称": product.product_name,
                        "仓库名称": warehouse_name,
                        "参考区间开始": reference_start.isoformat(),
                        "参考区间结束": reference_end.isoformat(),
                        "参考区间天数": int(day_count),
                        "参考日均销量": str(rounded_daily_avg),
                        "参考总销量": str(decimal_round(product.quantity)),
                    }
                )
    return rows


def write_json(path: str, payload: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def col_ref(col_idx: int) -> str:
    chars: List[str] = []
    current = col_idx
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def xlsx_inline_cell(row_idx: int, col_idx: int, value: object) -> str:
    cell_ref = f"{col_ref(col_idx)}{row_idx}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{cell_ref}"><v>{value}</v></c>'
    text = "" if value is None else str(value)
    return f'<c r="{cell_ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def build_sheet_xml(headers: List[str], rows: List[dict]) -> str:
    xml_rows: List[str] = []
    header_cells = "".join(
        xlsx_inline_cell(1, idx + 1, header) for idx, header in enumerate(headers)
    )
    xml_rows.append(f'<row r="1">{header_cells}</row>')
    for row_idx, row in enumerate(rows, start=2):
        cells = "".join(
            xlsx_inline_cell(row_idx, idx + 1, row.get(header, ""))
            for idx, header in enumerate(headers)
        )
        xml_rows.append(f'<row r="{row_idx}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(xml_rows)}</sheetData>"
        "</worksheet>"
    )


def write_xlsx(path: str, rows: List[dict]) -> None:
    headers = ["客户", "物品", "计划分类", "仓库", "提货日期", "数量"]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_xml = build_sheet_xml(headers, rows)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "docProps/core.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
</cp:coreProperties>""",
        )
        zf.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf/></cellStyleXfs>
  <cellXfs count="1"><xf xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def write_compare_xlsx(path: str, rows: List[dict]) -> None:
    headers = [
        "客户",
        "物品",
        "商品名称",
        "计划分类",
        "仓库",
        "仓库名称",
        "提货日期",
        "数量",
    ]
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet_xml = build_sheet_xml(headers, rows)
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "docProps/core.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
</cp:coreProperties>""",
        )
        zf.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>""",
        )
        zf.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>""",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        zf.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf/></cellStyleXfs>
  <cellXfs count="1"><xf xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def build_debug_payload(
    today: date,
    reference_start: date,
    reference_end: date,
    reason: str,
    products: List[ProductSales],
    rows: List[dict],
) -> dict:
    return {
        "today": today.isoformat(),
        "forecast_start": (today + timedelta(days=1)).isoformat(),
        "forecast_end": (today + timedelta(days=7)).isoformat(),
        "reference_start": reference_start.isoformat(),
        "reference_end": reference_end.isoformat(),
        "reference_days": span_days(reference_start, reference_end),
        "reference_reason": reason,
        "product_count": len(products),
        "excel_row_count": len(rows),
        "products": [
            {
                "product_id": item.product_id,
                "product_name": item.product_name,
                "plan_type": item.plan_type,
                "quantity": str(decimal_round(item.quantity)),
                "daily_avg": str(
                    decimal_round(item.quantity / Decimal(span_days(reference_start, reference_end)))
                ),
            }
            for item in products
        ],
    }


def main() -> int:
    args = parse_args()
    today = parse_day(args.today)
    reference_start, reference_end, reason = choose_reference_window(today)
    products = fetch_sales(
        api_url=args.api_url,
        api_key=args.api_key,
        start_day=reference_start,
        end_day=reference_end,
        timeout_seconds=args.timeout_seconds,
    )
    forecast_rows = build_forecast_rows(
        today=today,
        products=products,
        reference_start=reference_start,
        reference_end=reference_end,
    )
    write_xlsx(args.output_xlsx, forecast_rows)
    if args.compare_xlsx:
        compare_rows = [
            {
                "客户": row["客户"],
                "物品": row["物品"],
                "商品名称": row["产品名称"],
                "计划分类": row["计划分类"],
                "仓库": row["仓库"],
                "仓库名称": row["仓库名称"],
                "提货日期": row["提货日期"],
                "数量": row["数量"],
            }
            for row in forecast_rows
        ]
        write_compare_xlsx(args.compare_xlsx, compare_rows)
    if args.output_json:
        write_json(
            args.output_json,
            build_debug_payload(
                today=today,
                reference_start=reference_start,
                reference_end=reference_end,
                reason=reason,
                products=products,
                rows=forecast_rows,
            ),
        )
    print(
        json.dumps(
            {
                "output_xlsx": str(Path(args.output_xlsx).resolve()),
                "reference_start": reference_start.isoformat(),
                "reference_end": reference_end.isoformat(),
                "reference_reason": reason,
                "product_count": len(products),
                "excel_row_count": len(forecast_rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
