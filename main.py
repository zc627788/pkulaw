# -*- coding: utf-8 -*-
"""
北大法宝 (pkulaw.com) 环境保护 (Category=089) 全量列表采集程序

支持两套采集规则（默认全跑，分别导出两套 CSV / XLSX）：
  1. 中央法规 (chl): 法律法规 -> 中央法规 -> 环境保护 (089)
     遍历: 发布年份 (IssueDate) × 效力位阶 (EffectivenessDic)
     若单桶 > 200 条，自动按 子效力位阶 / 制定机关 / 时效性 / 升降序 下钻切片。
     导出: output/pkulaw_chl_env_laws.csv & output/pkulaw_chl_env_laws.xlsx

  2. 地方法规 (lar): 法律法规 -> 地方法规 -> 环境保护 (089)
     遍历: 发布年份 (IssueDate) × 效力位阶 (EffectivenessDic) × 指定机关-省级 (IssueDepartment)
     若单桶 > 200 条，自动按 子级制定机关 / 时效性 / 升降序 下钻切片。
     导出: output/pkulaw_lar_env_laws.csv & output/pkulaw_lar_env_laws.xlsx
"""

import argparse
import concurrent.futures
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

from checkpoint import CrawlCheckpoint
from exporter import deduplicate_records, export_csv, export_xlsx
from parser import parse_record_search_html
from pkulaw_client import ClusterNode, PkulawClient, RateLimitBlockedError
from proxy_pool import ProxyPool

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "output"
GUEST_MAX_ITEMS = 200
PAGE_SIZE = 100

LIB_CONFIG = {
    "chl": {
        "label": "中央法规",
        "csv_name": "pkulaw_chl_env_laws.csv",
        "xlsx_name": "pkulaw_chl_env_laws.xlsx",
        "sheet": "中央法规_环境保护",
    },
    "lar": {
        "label": "地方法规",
        "csv_name": "pkulaw_lar_env_laws.csv",
        "xlsx_name": "pkulaw_lar_env_laws.xlsx",
        "sheet": "地方法规_环境保护",
    },
}


def fetch_bucket_records(
    client: PkulawClient,
    category: str,
    eff_id: str,
    dept_id: str,
    year_id: str,
    timeliness_id: str,
    province_name: str,
    expected_count: int,
) -> List[Dict[str, str]]:
    """抓取单个满足 <= 200 条（或已无法再细分）的切片桶"""
    collected: List[Dict[str, str]] = []
    orders = [0] if expected_count <= GUEST_MAX_ITEMS else [0, 1]

    for order_idx in orders:
        html0 = client.search_records_page(
            category=category,
            effectiveness=eff_id,
            issue_department=dept_id,
            issue_date=year_id,
            timeliness=timeliness_id,
            page_index=0,
            page_size=PAGE_SIZE,
            order_by_index=order_idx,
        )
        total, rows0 = parse_record_search_html(html0, province_name=province_name, issue_year_hint=year_id)
        collected.extend(rows0)

        if total > PAGE_SIZE and len(rows0) == PAGE_SIZE:
            html1 = client.search_records_page(
                category=category,
                effectiveness=eff_id,
                issue_department=dept_id,
                issue_date=year_id,
                timeliness=timeliness_id,
                page_index=1,
                page_size=PAGE_SIZE,
                order_by_index=order_idx,
            )
            _, rows1 = parse_record_search_html(html1, province_name=province_name, issue_year_hint=year_id)
            collected.extend(rows1)

        if total <= GUEST_MAX_ITEMS:
            break

    return collected


def build_children_map(nodes: List[ClusterNode]) -> Dict[str, List[ClusterNode]]:
    children_map: Dict[str, List[ClusterNode]] = defaultdict(list)
    for n in nodes:
        children_map[n.pid].append(n)
    return children_map


