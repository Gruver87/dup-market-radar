from __future__ import annotations

from typing import Any, Dict, List


def rank_top(candidates: List[Dict[str, Any]], n: int = 10) -> List[Dict[str, Any]]:
    def key(c: Dict[str, Any]) -> int:
        if c.get("score_centi") is not None:
            return int(c["score_centi"])
        # legacy float score → reject path shouldn't happen
        return 0

    ordered = sorted(candidates, key=key, reverse=True)
    seen = set()
    out: List[Dict[str, Any]] = []
    for c in ordered:
        k = (c.get("venue"), c.get("symbol"), c.get("setup"))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
        if len(out) >= n:
            break
    return out
