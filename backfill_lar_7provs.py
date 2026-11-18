# -*- coding: utf-8 -*-
"""
专项补采：对差额 > 100 篇的 7 个省份（广东/山东/陕西/江西/浙江/山西/福建）
定位单年单机关 > 400 篇的叶子桶，使用多排序 + 标题高频词切片补全缺失记录，
追加写入 records_lar.jsonl 后，按“不去重（全量保留）”规则重新导出 CSV / XLSX。
"""

import argparse
import concurrent.futures
import json
import os
import threading
from collections import defaultdict
from pathlib import Path

from checkpoint import CrawlCheckpoint
from exporter import export_csv, export_xlsx
from parser import parse_record_search_html
from pkulaw_client import ClusterNode, PkulawClient
from proxy_pool import ProxyPool

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"

TARGET_PROVINCES = {
    "广东省",
    "山东省",
    "陕西省",
    "江西省",
    "浙江省",
    "山西省",
    "福建省",
}

KEYWORDS = [
    "",
    "通知", "批复", "复函", "函", "公告", "公示", "意见", "方案", "办法", "规定",
    "监测", "环评", "排污", "生态", "污染", "防治", "治理", "整改", "督察", "检查",
    "水", "大气", "固废", "土壤", "辐射", "标准", "规划", "清单", "目录", "考核",
    "验收", "许可", "安全", "应急", "建设", "项目", "企业", "公司", "加强", "开展",
    "印发", "关于", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
]


