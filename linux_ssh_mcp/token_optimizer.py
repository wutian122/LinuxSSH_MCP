from __future__ import annotations

import re


class TokenOptimizer:
    def filter_by_pattern(self, text: str, *, pattern: str) -> str:
        compiled = re.compile(pattern)
        lines = text.splitlines()
        filtered = [line for line in lines if compiled.search(line) is not None]
        return "\n".join(filtered)

    def estimate_tokens(self, text: str) -> int:
        if not text:
            return 0

        cjk_count = 0
        other_count = 0
        for ch in text:
            code = ord(ch)
            if (
                0x4E00 <= code <= 0x9FFF
                or 0x3400 <= code <= 0x4DBF
                or 0x20000 <= code <= 0x2A6DF
                or 0x2A700 <= code <= 0x2B73F
                or 0x2B740 <= code <= 0x2B81F
                or 0x2B820 <= code <= 0x2CEAF
            ):
                cjk_count += 1
            else:
                other_count += 1

        return cjk_count + max(1, other_count // 4)

    def truncate_by_tokens(self, text: str, *, max_tokens: int, suffix: str = "...") -> str:
        if max_tokens <= 0:
            return ""
        if self.estimate_tokens(text) <= max_tokens:
            return text

        lo = 0
        hi = len(text)
        while lo < hi:
            mid = (lo + hi) // 2
            candidate = text[:mid] + suffix
            if self.estimate_tokens(candidate) <= max_tokens:
                lo = mid + 1
            else:
                hi = mid

        cut = max(0, lo - 1)
        if cut <= 0:
            return suffix if self.estimate_tokens(suffix) <= max_tokens else ""
        return text[:cut] + suffix

