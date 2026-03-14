# -*- coding: utf-8 -*-
"""
Search helper utilities: query sanitization, DDGS/Bing search, source enrichment.
"""

import re
import logging
from typing import List, Dict
from urllib.parse import unquote, quote_plus

from ddgs import DDGS
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("api")

# ── Regex constants ────────────────────────────────────────

# 移除 OpenClaw 在 system message 裡附帶的大段 sender metadata JSON
_SENDER_META_RE = re.compile(
    r"(?:Sender|Conversation info)\s*\(untrusted metadata\):\s*```json.*?```\s*",
    re.DOTALL,
)

# 移除仍殘留的 markdown json metadata block（防禦性處理）
_GENERIC_JSON_FENCE_RE = re.compile(r"```json.*?```", re.DOTALL)

_TIME_FILLERS_RE = re.compile(
    r"^(?:今天|今日|現在|最近|这周|這週|this\s+week|today|right\s+now|currently)\s*",
    re.IGNORECASE,
)


def strip_sender_metadata(text: str) -> str:
    """移除 OpenClaw agent 注入的 sender metadata block"""
    if not isinstance(text, str) or "untrusted metadata" not in text:
        return text
    return _SENDER_META_RE.sub("", text).strip()


def _clean_query_text(text: str) -> str:
    """Normalize extracted user query for tool-calling."""
    if not isinstance(text, str):
        return ""

    cleaned = strip_sender_metadata(text)
    cleaned = _GENERIC_JSON_FENCE_RE.sub("", cleaned)
    cleaned = re.sub(r"^@\w+[\s,:-]*", "", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _sanitize_search_query(query: str) -> str:
    """Sanitize tool-instruction text into a search-friendly query."""
    if not isinstance(query, str):
        return ""

    q = unquote(query).strip()
    q = _clean_query_text(q)

    q = re.sub(
        r"^(?:請|麻煩)?\s*(?:使用|用)?\s*web[_ ]?search\s*(?:工具)?\s*(?:幫我)?\s*(?:查詢|查找|搜尋|搜索|找)?\s*",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(
        r"^(?:please\s+)?(?:use\s+)?web[_ ]?search\s*(?:tool)?\s*(?:to\s+)?(?:search|find|look\s+up)?\s*",
        "",
        q,
        flags=re.IGNORECASE,
    )

    # Handle leftovers like "tool 查詢今天東京天氣" / "tool search tokyo weather".
    q = re.sub(
        r"^(?:tool|tools|web[_ ]?tool|web[_ ]?search)\s*(?:[:：-])?\s*",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(
        r"^(?:查詢|查找|搜尋|搜索|找|search|find|lookup|look\s+up)\s*",
        "",
        q,
        flags=re.IGNORECASE,
    )

    # Drop very common temporal fillers that reduce recall.
    q = re.sub(r"^(?:今天|今日|現在)\s*", "", q)

    quoted = re.search(r"[\"""「『](.+?)[\"""」』]", q)
    if quoted:
        q = quoted.group(1).strip()

    return re.sub(r"\s+", " ", q).strip()


def _generate_search_query_variants(query: str) -> List[str]:
    """Generate generic fallback query variants.

    No domain-specific heuristics. Rules applied in order:
    1. Original sanitized query.
    2. Strip leading time fillers (今天 / today / …) if present.
    That's it — avoid over-fitting to specific query types.
    """
    sanitized = _sanitize_search_query(query)
    if not sanitized:
        return []

    variants: List[str] = [sanitized]

    # Generic: remove temporal filler at the start
    without_time = _TIME_FILLERS_RE.sub("", sanitized).strip()
    if without_time and without_time != sanitized:
        variants.append(without_time)

    deduped: List[str] = []
    seen: set = set()
    for variant in variants:
        normalized = re.sub(r"\s+", " ", variant).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def _guess_search_region(query: str) -> str:
    """Pick a DDGS region suited to the query language."""
    if re.search(r"[\u3040-\u30ff\u31f0-\u31ff]", query):
        return "jp-jp"
    if re.search(r"[\u4e00-\u9fff]", query):
        return "tw-tzh"
    return "wt-wt"


def _ddgs_text_search(query: str, max_results: int) -> List[Dict[str, str]]:
    """Run DDGS text search, trying multiple backends until results are found.

    Backend order is language-aware:
    - CJK queries: yahoo first, then google, ddg.
    - Other queries: google first, then ddg, yahoo.
    """
    region = _guess_search_region(query)
    is_cjk = bool(re.search(r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fff]", query))
    # Use only known-supported backends to avoid KeyError(...)->auto fallback.
    backends = ("yahoo", "google", "duckduckgo") if is_cjk else ("google", "duckduckgo", "yahoo")
    for backend in backends:
        try:
            results = list(
                DDGS(timeout=10).text(
                    query,
                    region=region,
                    safesearch="off",
                    max_results=max_results,
                    backend=backend,
                )
            )
            if results:
                logger.info(f"[DDGS] backend={backend} returned {len(results)} results for '{query}'")
                return results
            logger.info(f"[DDGS] backend={backend} returned 0 results for '{query}', trying next")
        except Exception as e:
            logger.warning(f"[DDGS] backend={backend} error: {e}")
    return []


def _bing_html_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Fallback parser for Bing HTML results when DDGS returns an empty list."""
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    parsed_results: List[Dict[str, str]] = []

    for item in soup.select("li.b_algo"):
        if len(parsed_results) >= max_results:
            break

        anchor = item.select_one("h2 a")
        snippet_node = item.select_one(".b_caption p") or item.select_one("p")
        if anchor is None:
            continue

        href_attr = anchor.get("href")
        href = href_attr.strip() if isinstance(href_attr, str) else ""
        title = anchor.get_text(" ", strip=True)
        snippet = snippet_node.get_text(" ", strip=True) if snippet_node else "No Snippet"
        if not href or not title:
            continue

        parsed_results.append({
            "title": title,
            "href": href,
            "body": snippet or "No Snippet",
        })

    return parsed_results


def _needs_source_enrichment(query: str) -> bool:
    """Decide whether to fetch source pages for deeper facts.

    Triggered for data-heavy/time-sensitive queries (finance, prices, counts, close values).
    """
    if not isinstance(query, str):
        return False
    keywords = [
        "收盤", "昨收", "昨收價", "台指期", "期貨", "股價", "指數", "多少點", "成交", "報價",
        "close", "closing", "settle", "settlement", "price", "index", "quote",
    ]
    lowered = query.lower()
    return any(k in query for k in keywords if not k.isascii()) or any(k in lowered for k in keywords if k.isascii())


def _extract_relevant_lines(text: str, query: str, max_lines: int = 3) -> List[str]:
    """Extract short, query-related lines (prefer lines with numeric values)."""
    if not text:
        return []

    query_terms = [
        t for t in re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", query.lower())
        if t not in {"今天", "今日", "請使用", "web", "search", "tool"}
    ]

    chunks = [
        c.strip() for c in re.split(r"[\n\r。；;!?！？]+", text)
        if c and c.strip()
    ]

    scored: List[tuple] = []
    for chunk in chunks:
        lc = chunk.lower()
        term_hit = sum(1 for t in query_terms if t in lc)
        if term_hit == 0:
            continue
        has_number = bool(re.search(r"\d", chunk))
        # Prioritize lines with both query overlap and numbers.
        score = term_hit * 10 + (5 if has_number else 0)
        scored.append((score, chunk))

    scored.sort(key=lambda x: x[0], reverse=True)
    picked: List[str] = []
    for _, line in scored:
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized and normalized not in picked:
            picked.append(normalized)
        if len(picked) >= max_lines:
            break
    return picked


def _fetch_source_detail(url: str, query: str) -> str:
    """Fetch a source page and extract compact, relevant details."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return ""

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=6)
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" not in ctype and "application/xhtml+xml" not in ctype:
            return ""

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        meta_desc = ""
        meta = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
        if meta and meta.get("content"):
            meta_desc = str(meta.get("content")).strip()

        text = soup.get_text("\n", strip=True)
        text = re.sub(r"\s+", " ", text)
        lines = _extract_relevant_lines(text[:12000], query, max_lines=2)

        parts = []
        if title:
            parts.append(f"Title: {title}")
        if meta_desc:
            parts.append(f"Meta: {meta_desc}")
        if lines:
            parts.append("Details: " + " | ".join(lines))

        detail = " ; ".join(parts).strip()
        return detail[:1000]
    except Exception as e:
        logger.info("[MCP DDGS] source detail fetch skipped (%s): %s", url, e)
        return ""


def _enrich_search_results(search_results: List[Dict[str, str]], query: str) -> List[Dict[str, str]]:
    """Attach optional `detail` field by fetching top result pages."""
    if not search_results:
        return search_results

    enriched: List[Dict[str, str]] = []
    max_fetch = 2
    fetched = 0
    for r in search_results:
        item = dict(r)
        url = item.get("href") or item.get("url")
        if fetched < max_fetch and isinstance(url, str):
            detail = _fetch_source_detail(url, query)
            if detail:
                item["detail"] = detail
            fetched += 1
        enriched.append(item)
    return enriched


def _looks_low_quality(results_text: str, query: str) -> bool:
    """Generic quality check with CJK-aware overlap.

    For CJK queries, use character overlap instead of whitespace tokenization.
    For non-CJK queries, use token overlap.
    """
    if not results_text or "URL:" not in results_text:
        return True

    is_cjk = bool(re.search(r"[\u3040-\u30ff\u31f0-\u31ff\u4e00-\u9fff]", query))
    if is_cjk:
        q_chars = set(re.sub(r"\s+", "", query))
        r_chars = set(re.sub(r"\s+", "", results_text))
        if not q_chars:
            return False
        return len(q_chars & r_chars) == 0

    q_tokens = set(re.findall(r"[a-zA-Z0-9]+", query.lower()))
    r_tokens = set(re.findall(r"[a-zA-Z0-9]+", results_text.lower()))
    if not q_tokens:
        return False
    return len(q_tokens & r_tokens) == 0
