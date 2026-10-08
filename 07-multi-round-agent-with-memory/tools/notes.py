"""Long-term memory tools: record a note, recall notes, delete a note.

A thin shell over the top-level `memory` module, which holds the store, the
index and the scoring as pure functions. Everything model-facing lives in the
`description=` strings below; everything testable lives in memory.py.

`import memory` reaches the lesson directory, not this package. That is the one
place a plugin depends on a top-level module — see MEMORY.md §8, "known
consequences". Do not import config here: the loader group of _test.py copies
tools/ into a temp directory and imports it with that directory as cwd, so a
module-level config import would make the "loads unchanged" case fail.
"""
import memory
from ._spec import tool


@tool(
    description=(
        "Save a fact to long-term memory so a FUTURE session can use it. The summary is "
        "shown in every later session's context, so it must stand alone — write "
        "'The author of this repo asks in Chinese', not 'as mentioned above'. Put the "
        "evidence, the why and the date in body. Record what the code, git history and "
        "CLAUDE.md do NOT already say: a local patch that lives outside the repo, a "
        "trap that cost time, a preference the user stated."
    ),
    parameters={
        "summary": {"type": "string", "description": "One self-contained line (<=100 chars) — this is what later sessions see"},
        "body": {"type": "string", "description": "The detail: why, the evidence, paths, dates. A few hundred characters."},
        "type": {"type": "string", "description": "user (who the user is) / feedback (how to work) / project (ongoing work, constraints) / reference (pointers to external things)"},
    },
    required=["summary"],
)
def write_note(summary: str, body: str = "", type: str = "project") -> str:
    '''
    Append one note to memory/notes.jsonl. Idempotent on the summary.

    Args:
        summary (str): One self-contained line. It goes into the index that is
            spliced into every later session's system prompt.
        body (str): The detail the index cannot carry.
        type (str): One of memory.NOTE_TYPES. Defaults to "project".

    Returns:
        str: What happened, including the note's id, or why nothing was written.
    '''
    try:
        written, message = memory.append(summary, body, type)
    except Exception as e:
        return f"Error writing note: {type(e).__name__}: {e}"
    return message


@tool(
    description=(
        "Search long-term memory from earlier sessions. Call this whenever the user "
        "refers to something you do not know — 'what did I tell you', 'the thing we "
        "decided', 'my usual setup' — or before asking the user to repeat themselves. "
        "If nothing matches, it returns the most recent notes instead, so you can see "
        "what the store holds; those may well be irrelevant, and saying so is correct."
    ),
    parameters={
        "query": {"type": "string", "description": "What to look for, in the user's own words. Empty returns the most recent notes."},
        "k": {"type": "integer", "description": "How many notes to return (default 5)"},
    },
    required=["query"],
)
def recall_notes(query: str, k: int = 5) -> str:
    '''
    Return the notes most relevant to a query, best first.

    Args:
        query (str): The lookup, in the user's own words. Matched on latin words
            and CJK bigrams — see memory._terms.
        k (int): Maximum notes to return. Defaults to 5.

    Returns:
        str: The matching notes with their bodies, or the most recent ones when
            nothing matched, or a plain statement that the store is empty.
    '''
    try:
        matched, results = memory.search(query, k)
    except Exception as e:
        return f"Error recalling notes: {type(e).__name__}: {e}"
    return memory.render_recall(matched, results, query)


@tool(
    description=(
        "Delete ONE note from long-term memory, by the id shown in square brackets in the "
        "memory index or in a recall result. Use this when a note turns out to be wrong or "
        "obsolete — do NOT write a correcting note instead, because that leaves both in the "
        "index and every later session reads the index first. To correct a note: delete it, "
        "then write the new one."
    ),
    parameters={
        "id": {"type": "integer", "description": "The id of the single note to delete"},
    },
    required=["id"],
)
def forget_note(id: int) -> str:
    '''
    Delete one note by id. Never more than one, and never by summary or query.

    Args:
        id (int): The note's id, as shown in the memory index or a recall
            result. An id that is not in the store deletes nothing.

    Returns:
        str: What happened, or an explanation when no note carried that id.
    '''
    try:
        deleted, message = memory.forget(id)
    except Exception as e:
        return f"Error forgetting note: {type(e).__name__}: {e}"
    return message
