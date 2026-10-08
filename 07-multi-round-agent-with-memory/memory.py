"""Long-term memory: a JSONL note store, its index, and retrieval scoring.

Pure and network-free by design, exactly the way context.py is: nothing here
imports config or react_agent, and every budget arrives as a parameter. That is
what lets _test.py cover this module with no API call, and it is why the module
exists at all rather than living inside the plugin.

Three consumers, three functions:

  * render_index()  -> the block react_agent splices into its system prompt
  * search()        -> what recall_notes returns
  * append()        -> what write_note returns

MEMORY.md is the design record: why the index exists, why summary and body are
separate fields, and why the scoring works on CJK bigrams.
"""
import json
import os
import re
import time

MEMORY_DIR = "memory"
NOTES_PATH = os.path.join(MEMORY_DIR, "notes.jsonl")

# The index sits in messages[0], so it is paid for on *every* request of the
# session. These two numbers are therefore a token budget, not a display
# preference: INDEX_MAX_NOTES x ~one line each is the fixed cost of having
# long-term memory at all. Raising them raises the cost of every turn.
INDEX_MAX_NOTES = 40
INDEX_MAX_CHARS = 4000

# The four kinds, borrowed from Claude Code's frontmatter. They differ in how
# easy they are to justify: "the user prefers X" is worth recording the moment
# it is said, "the build is broken because of Y" only after it was verified.
NOTE_TYPES = ("user", "feedback", "project", "reference")

# Per-note body budget in a recall result. Far below clip()'s MAX_OBS_CHARS
# (8,000) on purpose — see render_recall().
RECALL_BODY_CHARS = 600

_LATIN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]+")


def _terms(text: str) -> set:
    """Query terms: latin words plus CJK bigrams.

    Splitting on whitespace would be useless here. Chinese is written without
    spaces, so `query.split()` turns an entire question into a single token
    that matches nothing at all — the retriever would look broken while being
    perfectly "correct". Bigrams are the cheap fix: 作者用中文 becomes
    作者/者用/用中/中文, and a note containing 作者 scores.

    Single CJK characters are kept as-is (a one-character run has no bigram),
    latin words of one character are dropped as too common to mean anything.
    """
    text = text.lower()
    terms = {w for w in _LATIN_RE.findall(text) if len(w) > 1}
    for run in _CJK_RE.findall(text):
        if len(run) == 1:
            terms.add(run)
        else:
            terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return terms


def load(path: str = NOTES_PATH) -> list:
    """Read every note, oldest first. A missing file is an empty store.

    A malformed line is skipped rather than fatal: the file is append-only and
    hand-editable, so one bad line must not make every note unreachable. The
    count of skipped lines is not reported — `describe()` surfaces the count of
    loaded notes, which is what the caller can act on.
    """
    notes = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    note = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(note, dict) and note.get("summary"):
                    notes.append(note)
    except FileNotFoundError:
        return []
    return notes


def append(summary: str, body: str = "", type: str = "project",
           path: str = NOTES_PATH) -> tuple:
    """Append one note. Returns (written, message).

    Writing is idempotent on the summary: the most common duplicate is the
    model recording the same fact twice in one session, and a store that grows
    a copy every time it is told the same thing stops being an index of what is
    known and becomes a log of what was said. An exact-summary match returns
    (False, ...) and writes nothing.

    `type` shadows the builtin for the length of this function; the name is
    kept because it is model-visible (it is the tool argument name) and it
    matches the vocabulary this design borrowed.
    """
    summary = (summary or "").strip()
    if not summary:
        return False, "The summary was empty — nothing was written."
    if type not in NOTE_TYPES:
        return False, f"Unknown type {type!r}; the valid types are {list(NOTE_TYPES)}."

    notes = load(path)
    if any(n.get("summary") == summary for n in notes):
        return False, f"Already stored (not written again): {summary}"

    note = {
        "id": max((n.get("id") or 0) for n in notes) + 1 if notes else 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "type": type,
        "summary": summary,
        "body": (body or "").strip(),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(note, ensure_ascii=False) + "\n")
    return True, f"Stored note {note['id']} ({note['type']}): {summary}"


