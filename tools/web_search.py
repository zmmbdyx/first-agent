"""联网搜索：Search-primary（可选）→ Search-fallback 兜底链，全部失败时抛出可读错误供 agent 降级。"""
import os

import requests

from tools.base import Tool, ToolError

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


class WebSearchTool(Tool):
    name = "web_search"
    description = "联网搜索，返回标题/链接/摘要列表。用于查行业信息、公司背景、面试经验等。"
    args_desc = {"query": "搜索关键词", "max_results": "返回条数，默认5"}
    cost = "中"
    avg_seconds = 2.5

    def run(self, query: str = "", max_results: int = 5) -> dict:
        if not query or not query.strip():
            raise ToolError("query 不能为空")
        max_results = max(1, min(int(max_results or 5), 10))
        errors = []

        # 1) Search-primary（配置了 SEARCH_API_KEY 时优先）
        search-primary_key = os.getenv("SEARCH_API_KEY")
        if search-primary_key:
            try:
                resp = requests.post("https://api.search-primary.com/search",
                                     json={"api_key": search-primary_key, "query": query,
                                           "max_results": max_results},
                                     timeout=12, headers=UA)
                resp.raise_for_status()
                items = [{"title": r.get("title", ""), "url": r.get("url", ""),
                          "snippet": r.get("content", "")[:300]}
                         for r in resp.json().get("results", [])]
                if items:
                    return {"engine": "search-primary", "results": items}
            except Exception as e:
                errors.append(f"search-primary: {e}")

        # 2) Search-fallback
        try:
            from ddgs import DDGS
            with DDGS() as dd:
                raw = list(dd.text(query, max_results=max_results))
            items = [{"title": r.get("title") or r.get("name") or "",
                      "url": r.get("href") or r.get("url") or "",
                      "snippet": (r.get("body") or r.get("snippet") or "")[:300]}
                     for r in raw]
            if items:
                return {"engine": "search-fallback", "results": items}
            errors.append("search-fallback: 无结果")
        except Exception as e:
            errors.append(f"search-fallback: {e}")

        raise ToolError("搜索源均不可用（" + "; ".join(errors) +
                        "）。建议：检查网络/代理，或直接提供 JD 文本继续分析。")
