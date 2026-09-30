"""Shared foundation for the plugins. A leading underscore = not a plugin, the loader skips it.

This holds what **several plugin groups share**. A helper only one group needs stays with
that group (`_run_git` lives in `vcs.py`, for example) — do not pile it up here.
"""
import re

# Matches ANSI escape sequences (CSI form, e.g. "\x1b[01;36m"). See _strip_ansi for why.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Tags whose whole subtree is dropped when extracting body text.
# The first six mean "this content is not for humans"; the last four (nav/header/footer/
# aside) were added on 2026-09-28: measured on one content page, dropping them cut body
# noise by 98%. Note it is not a silver bullet — navigation scattered through <div>s
# (some homepages, say) cannot be dropped this way.
_SKIP_TAGS = ["script", "style", "noscript", "head", "svg", "template",
              "nav", "header", "footer", "aside"]

# A browser UA. A fair number of sites reject the default python-requests UA.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Accept-Language comes along for free: Chinese sources then return Chinese rather than
# an English or machine-translated version.
_HEADERS = {"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}


def _http(method: str, url: str, timeout: int = 20, **kw):
    '''
    All requests traffic goes through here — this function exists for one reason.

    **Its only job is to translate requests' exceptions into the builtin ConnectionError.**

    `react_agent.py`'s RETRIABLE_ERRORS only recognizes the builtin ConnectionError /
    TimeoutError, and requests' exceptions inherit from OSError:

        measured: issubclass(requests.exceptions.ConnectionError, ConnectionError) -> False
                  issubclass(requests.exceptions.Timeout,         TimeoutError)    -> False

    Without the translation a network blip falls into `_call_tool`'s generic
    `except Exception` and comes back to the model as an ordinary `Error: ...` — which
    looks perfectly normal but is **never retried**. The bug is silent, which is exactly
    why it is worth spelling out here.

    Args:
        method (str): HTTP method, e.g. "GET" or "POST".
        url (str): The URL to request.
        timeout (int): Read timeout in seconds. Defaults to 20.
            The connect timeout is pinned at 5s separately — failing to connect and
            responding slowly are two different things; an unreachable host gives the
            same verdict at 20s and at 5s, but 14 retries save ~3.5 minutes of pure
            waiting this way.
        **kw: Passed straight through to requests (params / data / stream / ...).

    Returns:
        requests.Response: **a non-2xx does not raise** — the caller uses `_http_error`
        to decide whether that status counts as "transient" or "deterministic".

    Raises:
        ImportError: requests is not installed. This import sits inside the function on
            purpose: only the network tools need it.
        ConnectionError: Connection failed / timed out / transfer interrupted (retriable).
    '''
    try:
        import requests
    except ImportError as e:
        raise ImportError("The 'requests' library is required to use this function. "
                          "Please install it using 'pip install requests'.") from e

    _NETWORK_ERRORS = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )

    try:
        # (connect, read): failing to connect and responding slowly are two different
        # things. The connect timeout is pinned at 5s — an unreachable host gives the
        # same verdict at 20s and at 5s, but 14 retries save ~3.5 minutes of pure waiting.
        response = requests.request(method, url, timeout=(5, timeout),
                                    headers=_HEADERS, **kw)
        return response
    except _NETWORK_ERRORS as e:
        raise ConnectionError(f"Network error occurred: {e}")


def _strip_ansi(text: str) -> str:
    '''
    Strip ANSI escape sequences from a string.

    Why it is needed: **color cannot be turned off reliably from the outside**; both
    measured attempts came up short —
    - Python 3.13+ honors FORCE_COLOR even when the output is a pipe, not a terminal;
    - `git -c color.ui=false` does not beat a more specific `color.status=always` in the
      user's gitconfig.

    Those escape bytes reach the observation, get counted as body text by `clip()`, and
    end up eating the model's context budget as mojibake. Stripping them once on the
    Python side is the only reliable fix.

    Args:
        text (str): Raw subprocess output (or anything else that may carry escapes).

    Returns:
        str: The same text with escape sequences removed.
    '''
    return _ANSI_RE.sub("", text)


def _html_to_text(html: str) -> str:
    '''
    Strip tags from HTML and return readable text.

    Builds the tree with bs4 + lxml rather than a hand-written parser:
    - unclosed `<p>` and tables missing `</td>` both recover correctly;
    - `decompose()` removes a whole subtree, so an unpaired `<script>` cannot wedge it
      (a hand-maintained skip counter would be).

    Falls back to `html.parser` when lxml is unavailable, but it must **never** be written
    as `except: return ""` — every page would then look empty and the model would draw the
    wrong conclusion, "this page has no content".

    Args:
        html (str): Raw HTML.

    Returns:
        str: The body text, blocks separated by "\\n". Tags in `_SKIP_TAGS` (including nav/header/footer/aside) have their whole subtree removed.
    '''
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    return text


def _http_error(resp, url: str) -> str:
    '''
    Turn a non-2xx response into either a raise (retriable) or an error string.

    **4xx and 5xx must be split**, because the right response to them is opposite:
    - **5xx** is a transient server-side failure -> raise ConnectionError -> retried;
    - **4xx** is deterministic (wrong URL / deleted / no permission) -> return a string ->
      the model fixes the URL itself instead of burning 3 retries.

    Lumping both into "request failed" is the easiest write and the worst one: it either
    waits out retries for nothing or robs the model of the chance to correct itself.

    Args:
        resp: A requests.Response.
        url (str): The URL, for the message.

    Returns:
        str: An empty string means 2xx/3xx (the caller keys off that with `if err: return err`); otherwise the error message for the model.

    Raises:
        ConnectionError: 5xx (retriable).
    '''
    if resp.status_code < 400:
        return ""
    if resp.status_code >= 500:
        raise ConnectionError(f"HTTP {resp.status_code} {resp.reason} for URL: {url}")  # retriable
    return f"Error: HTTP {resp.status_code} {resp.reason} for URL: {url}"  # not retriable
