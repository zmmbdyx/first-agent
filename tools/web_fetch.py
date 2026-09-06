"""网页抓取：requests + BeautifulSoup 提取正文，常用于抓取 JD 页面。"""
import requests
from bs4 import BeautifulSoup

from tools.base import Tool, ToolError
from tools.web_search import UA


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "抓取网页并提取正文文本。用于读取 JD 链接、公司介绍页等。"
    args_desc = {"url": "网页地址", "max_chars": "正文最大长度，默认6000"}
    cost = "低"
    avg_seconds = 1.2

    def run(self, url: str = "", max_chars: int = 6000) -> dict:
        if not url or not url.startswith(("http://", "https://")):
            raise ToolError(f"url 不合法: {url!r}")
        try:
            resp = requests.get(url, headers=UA, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            raise ToolError(f"请求失败: {type(e).__name__}: {e}")
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines()
                         if line.strip())
        if len(text) < 50:
            raise ToolError("页面正文过短，可能是动态渲染页或反爬拦截")
        return {"url": url, "title": soup.title.string.strip() if soup.title else "",
                "text": text[: int(max_chars or 6000)]}