def collect_dept_recursive(
    client: PkulawClient,
    children_map: Dict[str, List[ClusterNode]],
    dept_node: ClusterNode,
    category: str,
    eff_id: str,
    year_id: str,
    province_name: str,
) -> List[Dict[str, str]]:
    """当制定机关桶 > 200 条时，递归下钻至子级制定机关；若叶子仍 > 200，再按时效性拆分"""
    if dept_node.count <= 0:
        return []

    if dept_node.count <= GUEST_MAX_ITEMS:
        return fetch_bucket_records(
            client, category, eff_id, dept_node.id, year_id, "", province_name, dept_node.count
        )

    sub_nodes = [c for c in children_map.get(dept_node.id, []) if c.count > 0]
    if sub_nodes:
        rows: List[Dict[str, str]] = []
        for sub in sub_nodes:
            rows.extend(
                collect_dept_recursive(
                    client, children_map, sub, category, eff_id, year_id, province_name
                )
            )
        return rows

    t_nodes = client.get_cluster_tree(
        "TimelinessDic",
        category=category,
        effectiveness=eff_id,
        issue_department=dept_node.id,
        issue_date=year_id,
    )
    if t_nodes:
        rows = []
        for tn in t_nodes:
            if tn.count > 0:
                rows.extend(
                    fetch_bucket_records(
                        client, category, eff_id, dept_node.id, year_id, tn.id, province_name, tn.count
                    )
                )
        return rows

    return fetch_bucket_records(
        client, category, eff_id, dept_node.id, year_id, "", province_name, dept_node.count
    )


def collect_chl_eff_bucket(
    client: PkulawClient,
    eff_children_map: Dict[str, List[ClusterNode]],
    eff_node: ClusterNode,
    category: str,
    year_id: str,
) -> List[Dict[str, str]]:
    """中央法规：按 (年份 × 效力位阶) 采集，超 200 条时自动下钻子效力位阶 / 制定机关 / 时效性"""
    if eff_node.count <= 0:
        return []

    if eff_node.count <= GUEST_MAX_ITEMS:
        return fetch_bucket_records(
            client, category, eff_node.id, "", year_id, "", "", eff_node.count
        )

    # 先尝试下钻子级效力位阶（如 XC02 -> XC0201 / XC0202 / XC0203）
    sub_effs = [c for c in eff_children_map.get(eff_node.id, []) if c.count > 0]
    if sub_effs:
        rows: List[Dict[str, str]] = []
        for se in sub_effs:
            rows.extend(collect_chl_eff_bucket(client, eff_children_map, se, category, year_id))
        return rows

    # 叶子效力位阶仍 > 200：下钻制定机关 (IssueDepartment)
    dept_nodes = client.get_cluster_tree(
        "IssueDepartment",
        category=category,
        effectiveness=eff_node.id,
        issue_date=year_id,
    )
    if dept_nodes:
        dept_children_map = build_children_map(dept_nodes)
        top_depts = [d for d in dept_nodes if d.pid == "-1" and d.count > 0]
        if top_depts:
            rows = []
            for d in top_depts:
                rows.extend(
                    collect_dept_recursive(client, dept_children_map, d, category, eff_node.id, year_id, "")
                )
            return rows

    return fetch_bucket_records(
        client, category, eff_node.id, "", year_id, "", "", eff_node.count
    )


