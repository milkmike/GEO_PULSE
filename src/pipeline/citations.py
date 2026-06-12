"""Citation post-processing for AI briefs.

The LLM is shown numbered headlines [1..N] and asked to cite them.
After generation we strip citation markers that reference numbers we
never gave it (hallucinated), and report which numbers were used.
"""
import re
from typing import Set, Tuple

_CITE_RE = re.compile(r"\[(\d+)\]")


def apply_citations(content: str, valid_numbers: Set[int]) -> Tuple[str, Set[int]]:
    """Strip phantom [n] markers; return (cleaned content, used numbers)."""
    used: Set[int] = set()

    def repl(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        if n in valid_numbers:
            used.add(n)
            return m.group(0)
        return ""

    return _CITE_RE.sub(repl, content), used
