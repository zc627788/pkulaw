# -*- coding: utf-8 -*-
import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from pkulaw_client import PkulawClient
from proxy_pool import ProxyPool

parser = argparse.ArgumentParser(description="比对各省份官方聚合数与本地爬取数据")
parser.add_argument("--proxy", default=os.getenv("PKULAW_PROXY", ""), help="代理地址 (如 http://user:pass@host:port)")
args = parser.parse_args()

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

prov_raw = Counter()
prov_uniq = defaultdict(set)
lar_file = OUTPUT_DIR / "records_lar.jsonl"
if lar_file.exists():
    with open(lar_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            p = r.get("省份", "")
            gid = r.get("法宝ID", "")
            prov_raw[p] += 1
            if gid:
                prov_uniq[p].add(gid)
else:
    print(f"⚠️ 未找到 {lar_file}")

pool = ProxyPool(tunnel_proxy=args.proxy) if args.proxy else None
c = PkulawClient(library="lar", min_delay=0.1, max_delay=0.2, batch_pause_every=0, proxy_pool=pool)
c.warmup()
depts = c.get_cluster_tree("IssueDepartment", category="089")
provs = [d for d in depts if d.pid == "-1"]

print(f"Total official provinces: {len(provs)}")
print(f"{'省份':<18} {'官网总数':>8} {'省内去重':>8} {'差额':>8} {'原始条数':>8}")
print("-" * 58)
tot_off = tot_uniq = tot_raw = 0
for p in sorted(provs, key=lambda x: x.count, reverse=True):
    u = len(prov_uniq[p.name])
    rw = prov_raw[p.name]
    diff = p.count - u
    tot_off += p.count
    tot_uniq += u
    tot_raw += rw
    print(f"{p.name:<18} {p.count:>8} {u:>8} {diff:>8} {rw:>8}")
print("-" * 58)
print(f"{'合计':<18} {tot_off:>8} {tot_uniq:>8} {tot_off - tot_uniq:>8} {tot_raw:>8}")
