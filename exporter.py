# -*- coding: utf-8 -*-
"""
结果去重与 CSV / XLSX 导出模块
"""

import csv
from pathlib import Path
from typing import Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from parser import CSV_COLUMNS


def deduplicate_records(records: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """按 法宝ID（或 标题+发文字号+公布日期）保序去重"""
    seen = set()
    unique: List[Dict[str, str]] = []
    for r in records:
        gid = (r.get("法宝ID") or "").strip()
        key = ("gid", gid) if gid else ("meta", r.get("标题", ""), r.get("发文字号", ""), r.get("公布日期", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def export_csv(records: List[Dict[str, str]], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(records)


def export_xlsx(records: List[Dict[str, str]], xlsx_path: Path, sheet_title: str = "环境保护法规"):
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title

    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Microsoft YaHei", size=10, bold=True, color="FFFFFF")
    body_font = Font(name="Microsoft YaHei", size=10)

    ws.append(CSV_COLUMNS)
    for col_idx in range(1, len(CSV_COLUMNS) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for r in records:
        ws.append([r.get(col, "") for col in CSV_COLUMNS])

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            cell.font = body_font

    widths = {
        "标题": 52,
        "省份": 14,
        "时效性": 12,
        "效力位阶": 18,
        "制定机关": 28,
        "发文字号": 30,
        "公布日期": 14,
        "施行日期": 14,
        "发布年份": 10,
        "法宝ID": 24,
        "详情链接": 44,
    }
    for idx, col_name in enumerate(CSV_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = widths.get(col_name, 16)

    ws.freeze_panes = "A2"
    wb.save(xlsx_path)
