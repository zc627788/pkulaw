# -*- coding: utf-8 -*-
"""
北大法宝 (pkulaw.com) 纯协议 HTTP 客户端

逆向协议链路：
  1. GET  /law/lar                          -> 初始化会话 Cookie (pkulaw_v6_sessionid)
  2. POST /Aggregate/SingleClassResult      -> 获取左侧多维聚类树 (Category / EffectivenessDic / IssueDepartment / IssueDate / TimelinessDic)
  3. POST /law/search/RecordSearch          -> 获取平铺列表 HTML (ShowType=Default, 最大 PageSize=100, 访客态单查询上限 200 条)
"""

import json
import random
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from curl_cffi import requests as cffi_requests

from proxy_pool import ProxyPool

BASE_URL = "https://www.pkulaw.com"


class RateLimitBlockedError(RuntimeError):
    """触发北大法宝反爬频控人机校验 (VerificationCode/Index?IsBlack=true) 时抛出"""


@dataclass
class ClusterNode:
    id: str
    pid: str
    name: str
    count: int


def parse_cluster_name(raw_name: str) -> tuple[str, int]:
    """将 '北京市 (3029)' 解析为 ('北京市', 3029)"""
    m = re.match(r"^(.*?)\s*\((\d+)\)\s*$", (raw_name or "").strip())
    if m:
        return m.group(1).strip(), int(m.group(2))
    return (raw_name or "").strip(), 0


