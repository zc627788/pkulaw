# -*- coding: utf-8 -*-
"""
多源代理池模块（支持商业隧道代理 / 短效提取 API / 代理文件 / GitHub 实时更新源）

支持接入方式：
  1. 隧道代理 (--proxy http://user:pass@host:port): 固定入口、服务端自动换IP，异常时自动重建连接换出口
  2. 提取 API (--proxy-api "https://..."): 自动兼容纯文本 ip:port 列表与常见 JSON 返回格式
  3. 代理文件 (--proxy-file proxies.txt): 每行一个代理地址，自动测活并轮换
  4. GitHub 公开源 (--use-free-proxy): proxifly / monosans 自动并发测活轮换
"""

import concurrent.futures
import json
import random
import re
import threading
import time
from pathlib import Path
from typing import List, Optional

from curl_cffi import requests as cffi_requests

GITHUB_PROXY_FEEDS = [
    ("https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/countries/CN/data.txt", "http"),
    ("https://cdn.jsdelivr.net/gh/monosans/proxy-list@main/proxies/http.txt", "http"),
    ("https://cdn.jsdelivr.net/gh/monosans/proxy-list@main/proxies/socks5.txt", "socks5"),
    ("https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/socks5/data.txt", "socks5"),
]


class ProxyPool:
    def __init__(
        self,
        tunnel_proxy: str = "",
        proxy_api_url: str = "",
        proxy_file: str = "",
        use_github_feeds: bool = False,
        max_probe: int = 400,
    ):
        self.tunnel_proxy = self._normalize_line(tunnel_proxy) if tunnel_proxy.strip() else None
        self.proxy_api_url = proxy_api_url.strip()
        self.proxy_file = proxy_file.strip()
        self.use_github_feeds = use_github_feeds
        self.max_probe = max_probe
        self._lock = threading.Lock()
        self._alive: List[str] = []
        self._idx = 0
        self._last_api_fetch = 0.0

    @staticmethod
    def _normalize_line(line: str, default_proto: str = "http") -> Optional[str]:
        s = line.strip()
        if not s or s.startswith("#"):
            return None
        if "://" not in s:
            s = f"{default_proto}://{s}"
        return s

    def _parse_api_response(self, text: str) -> List[str]:
        """兼容国内常见短效代理 API 的纯文本与 JSON 返回格式"""
        text = (text or "").strip()
        if not text:
            return []

        results: List[str] = []
        if text.startswith("{") or text.startswith("["):
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    if obj.get("proxy"):
                        norm = self._normalize_line(str(obj["proxy"]))
                        if norm:
                            results.append(norm)
                    data = obj.get("data")
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and item.get("ip") and item.get("port"):
                                results.append(f"http://{item['ip']}:{item['port']}")
                            elif isinstance(item, str):
                                norm = self._normalize_line(item)
                                if norm:
                                    results.append(norm)
                    elif isinstance(data, dict):
                        for k in ("proxy_list", "proxies", "list"):
                            if isinstance(data.get(k), list):
                                for item in data[k]:
                                    norm = self._normalize_line(str(item))
                                    if norm:
                                        results.append(norm)
                elif isinstance(obj, list):
                    for item in obj:
                        norm = self._normalize_line(str(item))
                        if norm:
                            results.append(norm)
                if results:
                    return list(dict.fromkeys(results))
            except Exception:
                pass

        # 纯文本 ip:port（或 user:pass@ip:port）逐行解析
        for m in re.finditer(r"(?:(?:https?|socks5h?)://)?(?:[^\s:@]+:[^\s:@]+@)?\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}", text):
            norm = self._normalize_line(m.group(0))
            if norm:
                results.append(norm)
        return list(dict.fromkeys(results))

    def _fetch_from_api(self) -> List[str]:
        if not self.proxy_api_url:
            return []
        # 避免短效 API 提取过于频繁（至少间隔 1.5s）
        now = time.time()
        if now - self._last_api_fetch < 1.5:
            time.sleep(1.5 - (now - self._last_api_fetch))
        self._last_api_fetch = time.time()
        try:
            r = cffi_requests.get(self.proxy_api_url, timeout=10)
            if r.status_code == 200:
                parsed = self._parse_api_response(r.text)
                if parsed:
                    print(f"🌐 [代理API] 成功提取 {len(parsed)} 个短效代理节点", flush=True)
                return parsed
        except Exception as exc:
            print(f"⚠️ [代理API] 提取失败: {exc}", flush=True)
        return []

    def _fetch_candidates(self) -> List[str]:
        candidates: List[str] = []

        if self.proxy_file:
            p = Path(self.proxy_file)
            if p.exists():
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    norm = self._normalize_line(line)
                    if norm:
                        candidates.append(norm)

        if self.use_github_feeds:
            for url, proto in GITHUB_PROXY_FEEDS:
                try:
                    r = cffi_requests.get(url, timeout=12)
                    if r.status_code == 200:
                        lines = r.text.splitlines()
                        random.shuffle(lines)
                        for line in lines[:200]:
                            norm = self._normalize_line(line, proto)
                            if norm:
                                candidates.append(norm)
                except Exception:
                    continue

        return list(dict.fromkeys(candidates))[: self.max_probe]

    @staticmethod
    def _probe_one(proxy_url: str) -> Optional[str]:
        try:
            r = cffi_requests.get(
                "https://www.pkulaw.com/law/chl",
                proxies={"http": proxy_url, "https": proxy_url},
                impersonate="chrome120",
                timeout=6,
            )
            if r.status_code == 200 and "pkulaw" in r.text and "IsBlack=true" not in r.text:
                return proxy_url
        except Exception:
            pass
        return None

    def refresh(self) -> int:
        """刷新代理池（API 提取直接入池，文件/免费池则并发测活）"""
        if self.proxy_api_url:
            api_nodes = self._fetch_from_api()
            if api_nodes:
                with self._lock:
                    self._alive = api_nodes
                    self._idx = 0
                return len(api_nodes)

        candidates = self._fetch_candidates()
        if not candidates:
            return 0
        print(f"🔎 [代理池] 正在并发测活 {len(candidates)} 个候选节点 (目标: pkulaw.com)...", flush=True)
        alive: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=45) as ex:
            for res in ex.map(self._probe_one, candidates):
                if res:
                    alive.append(res)
        with self._lock:
            self._alive = alive
            self._idx = 0
        print(f"✅ [代理池] 测活完成：可用健康节点 {len(alive)} 个", flush=True)
        return len(alive)

    def get_proxy(self) -> Optional[str]:
        if self.tunnel_proxy:
            return self.tunnel_proxy

        with self._lock:
            if self._alive:
                p = self._alive[self._idx % len(self._alive)]
                self._idx += 1
                return p

        if self.refresh() > 0:
            with self._lock:
                p = self._alive[0]
                self._idx = 1
                return p
        return None

    def mark_bad(self, proxy_url: Optional[str]):
        """剔除失效代理（隧道代理为固定入口，不剔除，由上层重建连接换出口IP）"""
        if not proxy_url or self.tunnel_proxy:
            return
        with self._lock:
            if proxy_url in self._alive:
                self._alive.remove(proxy_url)
                print(f"🗑️ [代理池] 已剔除失效/受限节点 {proxy_url}（剩余 {len(self._alive)} 个）", flush=True)
