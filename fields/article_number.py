"""
Persistent article number allocation.

This module generates sequential internal article numbers such as "AC00001000".
The next number to allocate is persisted in a JSON state file so that allocations
remain consistent across runs and deployments.

Primary API:
- allocate(count): allocate `count` sequential article numbers and persist the updated counter
- peek_next(): return the next article number without incrementing the counter
- reset(start_value): overwrite the counter (intended for testing/migration only)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class ArticleNumberConfig:
    prefix: str = "AC"
    width: int = 8
    start_next: int = 1000


class ArticleNumberError(RuntimeError):
    """Raised when the state file is invalid or an allocation operation fails."""
    pass


def _project_root() -> Path:
    """Resolve the repository root based on this file's location."""
    return Path(__file__).resolve().parents[1]


def _state_path() -> Path:
    """Return the path of the JSON file that stores the next allocation counter."""
    return _project_root() / "data" / "article_number.json"


def _load_state(state_path: Path, cfg: ArticleNumberConfig) -> int:
    """Load and validate the persisted state and return the next integer to allocate."""
    if not state_path.exists():
        return cfg.start_next

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ArticleNumberError(f"Failed to read/parse state file: {state_path}") from e

    if not isinstance(raw, dict) or "next" not in raw:
        raise ArticleNumberError(
            f"Invalid state format in {state_path}. Expected {{\"next\": <int>}}"
        )

    nxt = raw["next"]
    if not isinstance(nxt, int) or nxt < 0:
        raise ArticleNumberError(f"Invalid 'next' value in {state_path}: {nxt}")

    return nxt


def _save_state(state_path: Path, next_value: int) -> None:
    """Persist the updated next counter to disk (write-temp-then-replace)."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_suffix(".tmp")

    payload = {"next": next_value}
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(state_path)


def format_article_number(n: int, cfg: ArticleNumberConfig = ArticleNumberConfig()) -> str:
    """Format an integer as an article number string like 'AC00001000'."""
    if n < 0:
        raise ArticleNumberError(f"Cannot format negative article number: {n}")
    return f"{cfg.prefix}{n:0{cfg.width}d}"


def allocate(count: int, cfg: ArticleNumberConfig = ArticleNumberConfig()) -> List[str]:
    """Allocate `count` sequential article numbers and persist the updated counter."""
    if not isinstance(count, int) or count <= 0:
        raise ArticleNumberError(f"count must be a positive integer, got: {count}")

    state_path = _state_path()
    current_next = _load_state(state_path, cfg)

    allocated_ints = list(range(current_next, current_next + count))
    allocated = [format_article_number(n, cfg) for n in allocated_ints]

    _save_state(state_path, current_next + count)
    return allocated


def peek_next(cfg: ArticleNumberConfig = ArticleNumberConfig()) -> str:
    """Return the next article number that would be allocated, without incrementing."""
    state_path = _state_path()
    current_next = _load_state(state_path, cfg)
    return format_article_number(current_next, cfg)


def reset(start_value: int = 1000, cfg: ArticleNumberConfig = ArticleNumberConfig()) -> None:
    """Overwrite the persisted counter (intended for testing/migration only)."""
    if start_value < 0:
        raise ArticleNumberError(f"start_value must be non-negative, got: {start_value}")

    _save_state(_state_path(), start_value)
