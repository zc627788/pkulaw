# -*- coding: utf-8 -*-
"""
北大法宝列表 HTML 解析器

将每条记录解析为结构化字段，严格对齐截图中的 sub 标题每一项：
  - 标题
  - 省份
  - 时效性 (如: 现行有效)
  - 效力位阶 (如: 省级地方性法规)
  - 制定机关 (如: 衢州市第八届人民代表大会常务委员会 / 北京市人大(含常委会))
  - 发文字号 (如: 衢州市第八届人民代表大会常务委员会公告第17号)
  - 公布日期 (如: 2026.11.02)
  - 施行日期 (如: 2026.11.02)
  - 发布年份 (遍历年份或公布日期年份)
  - 法宝ID (gid)
  - 详情链接

任何字段缺失时均返回空字符串 ""，确保列对齐不报错。
"""

import re
from typing import Dict, List, Tuple
from bs4 import BeautifulSoup

BASE_URL = "https://www.pkulaw.com"

CSV_COLUMNS = [
    "标题",
    "省份",
    "时效性",
    "效力位阶",
    "制定机关",
    "发文字号",
    "公布日期",
    "施行日期",
    "发布年份",
    "法宝ID",
    "详情链接",
]


def _parse_info_div(info_div) -> Dict[str, str]:
    result = {
        "时效性": "",
        "效力位阶": "",
        "制定机关": "",
        "发文字号": "",
        "公布日期": "",
        "施行日期": "",
    }
    if info_div is None:
        return result

    # 1. 优先通过语义链接精确提取 时效性 / 效力位阶 / 制定机关
    timeliness_a = info_div.select_one('a[href*="Aggs.TimelinessDic"]')
    if timeliness_a:
        result["时效性"] = timeliness_a.get_text(strip=True)

    eff_a = info_div.select_one('a[href*="Aggs.EffectivenessDic"]')
    if eff_a:
        result["效力位阶"] = eff_a.get_text(strip=True)

    dept_as = info_div.select('a[href*="Aggs.IssueDepartment"]')
    if dept_as:
        result["制定机关"] = "、".join(a.get_text(strip=True) for a in dept_as if a.get_text(strip=True))

    # 2. 通过 <span class="text"> 提取 发文字号 / 公布日期 / 施行日期
    doc_nos: List[str] = []
    for sp in info_div.select("span.text"):
        txt = sp.get_text(strip=True)
        if not txt:
            continue
        if txt.endswith("公布"):
            result["公布日期"] = txt[:-2].strip()
        elif txt.endswith("施行"):
            result["施行日期"] = txt[:-2].strip()
        else:
            doc_nos.append(txt)

    if doc_nos:
        result["发文字号"] = " ".join(doc_nos)

    # 3. 若页面结构缺少语义链接，兜底按分隔文本正则识别
    if not any(result.values()):
        raw = info_div.get_text(" / ", strip=True)
        for seg in [s.strip() for s in raw.split("/") if s.strip()]:
            if seg.endswith("公布"):
                result["公布日期"] = seg[:-2].strip()
            elif seg.endswith("施行"):
                result["施行日期"] = seg[:-2].strip()
            elif seg in ("现行有效", "废止或失效", "已被修改", "尚未施行", "部分废止或失效"):
                result["时效性"] = seg
            elif not result["发文字号"]:
                result["发文字号"] = seg

    return result


def parse_record_search_html(
    html: str,
    province_name: str = "",
    issue_year_hint: str = "",
) -> Tuple[int, List[Dict[str, str]]]:
    """返回 (总篇数, 记录字典列表)"""
    soup = BeautifulSoup(html, "lxml")

    total = 0
    m_tot = re.search(r"共\s*<[^>]*>(\d+)</[^>]*>\s*篇|共\s*(\d+)\s*篇", html)
    if m_tot:
        total = int(m_tot.group(1) or m_tot.group(2))

    rows: List[Dict[str, str]] = []
    for a in soup.select("h4 a[data-gid]"):
        title = a.get_text(strip=True)
        gid = (a.get("data-gid") or "").strip()
        href = (a.get("href") or "").strip()
        if href.startswith("/"):
            href = BASE_URL + href

        container = a.find_parent("li") or a.find_parent("div", class_="block") or a.parent.parent.parent
        info_div = container.select_one("div.info") if container else None
        parsed_info = _parse_info_div(info_div)

        pub_date = parsed_info["公布日期"]
        year_val = issue_year_hint or (pub_date[:4] if len(pub_date) >= 4 and pub_date[:4].isdigit() else "")

        rows.append({
            "标题": title,
            "省份": province_name,
            "时效性": parsed_info["时效性"],
            "效力位阶": parsed_info["效力位阶"],
            "制定机关": parsed_info["制定机关"],
            "发文字号": parsed_info["发文字号"],
            "公布日期": pub_date,
            "施行日期": parsed_info["施行日期"],
            "发布年份": year_val,
            "法宝ID": gid,
            "详情链接": href,
        })

    return total, rows
