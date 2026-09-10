"""联网检索：可配置检索端点（可选）→ 免密钥公开检索通道兜底，全部失败时抛可读错误供 agent 降级。

设计说明：检索端点完全由环境变量提供（SEARCH_API_URL / SEARCH_API_KEY），
代码内不写死任何第三方服务地址，换供应商只需改环境变量。
"""
import os

import requests

from core.tools.base import Tool, ToolError

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

        # 1) 已配置的检索端点（SEARCH_API_URL + SEARCH_API_KEY 同时存在时启用）
        endpoint = (os.getenv("SEARCH_API_URL") or "").strip()
        api_key = (os.getenv("SEARCH_API_KEY") or "").strip()
        if endpoint and api_key:
            try:
                resp = requests.post(endpoint,
                                     json={"api_key": api_key, "query": query,
                                           "max_results": max_results},
                                     timeout=12, headers=UA)
                resp.raise_for_status()
                payload = resp.json()
                raw = payload.get("results") or payload.get("data") or []
                items = [{"title": r.get("title", ""), "url": r.get("url", ""),
                          "snippet": str(r.get("content") or r.get("snippet") or "")[:300]}
                         for r in raw]
                if items:
                    return {"engine": "primary", "results": items}
            except Exception as e:
                errors.append(f"primary: {e}")

        # 2) 免密钥公开检索通道兜底（python 包 ddgs，无需任何凭据）
        try:
            from ddgs import DDGS
            with DDGS() as dd:
                raw = list(dd.text(query, max_results=max_results))
            items = [{"title": r.get("title") or r.get("name") or "",
                      "url": r.get("href") or r.get("url") or "",
                      "snippet": (r.get("body") or r.get("snippet") or "")[:300]}
                     for r in raw]
            if items:
                return {"engine": "fallback", "results": items}
            errors.append("fallback: 无结果")
        except Exception as e:
            errors.append(f"fallback: {e}")

        raise ToolError("检索通道均不可用（" + "; ".join(errors) +
                        "）。建议：检查网络/代理，或直接提供 JD 文本继续分析。")
