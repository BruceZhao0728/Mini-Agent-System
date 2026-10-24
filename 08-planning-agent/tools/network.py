"""Network tools: search / fetch a page / news / download."""
import os

from ._spec import tool
from ._common import _http, _http_error, _html_to_text


@tool(
    description=(
        "Search the web and return the top results as title, url and snippet. "
        "Use this to find URLs, then fetch_webpage to read one."
    ),
    parameters={
        "query": {"type": "string", "description": "Search query"},
        "limit": {"type": "integer", "description": "Max results, default 5"},
    },
    required=["query"],
)
def search_web(query: str, limit: int = 5) -> str:
    '''
    Search the web and return the top results.

    Uses the ddgs library, which rotates between backends (Google / Bing /
    Mojeek / ...), so how good a given search is varies run to run. Transient
    backend failures are translated to ConnectionError so _call_tool retries
    them; a deterministic DDGSException is not translated and is not retried.

    Args:
        query (str): The search query.
        limit (int): Maximum number of results to return.

    Returns:
        str: One block per result - "N. title" / url / snippet - or an Error: message.
    '''
    try:
        from ddgs import DDGS
        from ddgs.exceptions import RatelimitException, TimeoutException
    except ImportError:
        return "Error: ddgs module not installed. Please install it using 'pip install ddgs'."

    try:
        results = DDGS().text(query, max_results=limit)
    except (TimeoutException, RatelimitException) as e:
        raise ConnectionError(f"{type(e).__name__}: {e}") from e

    if not results:
        return f"No results found for {query!r}."

    lines = [f"{len(results)} results for {query!r}:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {(r.get('title') or '').strip()}")
        lines.append(f"   {r.get('href', '')}")
        body = (r.get("body") or "").strip()
        if body:
            lines.append(f"   {body[:200]}")
    return "\n".join(lines)


@tool(
    description=(
        "Fetch a URL over http/https and return its readable text with tags stripped. "
        "Use this for articles and docs. Requires the full URL including the scheme."
    ),
    parameters={
        "url": {"type": "string", "description": "Full URL, e.g. https://example.com/a"},
        "max_chars": {"type": "integer", "description": "Max characters of text, default 6000"},
    },
    required=["url"],
)
def fetch_webpage(url: str, max_chars: int = 6000) -> str:
    '''
    Fetch a webpage and return its readable text.

    Only the head is kept; the rest is marked with "...[N more characters not shown]".
    The truncation happens here rather than in clip(): clip() keeps both head and tail,
    which would splice the page footer onto the end of the body text.

    Args:
        url (str): The full URL to fetch, including the http/https scheme.
        max_chars (int): Maximum number of characters of body text to return.

    Returns:
        str: A header line (status / byte size / text size), then the page text.
    '''
    resp = _http("GET", url, timeout=20)

    err = _http_error(resp, url)
    # 4xx is deterministic (wrong URL / deleted / no permission): return a string and let
    # the model fix it. 5xx already raised ConnectionError inside _http_error, so it is
    # retried.
    if err:
        return err

    # With no charset from the server, requests defaults to ISO-8859-1 and Chinese pages
    # come out as mojibake.
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding

    text = _html_to_text(resp.text)
    total = len(text)

    # Head only here; this cannot be left to clip(): clip keeps head and tail, splicing
    # the footer onto the body text.
    body = text[:max_chars]
    if total > max_chars:
        body += f"\n...[{total - max_chars} more characters not shown]"

    header = (f"# {url} (HTTP {resp.status_code}, {len(resp.content)} bytes, "
              f"{total} chars of text, showing {len(body)})")
    return header + "\n" + body


