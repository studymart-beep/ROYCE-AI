"""One shared LLM budget: at most CAP llm attempts per rolling WINDOW per chat.

Every llm attempt reserves a slot BEFORE the provider is called (reserve-before-call), so
failed attempts and retries count too. Who is charged:
  - foreground chat: the chat bound with bind(chat_id) for the message
  - background tasks and retry loops: bind the task's chat_id the same way
  - anything unbound (nudges, jobs, planner without a chat): charged to one shared
    SYSTEM bucket with the same cap, so unbound work can never bypass the limit.

reserve/consume are atomic under one lock: concurrent callers cannot exceed CAP.
Past the cap, consume() raises BudgetExceeded carrying retry_after seconds.
"""
import contextvars, threading, time
from collections import deque

CAP = 30
WINDOW = 3600
SYSTEM = "__system__"
_LOCK = threading.Lock()
_CALLS = {}  # chat key -> deque of timestamps
_CURRENT = contextvars.ContextVar("royce_budget_chat", default=None)


class BudgetExceeded(Exception):
    def __init__(self, retry_after=None):
        self.retry_after = retry_after
        super().__init__(message(retry_after))


def message(retry_after=None):
    if retry_after is None:
        when = "try again in a bit"
    else:
        mins = max(1, int(-(-retry_after // 60)))
        when = f"try again in about {mins} min"
    return f"you've hit the 30 ai replies limit for this hour. {when}, or ask for something smaller."


MESSAGE = message()


def bind(chat_id):
    return _CURRENT.set(chat_id)


def unbind(token):
    _CURRENT.reset(token)


def current():
    return _CURRENT.get()


def _key(chat_id):
    if chat_id is None:
        chat_id = _CURRENT.get()
    return SYSTEM if chat_id is None else chat_id


def _prune(q, now):
    while q and now - q[0] >= WINDOW:
        q.popleft()


def used(chat_id=None, now=None):
    now = now if now is not None else time.time()
    with _LOCK:
        q = _CALLS.get(_key(chat_id), deque())
        _prune(q, now)
        return len(q)


def remaining(chat_id=None, now=None):
    return max(0, CAP - used(chat_id, now))


def consume(now=None, chat_id=None):
    """Reserve one llm attempt for the chat (or the bound chat, or SYSTEM). Raises BudgetExceeded past the cap."""
    key = _key(chat_id)
    now = now if now is not None else time.time()
    with _LOCK:
        q = _CALLS.setdefault(key, deque())
        _prune(q, now)
        if len(q) >= CAP:
            raise BudgetExceeded(max(0.0, WINDOW - (now - q[0])))
        q.append(now)


def reset():
    with _LOCK:
        _CALLS.clear()
