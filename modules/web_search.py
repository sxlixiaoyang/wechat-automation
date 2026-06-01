"""
Web搜索工具 — 多引擎搜索 + 自适应页面提取
可独立使用，也可作为模块嵌入 AI 引擎
"""
import re
import time
import requests
from typing import Dict, Optional

# 从项目配置读取代理（安全降级）
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import PROXY_URL
except ImportError:
    PROXY_URL = "http://127.0.0.1:10808"


class WebSearchTool:
    """Web Search工具 — 多引擎搜索 + 自适应页面解析 + 结果缓存 + 重试"""

    SEARCH_CACHE_TTL = 120        # 搜索结果缓存 120 秒
    PAGE_CACHE_TTL = 300          # 页面内容缓存 300 秒
    MAX_RETRIES = 2               # 最大重试次数
    RETRY_BACKOFF = 1.5           # 重试退避倍率
    QUALITY_THRESHOLD = 40        # 内容质量最低分

    # ====== 自适应提取：策略注册表（类级别，跨实例共享） ======
    _domain_strategies: Dict[str, dict] = {}

    def __init__(self, proxy_url: str = None):
        proxy = proxy_url or PROXY_URL
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        })
        if self.proxies:
            self.session.proxies.update(self.proxies)
        self._search_cache: Dict[str, tuple] = {}
        self._page_cache: Dict[str, tuple] = {}

    # ==================================================================
    # HTTP 层
    # ==================================================================
    def _get(self, url: str, timeout: int = 15, extra_headers: dict = None) -> Optional[requests.Response]:
        """带重试的 GET，返回 Response 或 None"""
        headers = extra_headers or {}
        last_err = None
        for attempt in range(1 + self.MAX_RETRIES):
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout)
                return resp
            except requests.exceptions.Timeout:
                last_err = f"超时({timeout}s)"
            except requests.exceptions.ConnectionError as e:
                last_err = f"连接失败: {e}"
            except Exception as e:
                last_err = str(e)
            if attempt < self.MAX_RETRIES:
                wait = self.RETRY_BACKOFF ** (attempt + 1)
                time.sleep(wait)
        print(f"    [WebSearch] 请求失败(已重试{self.MAX_RETRIES}次): {last_err}")
        return None

    @staticmethod
    def _detect_encoding(response: requests.Response) -> str:
        """智能编码检测"""
        ct = response.headers.get("Content-Type", "")
        if "charset=" in ct:
            declared = ct.split("charset=")[-1].strip().lower()
            if declared in ("gbk", "gb2312", "gb18030"):
                return "gbk"
            if declared == "utf-8":
                return "utf-8"
        try:
            import chardet
            result = chardet.detect(response.content)
            if result and result.get("confidence", 0) > 0.7:
                enc = result["encoding"]
                if enc and enc.lower() in ("gb2312", "gb18030", "gbk"):
                    return "gbk"
                if enc and enc.lower() == "utf-8":
                    return "utf-8"
        except ImportError:
            pass
        raw = response.content
        if raw.startswith(b'\xc0\xef') or raw.startswith(b'\xca\xfd'):
            return "gbk"
        try:
            sample = raw[:4096]
            high_bytes = sum(1 for b in sample if 0x80 <= b <= 0xFE)
            if high_bytes > len(sample) * 0.25:
                return "gbk"
        except:
            pass
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "gbk"

    # ==================================================================
    # 公开：多引擎搜索
    # ==================================================================
    def search(self, query: str, num_results: int = 3) -> str:
        """执行 Web 搜索，DuckDuckGo 优先，Bing 备用"""
        now = time.time()
        cached = self._search_cache.get(query)
        if cached and now - cached[0] < self.SEARCH_CACHE_TTL:
            print(f"    [WebSearch] 命中缓存: {query}")
            return cached[1]

        result = self._search_duckduckgo(query, num_results)
        if not result or result.startswith("搜索「"):
            print(f"    [WebSearch] DuckDuckGo 无结果，切换到 Bing")
            result = self._search_bing(query, num_results)

        if not result:
            result = f"搜索「{query}」未获取到结果。请尝试换一个关键词或更简短的关键词。"

        self._search_cache[query] = (now, result)
        if len(self._search_cache) > 200:
            oldest = sorted(self._search_cache.items(), key=lambda x: x[1][0])[:50]
            for k, _ in oldest:
                del self._search_cache[k]
        return result

    def _search_duckduckgo(self, query: str, num: int) -> str:
        """DuckDuckGo HTML 搜索"""
        try:
            import urllib.parse
            from bs4 import BeautifulSoup as BS
            url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
            resp = self._get(url)
            if not resp or resp.status_code != 200:
                return ""
            soup = BS(resp.text, "html.parser")
            results = soup.find_all("div", class_="result__body")
            if not results:
                return ""
            lines = []
            for i, r in enumerate(results[:num], 1):
                title_e = r.find("a", class_="result__a")
                snip_e = r.find("a", class_="result__snippet")
                url_e = r.find("a", class_="result__url")
                title = title_e.get_text().strip() if title_e else ""
                snippet = snip_e.get_text().strip() if snip_e else ""
                link = url_e.get("href", "") if url_e else ""
                if link:
                    from urllib.parse import urlparse, parse_qs
                    p = urlparse(link)
                    link = parse_qs(p.query).get("uddg", [link])[0]
                if title:
                    lines.append(f"{i}. {title}\n   {snippet}\n   {link}")
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    def _search_bing(self, query: str, num: int) -> str:
        """Bing 搜索备用"""
        try:
            import urllib.parse
            from bs4 import BeautifulSoup as BS
            url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}&count={num}"
            resp = self._get(url)
            if not resp or resp.status_code != 200:
                return ""
            soup = BS(resp.text, "html.parser")
            items = soup.find_all("li", class_="b_algo")
            if not items:
                return ""
            lines = []
            for i, li in enumerate(items[:num], 1):
                a = li.find("a")
                title = a.get_text().strip() if a else ""
                link = a.get("href", "") if a else ""
                desc_p = li.find("p") or li.find("div", class_="b_caption")
                desc = desc_p.get_text().strip() if desc_p else ""
                if title:
                    lines.append(f"{i}. {title}\n   {desc[:120]}\n   {link}")
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    # ==================================================================
    # 公开：访问 URL（自适应提取）
    # ==================================================================
    def visit_url(self, url: str, query_hint: str = "") -> str:
        """访问指定 URL 并自适应提取页面正文"""
        cache_key = f"{url}||{query_hint[:40]}" if query_hint else url
        now = time.time()
        cached = self._page_cache.get(cache_key)
        if cached and now - cached[0] < self.PAGE_CACHE_TTL:
            print(f"    [WebSearch] 页面缓存命中: {url[:60]}")
            return cached[1]

        extra_headers = {}
        if any(d in url for d in ["sinajs.cn", "sina.com.cn", "eastmoney.com", "cngold.org"]):
            extra_headers["Referer"] = "https://finance.sina.com.cn"

        try:
            resp = self._get(url, timeout=15, extra_headers=extra_headers)
            if not resp:
                return "访问失败: 网络请求失败（已重试）"

            if resp.status_code != 200:
                hints = {403: "(可能被屏蔽)", 404: "(页面不存在)", 429: "(请求过于频繁，稍后重试)",
                         500: "(服务器错误)", 502: "(网关错误)", 503: "(服务暂不可用)"}
                hint = hints.get(resp.status_code, "")
                return f"访问失败: HTTP {resp.status_code} {hint}"

            encoding = self._detect_encoding(resp)
            resp.encoding = encoding

            content = self._extract_text(resp.text, url_hint=url, query_hint=query_hint)

            score = self._quality_score(content, query_hint)
            if score < self.QUALITY_THRESHOLD:
                from bs4 import BeautifulSoup
                fp = self._page_fingerprint(BeautifulSoup(resp.text, "html.parser"))
                print(f"    [自适应] ⚠ 提取质量偏低(分{score:.0f}), 页面指纹: {fp[:120]}")

            if not content:
                content = "页面内容为空或无法解析"

            self._page_cache[cache_key] = (now, content)
            if len(self._page_cache) > 500:
                oldest = sorted(self._page_cache.items(), key=lambda x: x[1][0])[:100]
                for k, _ in oldest:
                    del self._page_cache[k]
            return content

        except Exception as e:
            return f"访问失败: {str(e)}"

    # ==================================================================
    # 自适应提取引擎
    # ==================================================================

    @staticmethod
    def _extract_domain(url: str) -> str:
        m = re.search(r'https?://([^/]+)', url)
        return m.group(1) if m else url

    @staticmethod
    def _page_fingerprint(soup) -> str:
        from collections import Counter
        tags = Counter(tag.name for tag in soup.find_all())
        top_tags = tags.most_common(10)
        classes = []
        for tag in soup.find_all(limit=200):
            cls = tag.get("class")
            if cls:
                classes.extend(cls if isinstance(cls, list) else [cls])
        top_classes = Counter(classes).most_common(8)
        title = soup.find("title")
        title_text = title.get_text(strip=True)[:80] if title else ""
        h1s = [h.get_text(strip=True)[:60] for h in soup.find_all(["h1", "h2"])[:3]]
        fp = f"title={title_text} | tags={top_tags[:6]} | classes={top_classes[:5]} | h={h1s}"
        return fp[:300]

    @staticmethod
    def _extract_text(html: str, url_hint: str = "", query_hint: str = "") -> str:
        """自适应页面提取 — 6策略并行 + 质量评分 + 策略记忆"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        domain = WebSearchTool._extract_domain(url_hint)

        # 步骤0：检查策略记忆
        cached = WebSearchTool._domain_strategies.get(domain)
        if cached:
            strat_name = cached.get("strategy", "")
            result = WebSearchTool._apply_strategy(soup, strat_name)
            if result:
                score = WebSearchTool._quality_score(result, query_hint)
                if score > WebSearchTool.QUALITY_THRESHOLD:
                    print(f"    [自适应] 命中记忆策略「{strat_name}」(分{score:.0f}) → {domain}")
                    return result

        # 步骤1：6种策略并行
        all_candidates = []
        strategies = [
            ("data_density",    WebSearchTool._try_data_density(soup)),
            ("semantic",        WebSearchTool._try_semantic_container(soup)),
            ("text_cluster",    WebSearchTool._try_text_clusters(soup)),
            ("headline_follow", WebSearchTool._try_headline_follow(soup)),
            ("structured",      WebSearchTool._try_structured(soup)),
            ("full_page",       WebSearchTool._try_full_page(soup)),
        ]

        for name, result in strategies:
            if result and len(result) > 30:
                score = WebSearchTool._quality_score(result, query_hint)
                all_candidates.append((score, name, result))

        if not all_candidates:
            raw = soup.get_text(separator="\n", strip=True)
            return WebSearchTool._trim_lines(raw, 3000)

        # 步骤2：评分排序
        all_candidates.sort(reverse=True, key=lambda x: x[0])
        best_score, best_name, best_result = all_candidates[0]

        print(f"    [自适应] 6策略评分: " +
              " | ".join(f"{n}={s:.0f}" for s, n, _ in all_candidates[:4]))

        # 步骤3：记入策略注册表
        WebSearchTool._domain_strategies[domain] = {
            "strategy": best_name,
            "score": best_score,
            "fingerprint": WebSearchTool._page_fingerprint(soup),
        }
        if len(WebSearchTool._domain_strategies) > 300:
            stale = sorted(WebSearchTool._domain_strategies.items(),
                          key=lambda x: x[1].get("score", 0))[:50]
            for k, _ in stale:
                del WebSearchTool._domain_strategies[k]

        return best_result

    # ---- 6 种提取策略 ----

    @staticmethod
    def _try_data_density(soup) -> str:
        candidates = []
        for el in soup.find_all(["div", "section", "dl", "table", "ul", "article"]):
            text = el.get_text(separator="\n", strip=True)
            if len(text) < 40:
                continue
            data_chars = len(re.findall(r'[\d]+[℃°度元%点级年月日时分秒万亿美元]', text))
            density = data_chars / max(len(text), 1) * 100
            child_count = len(el.find_all(["div", "p", "li", "tr", "dt", "span"], limit=20))
            if density > 1.5 and child_count >= 2:
                candidates.append((density * child_count, el))

        all_text = soup.get_text(separator="\n", strip=True)
        all_lines = [ln.strip() for ln in all_text.split("\n") if ln.strip()]
        start_idx = 0
        for i, ln in enumerate(all_lines):
            if re.search(r'[\d]+[℃°度元%点级年月日]', ln):
                start_idx = i
                break
        data_section = "\n".join(all_lines[start_idx:start_idx + 80])

        if candidates:
            candidates.sort(reverse=True, key=lambda x: x[0])
            best = candidates[0][1]
            raw = best.get_text(separator="\n", strip=True)
            if len(raw) > len(data_section) * 0.5:
                return WebSearchTool._trim_lines(raw, 3000)
        return WebSearchTool._trim_lines(data_section, 3000)

    @staticmethod
    def _try_semantic_container(soup) -> str:
        SEMANTIC_TAGS = ("article", "main")
        CONTENT_CLASSES = (
            "content", "article", "body", "detail", "text", "post",
            "weather", "main", "wrap", "container", "entry", "primary",
            "info", "data", "summary", "overview", "section",
        )
        for tag_name in SEMANTIC_TAGS:
            el = soup.find(tag_name)
            if el:
                raw = el.get_text(separator="\n", strip=True)
                if len(raw) > 40:
                    return WebSearchTool._trim_lines(raw, 3000)
        for el in soup.find_all(["div", "section", "dl", "span"]):
            cls = el.get("class")
            if not cls:
                continue
            cls_str = " ".join(cls) if isinstance(cls, list) else str(cls)
            cls_lower = cls_str.lower()
            if any(k in cls_lower for k in CONTENT_CLASSES):
                raw = el.get_text(separator="\n", strip=True)
                if len(raw) > 50:
                    return WebSearchTool._trim_lines(raw, 3000)
        return ""

    @staticmethod
    def _try_text_clusters(soup) -> str:
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "noscript", "iframe", "form"]):
            tag.decompose()
        weighted = []
        for el in soup.find_all(["div", "p", "section", "article", "li", "td", "dd"]):
            text = el.get_text(separator="", strip=True)
            if not text:
                continue
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            if not lines:
                continue
            avg_len = sum(len(l) for l in lines) / len(lines)
            weight = len(text) * (avg_len / 20)
            if weight > 30:
                weighted.append((weight, el))
        weighted.sort(reverse=True, key=lambda x: x[0])
        if weighted:
            best = weighted[0][1]
            raw = best.get_text(separator="\n", strip=True)
            return WebSearchTool._trim_lines(raw, 3000)
        return ""

    @staticmethod
    def _try_headline_follow(soup) -> str:
        for tag in ["h1", "h2", "h3"]:
            h = soup.find(tag)
            if h and len(h.get_text(strip=True)) > 2:
                parent = h.find_parent(["div", "section", "article"])
                if parent:
                    raw = parent.get_text(separator="\n", strip=True)
                    if len(raw) > 60:
                        return WebSearchTool._trim_lines(raw, 3000)
                parts = [h.get_text(strip=True)]
                for sibling in h.find_next_siblings(["div", "p", "section", "dl"], limit=8):
                    parts.append(sibling.get_text(separator="\n", strip=True))
                combined = "\n".join(parts)
                if len(combined) > 60:
                    return WebSearchTool._trim_lines(combined, 3000)
        return ""

    @staticmethod
    def _try_structured(soup) -> str:
        table = soup.find("table")
        if table:
            rows = table.find_all("tr")
            if len(rows) >= 2:
                lines = []
                for tr in rows[:20]:
                    cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    lines.append(" | ".join(cells))
                return WebSearchTool._trim_lines("\n".join(lines), 3000)
        dl = soup.find("dl")
        if dl:
            terms = dl.find_all(["dt", "dd"])
            if len(terms) >= 3:
                lines = [t.get_text(strip=True) for t in terms[:30]]
                return WebSearchTool._trim_lines("\n".join(lines), 3000)
        for lst in soup.find_all(["ul", "ol"]):
            items = lst.find_all("li", limit=15)
            if len(items) >= 3:
                lines = [li.get_text(strip=True) for li in items]
                raw = "\n".join(lines)
                if len(raw) > 40:
                    return raw if len(raw) < 3000 else raw[:3000] + "\n... (内容已截断)"
        return ""

    @staticmethod
    def _try_full_page(soup) -> str:
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "noscript", "iframe", "form", "aside", "button"]):
            tag.decompose()
        raw = soup.get_text(separator="\n", strip=True)
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip() and len(ln) > 2]
        return WebSearchTool._trim_lines("\n".join(lines), 3000)

    @staticmethod
    def _apply_strategy(soup, strategy_name: str) -> str:
        strategies = {
            "data_density":    WebSearchTool._try_data_density,
            "semantic":        WebSearchTool._try_semantic_container,
            "text_cluster":    WebSearchTool._try_text_clusters,
            "headline_follow": WebSearchTool._try_headline_follow,
            "structured":      WebSearchTool._try_structured,
            "full_page":       WebSearchTool._try_full_page,
        }
        fn = strategies.get(strategy_name)
        return fn(soup) if fn else ""

    # ---- 质量评分 ----

    @staticmethod
    def _quality_score(text: str, query_hint: str = "") -> float:
        if not text or len(text) < 30:
            return 0.0
        score = 0.0
        text_len = max(len(text), 1)

        data_matches = len(re.findall(
            r'[\d]+[\s]*(?:℃|°|度|元|美元|万元|亿元|%|点|级|年月日|时分秒|米|公里|克|盎司)',
            text))
        data_density = data_matches / text_len * 1000
        score += min(data_density * 3, 35)

        lines = [l for l in text.split("\n") if l.strip()]
        if lines:
            info_lines = sum(1 for l in lines if len(l) >= 10)
            score += (info_lines / len(lines)) * 30

        if query_hint:
            qwords = set(query_hint.lower().split())
            text_lower = text.lower()
            matched = sum(1 for w in qwords if w in text_lower)
            if qwords:
                score += (matched / len(qwords)) * 20

        garbage = len(re.findall(r'[{}[\]|\\\/\*#@<>]', text))
        garbage_ratio = garbage / text_len
        score += max(0, (1 - garbage_ratio * 30)) * 15

        return max(0, min(score, 100))

    @staticmethod
    def _trim_lines(raw: str, max_chars: int) -> str:
        result, total = [], 0
        for ln in raw.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            result.append(ln)
            total += len(ln)
            if total > max_chars:
                result.append("... (内容已截断)")
                break
        return "\n".join(result)