@tool(
    description=(
        "Get news headlines from Google News, optionally filtered by keyword. "
        "Call with no keyword for top headlines. "
        "Returns titles, sources and publisher sites only - "
        "to read a full article, use search_web with the headline to find it."
    ),
    parameters={
        "keyword": {"type": "string", "description": "Search term; omit for top headlines"},
        "limit": {"type": "integer", "description": "Max headlines, default 10"},
        "lang": {"type": "string", "description": "'zh' (default) or 'en'"},
    },
    required=[],             # callable with no arguments; the defaults live in the Python signature
)
def get_news(keyword: str = "", limit: int = 10, lang: str = "zh") -> str:
    '''
    Get news headlines from Google News, optionally filtered by keyword.

    entry["link"] is deliberately not printed: it is a news.google.com/rss/articles/<id>
    JS redirect shell, and the real address is not in the HTML it returns (measured: that
    593KB page contains no real news links at all). The publisher's domain is printed
    instead, since at least fetch_webpage can actually fetch that.

    Args:
        keyword (str): Search term. Empty string means top headlines.
        limit (int): Maximum number of headlines to return.
        lang (str): "zh" for Chinese, "en" for English.

    Returns:
        str: One block per headline (title / source - time / publisher site), or an Error: message.
    '''
    try:
        import feedparser
    except ImportError:
        return ("Error: feedparser module not installed. "
                "Please install it using 'pip install feedparser'.")

    locale = {
        "zh": ("zh-CN", "CN", "CN:zh"),
        "en": ("en-US", "US", "US:en"),
    }.get(lang)
    if locale is None:
        return f"Error: unsupported lang {lang!r}; use 'zh' or 'en'."

    hl, gl, ceid = locale
    # With a keyword it goes to search, without one to the headlines. Both endpoints
    # return the same shape.
    base = "https://news.google.com/rss/search" if keyword else "https://news.google.com/rss"
    params = {"hl": hl, "gl": gl, "ceid": ceid}
    if keyword:
        params["q"] = keyword

    resp = _http("GET", base, timeout=20, params=params)
    err = _http_error(resp, base)
    if err:
        return err

    feed = feedparser.parse(resp.content)      # note: content (bytes), not text

    # feedparser never raises: on failure it returns 0 entries, and bozo is not
    # necessarily True (measured: when the endpoint served an HTML error page, entries=0
    # but bozo=False). So the only usable signal is entries, never bozo. Returning those
    # 0 entries as a result would lead the model to the wrong conclusion, "there is no
    # news" — so this raises a transient failure instead and leaves it to _call_tool.
    if not feed.entries:
        raise ConnectionError(f"news feed returned no entries (status={feed.get('status')}, "
                              f"bozo={feed.get('bozo')}: {feed.get('bozo_exception')})")

    label = keyword if keyword else "top headlines"
    lines = [f"{min(limit, len(feed.entries))} items for {label!r} ({lang}):"]
    for i, entry in enumerate(feed.entries[:limit], 1):
        src = entry.get("source") or {}
        source = src.get("title", "")
        when = entry.get("published", "")[:22]
        lines.append(f"{i}. {entry.get('title', '').strip()}")
        lines.append(f"   {source}{' - ' if source and when else ''}{when}")
        # entry["link"] is deliberately not printed: it is a news.google.com/rss/articles/<id>
        # JS redirect shell and fetch_webpage cannot reach the body through it (measured:
        # that 593KB page holds no real news links). The publisher's domain is printed
        # instead, since that one is actually fetchable.
        if src.get("href"):
            lines.append(f"   {src['href']}")
    return "\n".join(lines)


@tool(
    description=(
        "Download a URL to a local file, byte for byte. Use this for images, zip files, PDFs, etc. "
        "Returns a one-line summary; the content is not returned."),
    parameters={
        "url": {"type": "string", "description": "File to download"},
        "path": {"type": "string", "description": "Where to write it; parent dirs are created"},
        "max_bytes": {"type": "integer", "description": "Refuse anything larger, default 10000000"},
    },
    required=["url", "path"],
)
def download_file(url: str, path: str, max_bytes: int = 10_000_000) -> str:
    '''
    Download a URL to a local file, byte for byte.

    write_file is no substitute: it is text mode with encoding='utf-8' and would corrupt
    an image/zip/PDF. The return value is only a summary — the content never enters the
    context, which is the whole point of downloading to a file.

    Args:
        url (str): The file to download.
        path (str): Where to write it. Parent directories are created.
        max_bytes (int): Refuse anything larger, checked twice (Content-Length
            and while streaming).

    Returns:
        str: A one-line summary.
    '''
    path = os.path.expanduser(path)
    resp = _http("GET", url, timeout=60, stream=True)
    err = _http_error(resp, url)
    if err:
        return err

    declared = int(resp.headers.get("Content-Length") or 0)
    if declared > max_bytes:
        return (f"Error: {url} is {declared} bytes, over the {max_bytes} byte limit. "
                f"Raise max_bytes if this is intended.")

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    written = 0
    with open(path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            written += len(chunk)
            if written > max_bytes:
                f.close()
                os.remove(path)
                return (f"Error: {url} exceeded the {max_bytes} byte limit while streaming; "
                        f"the partial file was removed.")
            f.write(chunk)

    ctype = resp.headers.get("Content-Type", "unknown")
    return f"downloaded: {path} ({written} bytes, {ctype}) from {url}"