class PkulawClient:
    def __init__(
        self,
        library: str = "lar",
        min_delay: float = 3.0,
        max_delay: float = 5.0,
        batch_pause_every: int = 40,
        batch_pause_sec: float = 35.0,
        timeout: int = 25,
        proxy_pool: Optional[ProxyPool] = None,
    ):
        self.library = library
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.batch_pause_every = batch_pause_every
        self.batch_pause_sec = batch_pause_sec
        self.timeout = timeout
        self.proxy_pool = proxy_pool
        self.current_proxy: Optional[str] = None
        self._req_count = 0
        self.session: Optional[cffi_requests.Session] = None
        self._init_session()

    def _init_session(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
        self.session = cffi_requests.Session(impersonate="chrome120")
        if self.proxy_pool is not None:
            self.current_proxy = self.proxy_pool.get_proxy()
            if self.current_proxy:
                self.session.proxies = {"http": self.current_proxy, "https": self.current_proxy}
        self.session.headers.update({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/law/{self.library}",
        })

    def warmup(self):
        """访问对应法规库入口页以激活会话 Cookie"""
        for attempt in range(1, 4):
            try:
                resp = self.session.get(f"{BASE_URL}/law/{self.library}", timeout=self.timeout)
                if resp.status_code == 200:
                    return
            except Exception:
                if attempt == 3:
                    raise
            self._init_session()
            time.sleep(1.0 * attempt)

    def _polite_sleep(self):
        self._req_count += 1
        if self.batch_pause_every > 0 and self._req_count > 1 and (self._req_count % self.batch_pause_every == 0):
            print(f"    ⏳ [防频控滑动窗口] 已连续请求 {self._req_count} 次，自动休眠 {self.batch_pause_sec:.0f}s...", flush=True)
            time.sleep(self.batch_pause_sec)
        elif self.max_delay > 0:
            time.sleep(random.uniform(self.min_delay, self.max_delay))

    @staticmethod
    def _build_aggs(
        category: str = "089",
        effectiveness: str = "",
        issue_department: str = "",
        issue_date: str = "",
        timeliness: str = "",
    ) -> dict:
        return {
            "RelatedPrompted": "",
            "EffectivenessDic": effectiveness,
            "SpecialType": "",
            "IssueDepartment": issue_department,
            "TimelinessDic": timeliness,
            "Category": category,
            "IssueDate": issue_date,
        }

    def get_cluster_tree(
        self,
        class_name: str,
        category: str = "089",
        effectiveness: str = "",
        issue_department: str = "",
        issue_date: str = "",
        timeliness: str = "",
        max_retries: int = 3,
    ) -> List[ClusterNode]:
        """请求 /Aggregate/SingleClassResult 返回聚类节点列表"""
        aggs = self._build_aggs(
            category=category,
            effectiveness=effectiveness,
            issue_department=issue_department,
            issue_date=issue_date,
            timeliness=timeliness,
        )
        payload = {
            "library": self.library,
            "className": class_name,
            "Aggs": json.dumps(aggs, ensure_ascii=False),
            "QueryBase64Request": "",
            "keyword": "",
            "advDic": "",
            "SearchInResult": "",
            "ClassFlag": self.library,
            "ExtCondition": "",
            "KeywordType": "Title",
            "MatchType": "Exact",
        }
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE_URL}/law/{self.library}"}

        last_exc = None
        for attempt in range(1, max_retries + 1):
            self._polite_sleep()
            try:
                resp = self.session.post(
                    f"{BASE_URL}/Aggregate/SingleClassResult",
                    data=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                cleaned = re.sub(r"[★☆▲]", "", resp.text)
                raw_nodes = json.loads(cleaned)
                nodes: List[ClusterNode] = []
                for n in raw_nodes:
                    name, cnt = parse_cluster_name(n.get("name", ""))
                    nodes.append(
                        ClusterNode(
                            id=str(n.get("id", "")),
                            pid=str(n.get("pId", "-1")),
                            name=name,
                            count=cnt,
                        )
                    )
                return nodes
            except Exception as exc:
                last_exc = exc
                if attempt < max_retries:
                    self._init_session()
                    self.warmup()
                    time.sleep(1.2 * attempt)
        raise RuntimeError(f"聚类树请求失败 ({class_name}): {last_exc}") from last_exc

    def search_records_page(
        self,
        category: str = "089",
        effectiveness: str = "",
        issue_department: str = "",
        issue_date: str = "",
        timeliness: str = "",
        keywords: str = "",
        match_type: str = "Exact",
        page_index: int = 0,
        page_size: int = 100,
        order_by_index: int = 0,
        max_retries: int = 3,
    ) -> str:
        """请求 /law/search/RecordSearch 返回单页结果 HTML"""
        payload = {
            "Menu": "law",
            "Keywords": keywords,
            "SearchKeywordType": "Title",
            "MatchType": match_type,
            "RangeType": "Piece",
            "Library": self.library,
            "ClassFlag": self.library,
            "GroupLibraries": "",
            "QueryOnClick": "False",
            "AfterSearch": "False",
            "ComplexSearch": "False",
            "PreviousLib": self.library,
            "IsSynonymSearch": "false",
            "IsSearchProvision": "False",
            "IsCustomSortSearch": "False",
            "IsAdv": "False",
            "ClassCodeKey": "",
            "Aggs.RelatedPrompted": "",
            "Aggs.EffectivenessDic": effectiveness,
            "Aggs.SpecialType": "",
            "Aggs.IssueDepartment": issue_department,
            "Aggs.TimelinessDic": timeliness,
            "Aggs.Category": category,
            "Aggs.IssueDate": issue_date,
            "GroupByIndex": "0",
            "OrderByIndex": str(order_by_index),
            "ShowType": "Default",
            "GroupValue": "",
            "RecordShowType": "List",
            "Pager.PageIndex": str(page_index),
            "Pager.PageSize": str(page_size),
        }
        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE_URL}/law/{self.library}"}

        last_exc = None
        for attempt in range(1, max_retries + 1):
            self._polite_sleep()
            try:
                resp = self.session.post(
                    f"{BASE_URL}/law/search/RecordSearch",
                    data=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                if "VerificationCode/Index" in resp.text or "IsBlack=true" in resp.text:
                    if self.proxy_pool is not None and self.current_proxy:
                        self.proxy_pool.mark_bad(self.current_proxy)
                        self._init_session()
                        self.warmup()
                        continue
                    raise RateLimitBlockedError(
                        "触发北大法宝IP频控人机校验，请在浏览器打开 https://www.pkulaw.com/law/chl 完成验证后继续运行（断点已保留）。"
                    )
                if resp.status_code != 200 or len(resp.text) < 200:
                    raise RuntimeError(f"异常响应 status={resp.status_code}, len={len(resp.text)}")
                return resp.text
            except RateLimitBlockedError:
                raise
            except Exception as exc:
                last_exc = exc
                if self.proxy_pool is not None and self.current_proxy:
                    self.proxy_pool.mark_bad(self.current_proxy)
                if attempt < max_retries:
                    self._init_session()
                    self.warmup()
                    time.sleep(1.5 * attempt)
        raise RuntimeError(f"列表页请求失败 (page={page_index}): {last_exc}") from last_exc
