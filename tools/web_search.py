from typing import Any

from langchain_community.utilities.duckduckgo_search import DuckDuckGoSearchAPIWrapper
from langchain_core.tools import tool


@tool("internet_search")
def internet_search(
    query: str, max_results: int = 5, backend: str = "text"
) -> list[dict[str, Any]]:
    """Search the web with DuckDuckGo and return structured results with URLs.

    Args:
        query: Search query text.
        max_results: Number of results to return (1-10 recommended).
        backend: DuckDuckGo source backend ("text" or "news").

    Returns:
        A list of dicts with keys: title, link, snippet, date, source.
    """
    clean_query = query.strip()
    if not clean_query:
        return []

    # Keep bounds stable to avoid very large payloads and noisy results.
    safe_max_results = max(1, min(max_results, 10))
    safe_backend = backend if backend in {"text", "news"} else "text"

    try:
        wrapper = DuckDuckGoSearchAPIWrapper()
        raw_results = wrapper.results(
            clean_query, safe_max_results, source=safe_backend
        )
    except Exception as exc:
        return [{"error": f"Search failed: {exc}"}]

    normalized_results: list[dict[str, Any]] = []
    for item in raw_results:
        normalized_results.append(
            {
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "date": item.get("date", ""),
                "source": item.get("source", ""),
            }
        )
    return normalized_results