def main():
    parser_cli = argparse.ArgumentParser(description="7 省份大叶子桶专项补采")
    parser_cli.add_argument("--proxy", default=os.getenv("PKULAW_PROXY", ""), help="代理地址 (如 http://user:pass@host:port)")
    args = parser_cli.parse_args()

    ckpt = CrawlCheckpoint(OUTPUT_DIR, library="lar")
    existing = ckpt.load_all_records()
    prov_seen = defaultdict(set)
    for r in existing:
        gid = r.get("法宝ID", "")
        if gid:
            prov_seen[r.get("省份", "")].add(gid)

    print(f"📦 当前 records_lar.jsonl 总行数: {len(existing)}", flush=True)

    pool = ProxyPool(tunnel_proxy=args.proxy) if args.proxy else None
    tls = threading.local()
    lock = threading.Lock()

    def get_client() -> PkulawClient:
        c = getattr(tls, "c", None)
        if c is None:
            c = PkulawClient(
                library="lar",
                min_delay=0.05,
                max_delay=0.15,
                batch_pause_every=0,
                proxy_pool=pool,
            )
            c.warmup()
            tls.c = c
        return c

    c0 = get_client()
    years = [y for y in c0.get_cluster_tree("IssueDate", category="089") if int(y.id) >= 2012]
    all_depts = c0.get_cluster_tree("IssueDepartment", category="089")
    prov_id_map = {d.name: d.id for d in all_depts if d.pid == "-1" and d.name in TARGET_PROVINCES}
    print(f"🎯 目标省份 ID 映射: {prov_id_map}", flush=True)

    # 查找 (年份 × 省份) 下存在 > 400 篇的叶子机关桶
    big_leaf_tasks = []

    def scan_year(y_node: ClusterNode):
        wc = get_client()
        effs = [e for e in wc.get_cluster_tree("EffectivenessDic", category="089", issue_date=y_node.id) if e.pid == "-1" and e.count > 400]
        local_tasks = []
        for e in effs:
            depts = wc.get_cluster_tree("IssueDepartment", category="089", effectiveness=e.id, issue_date=y_node.id)
            children_map = defaultdict(list)
            for d in depts:
                children_map[d.pid].append(d)

            def find_big_leaves(node: ClusterNode, prov_name: str):
                if node.count <= 400:
                    return
                subs = [s for s in children_map.get(node.id, []) if s.count > 0]
                if subs:
                    for s in subs:
                        find_big_leaves(s, prov_name)
                else:
                    local_tasks.append((y_node.id, e.id, node.id, node.name, node.count, prov_name))

            for d in depts:
                if d.pid == "-1" and d.name in TARGET_PROVINCES and d.count > 400:
                    find_big_leaves(d, d.name)
        return local_tasks

    print("🔎 正在定位 7 省中单桶 > 400 篇的超大叶子机关...", flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(scan_year, years):
            big_leaf_tasks.extend(res)

    print(f"📌 共定位到 {len(big_leaf_tasks)} 个超大叶子桶:", flush=True)
    for t in big_leaf_tasks:
        print(f"   {t[0]} | {t[5]} | {t[3]} ({t[4]}篇)", flush=True)

    new_rows_total = []

    def backfill_leaf(task):
        y_id, e_id, d_id, d_name, d_cnt, prov_name = task
        wc = get_client()
        leaf_added = []
        for kw in KEYWORDS:
            orders = [0, 1, 2, 3, 4, 5] if kw == "" else [0, 1]
            for ob in orders:
                h0 = wc.search_records_page(
                    category="089",
                    effectiveness=e_id,
                    issue_department=d_id,
                    issue_date=y_id,
                    keywords=kw,
                    match_type="Fuzzy" if kw else "Exact",
                    page_index=0,
                    page_size=100,
                    order_by_index=ob,
                )
                tot, r0 = parse_record_search_html(h0, province_name=prov_name, issue_year_hint=y_id)
                batch = list(r0)
                if tot > 100 and len(r0) == 100:
                    h1 = wc.search_records_page(
                        category="089",
                        effectiveness=e_id,
                        issue_department=d_id,
                        issue_date=y_id,
                        keywords=kw,
                        match_type="Fuzzy" if kw else "Exact",
                        page_index=1,
                        page_size=100,
                        order_by_index=ob,
                    )
                    _, r1 = parse_record_search_html(h1, province_name=prov_name, issue_year_hint=y_id)
                    batch.extend(r1)

                with lock:
                    for row in batch:
                        gid = row.get("法宝ID", "")
                        if gid and gid not in prov_seen[prov_name]:
                            prov_seen[prov_name].add(gid)
                            leaf_added.append(row)
                            new_rows_total.append(row)

                if kw != "" and tot <= 200:
                    break
        print(f"  ✅ 补采 {y_id} | {prov_name} | {d_name} ({d_cnt}篇) -> 净补回 {len(leaf_added)} 条", flush=True)

    if big_leaf_tasks:
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
            futs = [ex.submit(backfill_leaf, t) for t in big_leaf_tasks]
            for f in concurrent.futures.as_completed(futs):
                f.result()

    if new_rows_total:
        ckpt.append_records(new_rows_total)
    print(f"\n🎉 7 省超大叶子桶补采完成：共净补回 {len(new_rows_total)} 条！", flush=True)

    # 按用户要求：不去重，直接全量导出
    all_lar = ckpt.load_all_records()
    export_csv(all_lar, OUTPUT_DIR / "pkulaw_lar_env_laws.csv")
    export_xlsx(all_lar, OUTPUT_DIR / "pkulaw_lar_env_laws.xlsx", sheet_title="地方法规_环境保护")
    print(f"✅ 地方法规（不去重全量）已导出: {len(all_lar)} 条 -> pkulaw_lar_env_laws.csv / .xlsx", flush=True)

    chl_ckpt = CrawlCheckpoint(OUTPUT_DIR, library="chl")
    all_chl = chl_ckpt.load_all_records()
    export_csv(all_chl, OUTPUT_DIR / "pkulaw_chl_env_laws.csv")
    export_xlsx(all_chl, OUTPUT_DIR / "pkulaw_chl_env_laws.xlsx", sheet_title="中央法规_环境保护")
    print(f"✅ 中央法规（不去重全量）已导出: {len(all_chl)} 条 -> pkulaw_chl_env_laws.csv / .xlsx", flush=True)


if __name__ == "__main__":
    main()
