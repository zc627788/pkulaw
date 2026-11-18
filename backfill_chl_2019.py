# -*- coding: utf-8 -*-
"""
专项补采：2019 年中央法规 部门工作文件(XE0303) -> 生态环境部(60242) 共 871 篇
通过多排序 + 标题高频关键词切片，突破单查询 200 条上限并追加写入 records_chl.jsonl 后重新导出。
"""

import argparse
import concurrent.futures
import os
import threading
from pathlib import Path

from checkpoint import CrawlCheckpoint
from exporter import deduplicate_records, export_csv, export_xlsx
from parser import parse_record_search_html
from pkulaw_client import PkulawClient
from proxy_pool import ProxyPool

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"

KEYWORDS = [
    "",  # 先跑全量多排序
    "复函", "批复", "公告", "意见", "办法", "规定", "函", "通报", "公示",
    "方案", "指南", "规范", "标准", "监测", "督察", "规划", "清单", "名录", "目录",
    "环评", "验收", "排污", "辐射", "核", "水", "大气", "固废", "土壤", "海洋",
    "生态", "污染", "防治", "治理", "整改", "检查", "抽查", "考核", "评估", "试点",
    "示范", "专项", "行动", "措施", "制度", "安全", "应急", "风险", "平台", "系统",
    "数据", "统计", "资金", "预算", "补贴", "处罚", "调整", "修改", "废止", "答复",
    "请示", "报告", "会议", "培训", "建设", "企业", "公司", "项目", "加强", "开展",
    "做好", "印发", "进一步", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "第", "年", "月", "市", "省", "区", "县", "部", "委", "局", "厅", "院", "中心",
]


def main():
    parser_cli = argparse.ArgumentParser(description="2019 年中央法规生态环境部缺口专项补采")
    parser_cli.add_argument("--proxy", default=os.getenv("PKULAW_PROXY", ""), help="代理地址 (如 http://user:pass@host:port)")
    args = parser_cli.parse_args()

    ckpt = CrawlCheckpoint(OUTPUT_DIR, library="chl")
    existing = ckpt.load_all_records()
    existing_gids = {r.get("法宝ID") for r in existing if r.get("法宝ID")}
    print(f"📦 当前 records_chl.jsonl 已有去重记录: {len(existing_gids)} 条", flush=True)

    pool = ProxyPool(tunnel_proxy=args.proxy) if args.proxy else None
    tls = threading.local()
    lock = threading.Lock()
    new_rows = []
    seen_2019_mee = set()

    def get_client():
        c = getattr(tls, "c", None)
        if c is None:
            c = PkulawClient(
                library="chl",
                min_delay=0.05,
                max_delay=0.15,
                batch_pause_every=0,
                proxy_pool=pool,
            )
            c.warmup()
            tls.c = c
        return c

    def fetch_kw(kw: str):
        c = get_client()
        orders = [0, 1, 2, 3, 4, 5] if kw == "" else [0, 1]
        local_rows = []
        for ob in orders:
            h0 = c.search_records_page(
                category="089",
                effectiveness="XE0303",
                issue_department="60242",
                issue_date="2019",
                keywords=kw,
                match_type="Fuzzy" if kw else "Exact",
                page_index=0,
                page_size=100,
                order_by_index=ob,
            )
            tot, r0 = parse_record_search_html(h0, province_name="", issue_year_hint="2019")
            local_rows.extend(r0)
            if tot > 100 and len(r0) == 100:
                h1 = c.search_records_page(
                    category="089",
                    effectiveness="XE0303",
                    issue_department="60242",
                    issue_date="2019",
                    keywords=kw,
                    match_type="Fuzzy" if kw else "Exact",
                    page_index=1,
                    page_size=100,
                    order_by_index=ob,
                )
                _, r1 = parse_record_search_html(h1, province_name="", issue_year_hint="2019")
                local_rows.extend(r1)
            if kw != "" and tot <= 200:
                break
        with lock:
            added_here = 0
            for r in local_rows:
                gid = r.get("法宝ID")
                if gid:
                    seen_2019_mee.add(gid)
                    if gid not in existing_gids:
                        existing_gids.add(gid)
                        new_rows.append(r)
                        added_here += 1
            if added_here > 0:
                print(f"  + kw='{kw or 'ALL'}' 新增 {added_here} 条 (2019生态环境部已覆盖 {len(seen_2019_mee)}/871)", flush=True)

    # 先跑全量多排序
    fetch_kw("")
    # 再并发跑关键词切片，达到 871 即提前停止
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fetch_kw, kw) for kw in KEYWORDS[1:]]
        for fut in concurrent.futures.as_completed(futs):
            fut.result()

    print(f"\n✅ 2019 生态环境部补采完成：共覆盖 {len(seen_2019_mee)}/871 篇，净新增 {len(new_rows)} 条", flush=True)
    if new_rows:
        ckpt.append_records(new_rows)

    all_rows = deduplicate_records(ckpt.load_all_records())
    csv_path = OUTPUT_DIR / "pkulaw_chl_env_laws.csv"
    xlsx_path = OUTPUT_DIR / "pkulaw_chl_env_laws.xlsx"
    export_csv(all_rows, csv_path)
    export_xlsx(all_rows, xlsx_path, sheet_title="中央法规_环境保护")
    print(f"🎉 已重新导出中央法规全量：去重后共 {len(all_rows)} 条 -> {csv_path.name} / {xlsx_path.name}", flush=True)


if __name__ == "__main__":
    main()
