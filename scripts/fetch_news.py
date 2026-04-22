"""
消息面采集(离线模式)。

两种源:
    1. akshare 东财个股新闻:ak.stock_news_em(symbol=code) —— 默认,无需配置。
    2. SearXNG 自建实例:需要环境变量 SEARXNG_BASE_URL,format=json。

输出 data/YYYY-MM-DD/<code>_news.json。

Claude 在对话里也可以直接用 web_search,这里是为"纯离线 / cron 无 Claude 环境"备份。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any

import yaml


def fetch_news_akshare(code: str, limit: int = 10) -> list[dict]:
    try:
        import akshare as ak
    except ImportError:
        print("ERROR: 需要 akshare。pip install akshare", file=sys.stderr)
        return []
    try:
        df = ak.stock_news_em(symbol=code)
        df = df.head(limit)
        out = []
        for _, r in df.iterrows():
            out.append({
                "title": str(r.get("新闻标题", "")),
                "summary": str(r.get("新闻内容", ""))[:300],
                "url": str(r.get("新闻链接", "")),
                "published": str(r.get("发布时间", "")),
                "source": str(r.get("文章来源", "东方财富")),
            })
        return out
    except Exception as e:
        print(f"WARN stock_news_em({code}): {e}", file=sys.stderr)
        return []


def fetch_news_searxng(query: str, limit: int = 5) -> list[dict]:
    base = os.environ.get("SEARXNG_BASE_URL", "").rstrip("/")
    if not base:
        return []
    try:
        import urllib.parse
        import urllib.request
    except ImportError:
        return []
    url = f"{base}/search?q={urllib.parse.quote(query)}&format=json&language=zh-CN"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "astock-daily/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        out = []
        for r in data.get("results", [])[:limit]:
            out.append({
                "title": r.get("title", ""),
                "summary": r.get("content", "")[:300],
                "url": r.get("url", ""),
                "published": r.get("publishedDate", ""),
                "source": r.get("engine", "searxng"),
            })
        return out
    except Exception as e:
        print(f"WARN searxng ({query}): {e}", file=sys.stderr)
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/stocks.yml")
    ap.add_argument("--output-dir", default="data")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--use-searxng", action="store_true",
                    help="额外使用 SearXNG 搜索,需设 SEARXNG_BASE_URL")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    today = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(args.output_dir, today)
    os.makedirs(out_dir, exist_ok=True)

    for s in cfg["watchlist"]:
        code = str(s["code"]).zfill(6)
        name = s.get("name", "")
        print(f"[news] {code} {name} ...", flush=True)

        news_list: list[dict[str, Any]] = []
        news_list.extend(fetch_news_akshare(code, limit=args.limit))
        if args.use_searxng:
            news_list.extend(fetch_news_searxng(f"{name} 最新", limit=5))

        # 去重(按 url)
        seen = set()
        dedup = []
        for n in news_list:
            u = n.get("url", "")
            if u and u in seen:
                continue
            seen.add(u)
            dedup.append(n)

        path = os.path.join(out_dir, f"{code}_news.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "code": code,
                "name": name,
                "fetched_at": datetime.now().isoformat(),
                "news": dedup,
            }, f, ensure_ascii=False, indent=2)

    print(f"[done] 消息面数据 -> {out_dir}")


if __name__ == "__main__":
    main()
