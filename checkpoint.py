# -*- coding: utf-8 -*-
"""
断点续采与实时流式落盘管理器
"""

import json
import threading
from pathlib import Path
from typing import Dict, List, Set


class CrawlCheckpoint:
    def __init__(self, output_dir: Path, library: str = "lar"):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.ckpt_file = self.output_dir / f"checkpoint_{library}.json"
        self.jsonl_file = self.output_dir / f"records_{library}.jsonl"
        self._lock = threading.Lock()
        self.done_buckets: Set[str] = self._load_done_buckets()

    def _load_done_buckets(self) -> Set[str]:
        if self.ckpt_file.exists():
            try:
                data = json.loads(self.ckpt_file.read_text(encoding="utf-8"))
                return set(data.get("done_buckets", []))
            except Exception:
                pass
        return set()

    def is_done(self, bucket_key: str) -> bool:
        with self._lock:
            return bucket_key in self.done_buckets

    def append_records(self, rows: List[Dict[str, str]]):
        if not rows:
            return
        with self._lock:
            with open(self.jsonl_file, "a", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def mark_done(self, bucket_key: str):
        with self._lock:
            self.done_buckets.add(bucket_key)
            self.ckpt_file.write_text(
                json.dumps({"done_buckets": sorted(self.done_buckets)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def reset(self):
        self.done_buckets.clear()
        for f in (self.ckpt_file, self.jsonl_file):
            if f.exists():
                f.unlink()

    def load_all_records(self) -> List[Dict[str, str]]:
        if not self.jsonl_file.exists():
            return []
        records: List[Dict[str, str]] = []
        with open(self.jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records