def forget(id: int, path: str = NOTES_PATH) -> tuple:
    """Delete one note by id. Returns (deleted, message).

    Rewrites the whole file through a temporary one and os.replace — the
    rename is atomic, so a crash mid-write cannot leave a half-store. At this
    size (tens of notes) a full rewrite costs nothing, which is why deleting
    does NOT require the one-file-per-note layout that an earlier draft of
    MEMORY.md claimed it did.

    Deletion takes an id and nothing else. That is the safety property: the
    blast radius of a bad call is exactly one note, and there is no query a
    confused model could pass that would clear several at once.

    Deleting never renumbers the survivors: take id 2 out of [1, 2, 3] and 1
    and 3 keep their ids, so an id read a moment ago still names the same note.
    The one exception is a store emptied completely — append() then starts over
    at 1, because there is nothing left for the new id to collide with. That is
    deliberate (a counter file would be state to keep in sync for no gain), and
    both behaviours are pinned by _test.py 9j.

    `id` shadows the builtin for the length of this function, for the same
    reason append()'s `type` does: it is the model-visible argument name and
    it matches the field it selects.
    """
    notes = load(path)
    kept = [n for n in notes if n.get("id") != id]
    if len(kept) == len(notes):
        return False, f"No note with id={id}; nothing was deleted."

    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for note in kept:
            f.write(json.dumps(note, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return True, f"Deleted note {id} ({len(kept)} left)."


def score(note: dict, query: str, terms: set) -> float:
    """Relevance in 0..1.

    A whole-query substring hit is 1.0 — it is the strongest signal available
    without embeddings. Otherwise the score is the share of the query's terms
    the note contains, which is what makes a partially-matching note rank below
    an exact one instead of disappearing.
    """
    hay = (str(note.get("summary", "")) + "\n" + str(note.get("body", ""))).lower()
    if query and query in hay:
        return 1.0
    if not terms:
        return 0.0
    return sum(1 for t in terms if t in hay) / len(terms)


def search(query: str, k: int = 5, path: str = NOTES_PATH) -> tuple:
    """Top-k notes for a query. Returns (matched, [(score, note), ...]).

    Zero-score notes are dropped. When nothing matches at all, the most recent
    k are returned with matched=False instead of an empty result: a caller that
    gets nothing back cannot tell "the store is empty" from "my query was
    bad", and the second case is the one a keyword retriever produces most
    often. Returning the tail turns a failed lookup into a usable answer.
    """
    notes = load(path)
    query = (query or "").strip().lower()
    terms = _terms(query)
    scored = [(score(n, query, terms), n) for n in notes]
    scored = [pair for pair in scored if pair[0] > 0]
    if not scored:
        recent = notes[-k:] if k > 0 else []
        return False, [(0.0, n) for n in reversed(recent)]
    scored.sort(key=lambda pair: (-pair[0], -(pair[1].get("id") or 0)))
    return True, scored[:k]


def render_index(path: str = NOTES_PATH, max_notes: int = INDEX_MAX_NOTES,
                 max_chars: int = INDEX_MAX_CHARS) -> str:
    """The index block for the system prompt, or "" when the store is empty.

    One line per note, oldest first, each led by its id. The budget is spent
    newest-first and the result is flipped back, so when the cap bites it is
    the *oldest* notes that fall out — the recent ones are the ones most likely
    to still be true.

    The id costs about four characters per line (~40 tokens for a full index)
    and buys the ability to call forget_note(id=…) straight from the index,
    without a recall_notes round trip first. A summary is not enough to delete
    by, and by design deletion takes nothing else.

    The truncation notice is not decoration. Without it the notes that did not
    fit are, as far as the model is concerned, notes that do not exist — and it
    has no way to discover otherwise, because nothing tells it to look.
    """
    notes = load(path)
    if not notes:
        return ""

    shown, used = [], 0
    for note in reversed(notes):
        line = (f"- [{note.get('id', '?')}] ({note.get('type', '?')}) "
                f"{note.get('summary', '')}")
        if len(shown) >= max_notes or used + len(line) > max_chars:
            break
        shown.append(line)
        used += len(line) + 1

    hidden = len(notes) - len(shown)
    shown.reverse()
    block = "\n".join(shown)
    if hidden:
        notice = f"- ({hidden} more not shown — use recall_notes to search)"
        block = f"{block}\n{notice}" if block else notice
    return block


def render_recall(matched: bool, results: list, query: str,
                  body_chars: int = RECALL_BODY_CHARS) -> str:
    """Format search()'s result for the model.

    Lives here rather than in the plugin so _test.py can cover it with no
    network — the same reason context.py has its two formatters. Per-note body
    truncation is deliberately far below clip()'s 8,000: a recall that returns
    one note in full and four clipped away answers a different question than
    the one that was asked.
    """
    if not results:
        return "Long-term memory is empty — no notes yet."

    if matched:
        lines = [f"{len(results)} note(s) matched, best first:"]
    else:
        lines = [f"No note matched {query!r}. The {len(results)} newest are below, "
                 f"only so you know what the store holds — if none is relevant, "
                 f"treat this as no memory at all; do not force a fit."]

    for value, note in results:
        if matched:
            lines.append(f"[{note.get('id')}] ({note.get('type')}, "
                         f"score {value:.2f}) {note.get('summary', '')}")
        else:
            lines.append(f"[{note.get('id')}] ({note.get('type')}, "
                         f"{str(note.get('ts', ''))[:10]}) {note.get('summary', '')}")
        body = str(note.get("body") or "").strip()
        if body:
            if len(body) > body_chars:
                body = body[:body_chars] + f"… (the body is {len(body)} chars, truncated)"
            lines.append("    " + body.replace("\n", "\n    "))
    return "\n".join(lines)


def describe(path: str = NOTES_PATH, max_notes: int = INDEX_MAX_NOTES,
             max_chars: int = INDEX_MAX_CHARS) -> str:
    """What /memory prints.

    Terminal-facing, unlike the rest of this module: render_index, render_recall
    and append put their strings in front of the *model* as tool results, and
    are worded for it. This one is read by the person who typed `/memory`, next
    to `/help` and `✅ Compacted: …`.
    """
    notes = load(path)
    if not notes:
        return (f"No long-term memory yet ({path} does not exist, or holds no notes).\n"
                f"Index cap: {max_notes} notes / {max_chars} chars.")

    counts = {}
    for note in notes:
        counts[note.get("type", "?")] = counts.get(note.get("type", "?"), 0) + 1
    breakdown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    index = render_index(path, max_notes, max_chars)
    return (f"{len(notes)} note(s): {breakdown}\n"
            f"Index: {len(index):,} chars (cap {max_notes} notes / {max_chars} chars)"
            f" — paid for on every request\n"
            f"Store: {os.path.abspath(path)}")