def run_single_library(lib: str, args):
    cfg = LIB_CONFIG[lib]
    ckpt = CrawlCheckpoint(OUTPUT_DIR, library=lib)
    if args.force:
        ckpt.reset()
        print(f"🔄 [{cfg['label']}] 已清空历史断点，将全量重新采集。")

    csv_path = OUTPUT_DIR / cfg["csv_name"]
    xlsx_path = OUTPUT_DIR / cfg["xlsx_name"]

    if args.export_only:
        all_rows = deduplicate_records(ckpt.load_all_records())
        export_csv(all_rows, csv_path)
        export_xlsx(all_rows, xlsx_path, sheet_title=cfg["sheet"])
        print(f"✅ [{cfg['label']}] 仅导出完成：去重后共 {len(all_rows)} 条 -> {csv_path.name} / {xlsx_path.name}")
        return

    pool = None
    if args.proxy or args.use_free_proxy or args.proxy_api or args.proxy_file:
        pool = ProxyPool(
            tunnel_proxy=args.proxy,
            proxy_api_url=args.proxy_api,
            proxy_file=args.proxy_file,
            use_github_feeds=args.use_free_proxy,
        )

    # 接入代理池时自动关闭单IP每40次休眠35s的限制
    batch_pause = 0 if pool is not None else 40
    client = PkulawClient(
        library=lib,
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        batch_pause_every=batch_pause,
        proxy_pool=pool,
    )
    print(f"\n🌐 正在初始化北大法宝 [{cfg['label']} ({lib})] 会话...")
    client.warmup()

    year_nodes = client.get_cluster_tree("IssueDate", category=args.category)
    target_years = {y.strip() for y in args.years.split(",") if y.strip()} if args.years else None
    if target_years:
        year_nodes = [y for y in year_nodes if y.id in target_years or y.name in target_years]

    target_effs = {e.strip() for e in args.eff.split(",") if e.strip()} if args.eff else None
    target_provs = {p.strip() for p in args.provinces.split(",") if p.strip()} if args.provinces else None

    print(f"📅 [{cfg['label']}] 待遍历发布年份共 {len(year_nodes)} 个 | 并发线程数: {args.workers}")

    t_start = time.time()
    new_count = 0
    count_lock = threading.Lock()
    tls = threading.local()

    def get_worker_client() -> PkulawClient:
        c = getattr(tls, "client", None)
        if c is None:
            c = PkulawClient(
                library=lib,
                min_delay=args.min_delay,
                max_delay=args.max_delay,
                batch_pause_every=batch_pause,
                proxy_pool=pool,
            )
            c.warmup()
            tls.client = c
        return c

    for y_idx, y_node in enumerate(year_nodes, 1):
        if y_node.count <= 0:
            continue

        eff_nodes = client.get_cluster_tree("EffectivenessDic", category=args.category, issue_date=y_node.id)
        eff_children_map = build_children_map(eff_nodes)
        top_effs = [e for e in eff_nodes if e.pid == "-1" and e.count > 0]
        if target_effs:
            top_effs = [e for e in top_effs if e.id in target_effs or e.name in target_effs]

        print(f"\n📆 [{cfg['label']} {y_idx}/{len(year_nodes)}] 年份 {y_node.id} (共 {y_node.count} 篇, {len(top_effs)} 类效力位阶)")

        if lib == "chl":
            pending_chl = [e for e in top_effs if not ckpt.is_done(f"chl|{y_node.id}|{e.id}")]
            def _run_chl(e_node: ClusterNode):
                nonlocal new_count
                wc = get_worker_client() if args.workers > 1 else client
                rows = collect_chl_eff_bucket(wc, eff_children_map, e_node, args.category, y_node.id)
                ckpt.append_records(rows)
                ckpt.mark_done(f"chl|{y_node.id}|{e_node.id}")
                with count_lock:
                    new_count += len(rows)
                    cur_tot = new_count
                print(
                    f"  ✅ {y_node.id} | {e_node.name}: "
                    f"预计 {e_node.count} 篇 -> 实采 {len(rows)} 条 (累计新增 {cur_tot} 条)",
                    flush=True,
                )

            if args.workers > 1 and len(pending_chl) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                    list(ex.map(_run_chl, pending_chl))
            else:
                for e_node in pending_chl:
                    _run_chl(e_node)
        else:
            tasks_lar = []
            for e_node in top_effs:
                dept_nodes = client.get_cluster_tree(
                    "IssueDepartment",
                    category=args.category,
                    effectiveness=e_node.id,
                    issue_date=y_node.id,
                )
                dept_children_map = build_children_map(dept_nodes)
                provinces = [d for d in dept_nodes if d.pid == "-1" and d.count > 0]
                if target_provs:
                    provinces = [p for p in provinces if p.name in target_provs or p.id in target_provs]

                for p_node in provinces:
                    bucket_key = f"lar|{y_node.id}|{e_node.id}|{p_node.id}"
                    if not ckpt.is_done(bucket_key):
                        tasks_lar.append((e_node, dept_children_map, p_node, bucket_key))

            def _run_lar(item):
                nonlocal new_count
                e_node, dept_children_map, p_node, bucket_key = item
                wc = get_worker_client() if args.workers > 1 else client
                rows = collect_dept_recursive(
                    client=wc,
                    children_map=dept_children_map,
                    dept_node=p_node,
                    category=args.category,
                    eff_id=e_node.id,
                    year_id=y_node.id,
                    province_name=p_node.name,
                )
                ckpt.append_records(rows)
                ckpt.mark_done(bucket_key)
                with count_lock:
                    new_count += len(rows)
                    cur_tot = new_count
                print(
                    f"  ✅ {y_node.id} | {e_node.name} | {p_node.name}: "
                    f"预计 {p_node.count} 篇 -> 实采 {len(rows)} 条 (累计新增 {cur_tot} 条)",
                    flush=True,
                )

            if args.workers > 1 and len(tasks_lar) > 1:
                with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
                    futs = [ex.submit(_run_lar, t) for t in tasks_lar]
                    for fut in concurrent.futures.as_completed(futs):
                        fut.result()
            else:
                for t in tasks_lar:
                    _run_lar(t)

    all_rows = deduplicate_records(ckpt.load_all_records())
    export_csv(all_rows, csv_path)
    export_xlsx(all_rows, xlsx_path, sheet_title=cfg["sheet"])

    elapsed = time.time() - t_start
    print("\n" + "=" * 68)
    print(f"🎉 [{cfg['label']}] 采集与导出完成！耗时 {elapsed:.1f}s")
    print(f"   • 去重后总记录数: {len(all_rows)} 条")
    print(f"   • CSV 文件 : {csv_path}")
    print(f"   • XLSX 文件: {xlsx_path}")
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser(description="北大法宝 - 中央法规 & 地方法规（环境保护）双库批量采集工具")
    ap.add_argument(
        "--library",
        choices=["all", "chl", "lar"],
        default="all",
        help="采集库：chl=中央法规, lar=地方法规, all=两套全采并分别导出（默认 all）",
    )
    ap.add_argument("--category", default="089", help="法规类别编码，默认 089 (环境保护)")
    ap.add_argument("--years", default="", help="指定年份，逗号分隔，例如 2026 或 2025,2026（默认全部年份）")
    ap.add_argument("--provinces", default="", help="仅对地方法规生效：指定省份，逗号分隔，例如 北京市,浙江省")
    ap.add_argument("--eff", default="", help="指定效力位阶编码或名称，逗号分隔（默认全部）")
    ap.add_argument("--min-delay", type=float, default=3.0, help="每次请求最小间隔秒数（接付费代理可设 0.05）")
    ap.add_argument("--max-delay", type=float, default=5.0, help="每次请求最大间隔秒数（接付费代理可设 0.15）")
    ap.add_argument("--workers", type=int, default=1, help="并发线程数（接付费轮换隧道代理可设 8~12，提速 10 倍）")
    ap.add_argument("--proxy", default="", help="接入商业隧道代理 (如 http://user:pass@tunnel.example.com:9000)")
    ap.add_argument("--proxy-api", default="", help="接入短效代理提取 API 或 jhao104/proxy_pool (如 http://...)")
    ap.add_argument("--proxy-file", default="", help="接入自定义代理列表文件 (每行 ip:port 或 http://user:pass@ip:port)")
    ap.add_argument("--use-free-proxy", action="store_true", help="接入 GitHub 免费代理池 (proxifly/monosans)")
    ap.add_argument("--force", action="store_true", help="清空断点从头全量重采")
    ap.add_argument("--export-only", action="store_true", help="仅从本地断点重新去重导出 CSV/XLSX")
    args = ap.parse_args()

    libs = ["chl", "lar"] if args.library == "all" else [args.library]
    try:
        for lib in libs:
            run_single_library(lib, args)
    except RateLimitBlockedError as exc:
        print(f"\n⚠️ {exc}")
        sys.exit(2)
    except KeyboardInterrupt:
        print("\n⏹️ 用户中断！已采集记录与断点均已实时保存，下次运行将自动续采。")
        sys.exit(130)


if __name__ == "__main__":
    main()
