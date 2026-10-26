"""Per-chat LLM budget: at most CAP calls per rolling hour per chat, counted in memory.

The bot binds the active chat with bind(chat_id) for the duration of a message. Every
llm call made inside that scope calls consume(). Past the cap, consume() raises
BudgetExceeded and the bot replies with a plain message. Unbound calls (nudges,
background jobs) are not counted against any chat.
"""
import contextvars, threading, time
from collections import deque

CAP = 30
WINDOW = 3600
_LOCK = threading.Lock()
_CALLS = {}  # chat_id -> deque of timestamps
_CURRENT = contextvars.ContextVar("royce_budget_chat", default=None)


class BudgetExceeded(Exception):
    pass


MESSAGE = "you've hit the 30 ai replies limit for this hour. try again in a bit, or ask for something smaller."


def bind(chat_id):
    return _CURRENT.set(chat_id)


def unbind(token):
    _CURRENT.reset(token)


def current():
    return _CURRENT.get()


def _prune(q, now):
    while q and now - q[0] >= WINDOW:
        q.popleft()


def used(chat_id, now=None):
    now = now if now is not None else time.time()
    with _LOCK:
        q = _CALLS.get(chat_id, deque())
        _prune(q, now)
        return len(q)


def consume(now=None, chat_id=None):
    """Count one llm call for the bound chat (or chat_id). Raises BudgetExceeded past the cap."""
    cid = chat_id if chat_id is not None else _CURRENT.get()
    if cid is None:
        return
    now = now if now is not None else time.time()
    with _LOCK:
        q = _CALLS.setdefault(cid, deque())
        _prune(q, now)
        if len(q) >= CAP:
            raise BudgetExceeded(MESSAGE)
        q.append(now)


def reset():
    with _LOCK:
        _CALLS.clear()
