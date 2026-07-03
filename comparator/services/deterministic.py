# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/deterministic.py                         ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
Deterministic Similarity Comparison Algorithm
----------------------------------------------
Language-aware source code comparison that strips comments, normalizes
whitespace, extracts structural identifiers, and computes a weighted
similarity score.  This is the first-pass comparison engine invoked
before falling back to the LLM.

The algorithm works in layers:
  1. Strip comments (language-aware: C-style, Python-style)
  2. Remove string literals to focus on code structure
  3. Normalize whitespace according to language rules
  4. Tokenize and compare with SequenceMatcher
  5. Extract structural identifiers (class/function names) and compare
  6. Return a weighted similarity percentage and confidence level
"""
import re
import difflib
from typing import Tuple, Set

# ---------------------------------------------------------------------------
# Comment / string patterns
# ---------------------------------------------------------------------------
_C_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)
_C_LINE_COMMENT = re.compile(r'//.*?$', re.MULTILINE)
_PY_BLOCK_STRING = re.compile(r'(\'\'\'.*?\'\'\'|""".*?""")', re.DOTALL)
_PY_LINE_COMMENT = re.compile(r'#.*?$', re.MULTILINE)
_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')

# ---------------------------------------------------------------------------
# Structural-identifier patterns (per language family)
# ---------------------------------------------------------------------------
_JAVA_CLASS = re.compile(
    r'\b(?:class|interface|enum|record)\s+(\w+)')
_JAVA_METHOD = re.compile(
    r'(?:public|private|protected|static|final|abstract|synchronized|native'
    r'|\s)+[\w<>\[\],\s]+\s+(\w+)\s*\(')
_C_FUNCTION = re.compile(r'\b(\w+)\s*\([^)]*\)\s*\{')
_RUST_FN = re.compile(r'\bfn\s+(\w+)')
_RUST_STRUCT = re.compile(r'\b(?:struct|enum|trait)\s+(\w+)')
_RUST_IMPL = re.compile(r'\bimpl(?:<[^>]+>)?\s+(\w+)')
_GO_FUNC = re.compile(
    r'\bfunc\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)')
_PY_CLASS = re.compile(r'\bclass\s+(\w+)')
_PY_DEF = re.compile(r'\bdef\s+(\w+)')
_JS_FUNC = re.compile(r'\bfunction\s+(\w+)')
_JS_CLASS = re.compile(r'\bclass\s+(\w+)')
_JS_ARROW = re.compile(
    r'(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>|\w+\s*=>)')

# Extension sets
_C_FAMILY = {
    '.c', '.h', '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx',
    '.java', '.cs', '.go', '.js', '.jsx', '.ts', '.tsx',
    '.kt', '.kts', '.scala', '.swift', '.m', '.mm', '.rs',
    '.gradle',
}
_PYTHON = {'.py'}

# Noise identifiers to discard
_NOISE = frozenset({
    'main', 'this', 'self', 'new', 'return', 'if', 'else', 'for',
    'while', 'do', 'switch', 'case', 'break', 'continue', 'try',
    'catch', 'finally', 'throw', 'throws', 'public', 'private',
    'protected', 'static', 'void', 'int', 'string', 'bool',
    'true', 'false', 'null', 'None', 'undefined', 'var', 'let',
    'const', 'get', 'set', 'toString', 'hashCode', 'equals',
})


# ===================================================================
# Public helpers
# ===================================================================

def compute_filename_similarity(name1: str, name2: str) -> float:
    """Return 0-1 similarity ratio between two filenames (ignoring extension)."""
    base1 = name1.rsplit('.', 1)[0].lower()
    base2 = name2.rsplit('.', 1)[0].lower()
    return difflib.SequenceMatcher(None, base1, base2).ratio()


def compute_content_status(content1: str, content2: str, extension: str) -> str:
    """
    Classify the relationship between two file contents.

    Returns
    -------
    'identical' : raw content is exactly the same
    'minor'     : differs only in whitespace / comments / ignored tokens
    'different' : functionally different code
    """
    if content1 == content2:
        return 'identical'

    clean1 = _strip_comments(content1, extension)
    clean2 = _strip_comments(content2, extension)

    clean1 = _STRING_LITERAL.sub('""', clean1)
    clean2 = _STRING_LITERAL.sub('""', clean2)

    norm1 = _normalize(clean1)
    norm2 = _normalize(clean2)

    if norm1 == norm2:
        return 'minor'

    return 'different'


def compute_similarity(
    content1: str,
    content2: str,
    extension: str,
) -> Tuple[float, str]:
    """
    Deterministic similarity comparison of two source files.

    Returns
    -------
    (similarity_pct, confidence)
        similarity_pct : float in [0, 100]
        confidence      : 'high' | 'medium' | 'low'
    """
    # Edge cases
    if not content1.strip() and not content2.strip():
        return 100.0, 'high'
    if not content1.strip() or not content2.strip():
        return 0.0, 'high'

    # 1. Strip comments
    clean1 = _strip_comments(content1, extension)
    clean2 = _strip_comments(content2, extension)

    # 2. Replace string literals with placeholder
    clean1 = _STRING_LITERAL.sub('""', clean1)
    clean2 = _STRING_LITERAL.sub('""', clean2)

    # 3. Normalize whitespace
    norm1 = _normalize(clean1)
    norm2 = _normalize(clean2)

    # Quick identity check
    if norm1 == norm2:
        return 100.0, 'high'

    # 4. Token-level similarity
    tokens1 = _tokenize(norm1)
    tokens2 = _tokenize(norm2)

    if not tokens1 and not tokens2:
        return 100.0, 'high'
    if not tokens1 or not tokens2:
        return 0.0, 'high'

    token_sim = difflib.SequenceMatcher(None, tokens1, tokens2).ratio() * 100

    # 5. Structural identifier similarity
    ids1 = _extract_identifiers(content1, extension)
    ids2 = _extract_identifiers(content2, extension)

    if ids1 and ids2:
        common = ids1 & ids2
        union = ids1 | ids2
        struct_sim = (len(common) / len(union)) * 100
        # Weighted blend: 60 % token structure, 40 % identifier overlap
        similarity = 0.60 * token_sim + 0.40 * struct_sim
    else:
        similarity = token_sim

    similarity = round(min(similarity, 100.0), 2)

    if similarity > 85:
        confidence = 'high'
    elif similarity > 40:
        confidence = 'medium'
    else:
        confidence = 'low'

    return similarity, confidence


# ===================================================================
# Internal helpers
# ===================================================================

def _strip_comments(content: str, ext: str) -> str:
    if ext in _C_FAMILY:
        content = _C_BLOCK_COMMENT.sub('', content)
        content = _C_LINE_COMMENT.sub('', content)
    elif ext in _PYTHON:
        content = _PY_BLOCK_STRING.sub('', content)
        content = _PY_LINE_COMMENT.sub('', content)
    return content


def _normalize(text: str) -> str:
    lines = []
    for line in text.split('\n'):
        stripped = line.strip()
        if stripped:
            stripped = re.sub(r'\s+', ' ', stripped)
            lines.append(stripped)
    return '\n'.join(lines)


def _tokenize(text: str) -> list:
    return re.findall(r'\w+|[^\w\s]', text)


def _extract_identifiers(content: str, ext: str) -> Set[str]:
    ids: Set[str] = set()

    if ext in {'.java', '.cs', '.kt', '.kts', '.scala'}:
        ids.update(_JAVA_CLASS.findall(content))
        ids.update(_JAVA_METHOD.findall(content))
    elif ext in {'.c', '.h', '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx'}:
        ids.update(_JAVA_CLASS.findall(content))  # C++ classes
        ids.update(_C_FUNCTION.findall(content))
    elif ext == '.rs':
        ids.update(_RUST_FN.findall(content))
        ids.update(_RUST_STRUCT.findall(content))
        ids.update(_RUST_IMPL.findall(content))
    elif ext == '.go':
        ids.update(_GO_FUNC.findall(content))
    elif ext == '.py':
        ids.update(_PY_CLASS.findall(content))
        ids.update(_PY_DEF.findall(content))
    elif ext in {'.js', '.jsx', '.ts', '.tsx'}:
        ids.update(_JS_FUNC.findall(content))
        ids.update(_JS_CLASS.findall(content))
        ids.update(_JS_ARROW.findall(content))

    return ids - _NOISE
