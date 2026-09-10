"""网页抓取：requests + BeautifulSoup 提取正文，常用于抓取 JD 页面。
SSRF 防护：拒绝内网/本机地址（部署场景下防止探测内部服务）。"""
import ipaddress
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from tools.base import Tool, ToolError
from tools.web_search import UA


def _assert_public(url: str):
    """SSRF 防护：本机/环回/内网地址一律拒绝。"""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise ToolError(f"url 不合法: {url}")
    if host in ("localhost", "0.0.0.0") or host.endswith(".local"):
        raise ToolError(f"禁止访问内网地址: {host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ToolError(f"域名无法解析: {host}")
    for info in infos:
        ip = info[4][0]
        # 改动：ip_address() 对畸形/带 zone 的地址会抛 ValueError，原实现未捕获，
        # 会以裸 ValueError 逃出工具层（只在注册表兜底处被当作未知异常重试）；
        # 这里解析失败一律按「不可信地址」拒绝，并补齐 loopback/link-local/
        # multicast/unspecified 判断（is_private 未覆盖全部场景）。
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            raise ToolError(f"目标地址无法解析，已拒绝: {host}")
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_unspecified or addr.is_reserved):
            raise ToolError(f"禁止访问内网地址: {host}")


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "抓取公开网页正文（用于读取 JD 链接、公司介绍页）。"
    args_desc = {"url": "网页地址", "max_chars": "正文最大长度，默认6000"}
    cost = "低"
    avg_seconds = 1.2

    def run(self, url: str = "", max_chars: int = 6000) -> dict:
        if not url or not url.startswith(("http://", "https://")):
            raise ToolError(f"url 不合法: {url!r}")
        _assert_public(url)
        try:
            resp = requests.get(url, headers=UA, timeout=15, allow_redirects=False)
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
