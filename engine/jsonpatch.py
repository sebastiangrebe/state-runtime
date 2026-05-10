"""Tiny RFC 6902 applier — replace/add/remove on JSON Pointer paths.

We don't pull in `python-jsonpatch` because we only need three ops on
fully-deserialised dicts/lists, and we want explicit control over invalid
paths (model can hallucinate them).
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Sequence


class PatchError(Exception):
    pass


def _decode(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _walk(doc: Any, parts: Sequence[str]) -> tuple[Any, str]:
    """Walk to the parent container, return (parent, last_token)."""
    if not parts:
        raise PatchError("empty path is unsupported (whole-doc replace not allowed)")
    target = doc
    for part in parts[:-1]:
        token = _decode(part)
        if isinstance(target, list):
            try:
                idx = int(token)
            except ValueError as e:
                raise PatchError(f"non-int index into list: {token!r}") from e
            try:
                target = target[idx]
            except IndexError as e:
                raise PatchError(f"index out of range at {token!r}") from e
        elif isinstance(target, dict):
            if token not in target:
                raise PatchError(f"missing key {token!r}")
            target = target[token]
        else:
            raise PatchError(f"cannot descend into {type(target).__name__} at {token!r}")
    return target, _decode(parts[-1])


def apply_patch(doc: Any, ops: list[dict[str, Any]]) -> Any:
    """Mutate a *copy* of `doc` according to `ops`, return it.

    `ops` is a list of `{op, path, value?}` dicts (already model_dump'd).
    """
    out = deepcopy(doc)
    for op in ops:
        kind = op["op"]
        path = op.get("path", "")
        if not path.startswith("/"):
            raise PatchError(f"path must begin with '/': {path!r}")
        parts = path.split("/")[1:]
        parent, last = _walk(out, parts)

        if kind == "replace":
            if isinstance(parent, list):
                idx = int(last)
                parent[idx] = op.get("value")
            elif isinstance(parent, dict):
                if last not in parent:
                    raise PatchError(f"replace on missing key {last!r}")
                parent[last] = op.get("value")
            else:
                raise PatchError(f"cannot replace on {type(parent).__name__}")

        elif kind == "add":
            if isinstance(parent, list):
                if last == "-":
                    parent.append(op.get("value"))
                else:
                    parent.insert(int(last), op.get("value"))
            elif isinstance(parent, dict):
                parent[last] = op.get("value")
            else:
                raise PatchError(f"cannot add on {type(parent).__name__}")

        elif kind == "remove":
            if isinstance(parent, list):
                del parent[int(last)]
            elif isinstance(parent, dict):
                if last not in parent:
                    raise PatchError(f"remove on missing key {last!r}")
                del parent[last]
            else:
                raise PatchError(f"cannot remove on {type(parent).__name__}")

        else:
            raise PatchError(f"unsupported op {kind!r}")
    return out
