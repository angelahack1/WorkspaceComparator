# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/file_diff.py                             ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
File Diff Service
-----------------
Beyond-Compare-style structural diff between two files.

Instead of raw difflib opcodes, this service builds an *aligned row
model*: each row pairs a left line with its corresponding right line
(or a gap placeholder).  Inside 'replace' blocks a recursive best-pair
alignment matches *similar* lines to each other, so corresponding lines
sit side by side even when line numbers drift apart.  Modified rows
carry word-level intra-line segments so the UI can highlight exactly
which parts of a line changed.

Row shapes (JSON-friendly dicts):
  {'t': 'eq',  'l': <lineno>, 'r': <lineno>}                  equal line
  {'t': 'mod', 'l': .., 'r': .., 'ls': [...], 'rs': [...]}    modified pair
  {'t': 'mod', 'l': .., 'r': .., 'm': 1}                      whitespace-only pair
  {'t': 'del', 'l': ..[, 'm': 1]}                             left-only line
  {'t': 'add', 'r': ..[, 'm': 1]}                             right-only line

'ls'/'rs' are lists of [text, changed] word-level segments.
'm': 1 flags a *minor* row (whitespace-only change or blank line).
Line numbers are 1-based; a missing side means "render a gap".

HEX (binary) mode -- compute_hex_diff() -- reuses the same row shapes,
but 'l'/'r' are 1-based indices of 16-byte rows (hexdump -C style) into
base64-encoded byte windows; the UI formats the hexdump text and the
per-byte highlighting itself.  There is no 'm' and no 'ls'/'rs' in hex
rows.  See the "Hex (binary) comparison" section below.
"""
import base64
import difflib
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

from .binary_detect import (
    bytes_equal,
    is_binary_file,
    match_cost,
    read_head,
    read_text_file,
)

logger = logging.getLogger(__name__)

# Word-level tokenizer for intra-line diffs: words, whitespace runs, punctuation
_TOKEN_RE = re.compile(r'\w+|\s+|[^\w\s]')

# Alignment tuning
PAIR_THRESHOLD = 0.5    # minimum SequenceMatcher ratio for two lines to pair up
MAX_PAIR_AREA = 2500    # L*R above this -> cheap sequential pairing (perf guard)

# Hex (binary) comparison tuning
HEX_BYTES_PER_ROW = 16        # canonical hexdump -C row width
HEX_VIEW_MAX_BYTES = 131072   # 128 KB rendered per side; the rest is reported, not drawn
_HEX_MAX_MATCH_COST = 8_000_000  # SequenceMatcher work bound -> offset pairing fallback


def _read_file(path: str, encoding: str = 'auto') -> str:
    """Read any content-sniffed text file, regardless of extension."""
    return read_text_file(path, encoding)


def _file_meta(path: str) -> Dict[str, Any]:
    """Size + modification time for the file info bars."""
    try:
        st = os.stat(path)
        return {
            'size': st.st_size,
            'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
        }
    except OSError:
        return {'size': None, 'mtime': None}


# ===================================================================
# Intra-line (word-level) segmentation
# ===================================================================

def _inline_segments(a: str, b: str) -> Tuple[List[list], List[list]]:
    """
    Word-level diff of two lines.

    Returns (left_segments, right_segments) where each segment is
    [text, changed] and changed is 0/1.
    """
    ta = _TOKEN_RE.findall(a)
    tb = _TOKEN_RE.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    aseg: List[list] = []
    bseg: List[list] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        at = ''.join(ta[i1:i2])
        bt = ''.join(tb[j1:j2])
        if tag == 'equal':
            if at:
                aseg.append([at, 0])
                bseg.append([bt, 0])
        else:
            if at:
                aseg.append([at, 1])
            if bt:
                bseg.append([bt, 1])
    return aseg, bseg


# ===================================================================
# Row builders
# ===================================================================

def _is_ws_only(a: str, b: str) -> bool:
    """True when the two lines differ only in whitespace."""
    return ''.join(a.split()) == ''.join(b.split())


def _mod_row(ll: List[str], rl: List[str], i: int, j: int) -> Dict[str, Any]:
    a, b = ll[i], rl[j]
    if a == b:
        return {'t': 'eq', 'l': i + 1, 'r': j + 1}
    row: Dict[str, Any] = {'t': 'mod', 'l': i + 1, 'r': j + 1}
    if _is_ws_only(a, b):
        row['m'] = 1
    else:
        row['ls'], row['rs'] = _inline_segments(a, b)
    return row


def _del_row(ll: List[str], i: int) -> Dict[str, Any]:
    row: Dict[str, Any] = {'t': 'del', 'l': i + 1}
    if not ll[i].strip():
        row['m'] = 1
    return row


def _add_row(rl: List[str], j: int) -> Dict[str, Any]:
    row: Dict[str, Any] = {'t': 'add', 'r': j + 1}
    if not rl[j].strip():
        row['m'] = 1
    return row


# ===================================================================
# Replace-block alignment
# ===================================================================

def _sequential(ll, rl, i1, i2, j1, j2) -> List[Dict[str, Any]]:
    """
    Fallback pairing: zip lines in order, leftovers become add/del.

    Pairs with almost nothing in common (e.g. a code line facing a blank
    line) are NOT forced into a 'mod' pair -- they are emitted as stacked
    del/add rows instead, which is what BeyondCompare does.
    """
    rows: List[Dict[str, Any]] = []
    pend_del: List[Dict[str, Any]] = []
    pend_add: List[Dict[str, Any]] = []

    def flush():
        rows.extend(pend_del)
        rows.extend(pend_add)
        del pend_del[:], pend_add[:]

    n = min(i2 - i1, j2 - j1)
    for k in range(n):
        i, j = i1 + k, j1 + k
        a, b = ll[i], rl[j]
        ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        if ratio >= 0.3 or a == b or _is_ws_only(a, b):
            flush()
            rows.append(_mod_row(ll, rl, i, j))
        else:
            pend_del.append(_del_row(ll, i))
            pend_add.append(_add_row(rl, j))
    for i in range(i1 + n, i2):
        pend_del.append(_del_row(ll, i))
    for j in range(j1 + n, j2):
        pend_add.append(_add_row(rl, j))
    flush()
    return rows


def _align_replace(ll, rl, i1, i2, j1, j2) -> List[Dict[str, Any]]:
    """
    Align the lines of a 'replace' block by content similarity.

    Recursively finds the best-matching (left, right) line pair above
    PAIR_THRESHOLD, anchors on it, and aligns the sub-blocks on either
    side.  Lines with no good partner become add/del gap rows, which is
    what makes corresponding lines face each other in the UI.
    """
    L, R = i2 - i1, j2 - j1
    if L == 0:
        return [_add_row(rl, j) for j in range(j1, j2)]
    if R == 0:
        return [_del_row(ll, i) for i in range(i1, i2)]
    if L * R > MAX_PAIR_AREA:
        return _sequential(ll, rl, i1, i2, j1, j2)

    best_ratio, bi, bj = PAIR_THRESHOLD, -1, -1
    for i in range(i1, i2):
        a = ll[i]
        for j in range(j1, j2):
            sm = difflib.SequenceMatcher(None, a, rl[j], autojunk=False)
            # Cheap upper bounds first; skip hopeless candidates fast
            if sm.real_quick_ratio() <= best_ratio or sm.quick_ratio() <= best_ratio:
                continue
            ratio = sm.ratio()
            if ratio > best_ratio:
                best_ratio, bi, bj = ratio, i, j

    if bi < 0:
        # Nothing similar enough to anchor on
        return _sequential(ll, rl, i1, i2, j1, j2)

    return (
        _align_replace(ll, rl, i1, bi, j1, bj)
        + [_mod_row(ll, rl, bi, bj)]
        + _align_replace(ll, rl, bi + 1, i2, bj + 1, j2)
    )


# ===================================================================
# Public API
# ===================================================================

def compute_file_diff(
    left_path: str,
    right_path: str,
    left_encoding: str = 'auto',
    right_encoding: str = 'auto',
) -> Dict[str, Any]:
    """
    Compute an aligned, BeyondCompare-style diff between two files.

    Returns a dict with:
      - left_lines / right_lines : raw lines of each file
      - rows                     : aligned row model (see module docstring)
      - left_meta / right_meta   : {size, mtime} for the file info bars
      - left_path / right_path
    """
    left_lines = _read_file(left_path, left_encoding).splitlines(keepends=False)
    right_lines = _read_file(right_path, right_encoding).splitlines(keepends=False)

    sm = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)

    rows: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                rows.append({'t': 'eq', 'l': i1 + k + 1, 'r': j1 + k + 1})
        elif tag == 'delete':
            for i in range(i1, i2):
                rows.append(_del_row(left_lines, i))
        elif tag == 'insert':
            for j in range(j1, j2):
                rows.append(_add_row(right_lines, j))
        else:  # replace -> smart alignment
            rows.extend(_align_replace(left_lines, right_lines, i1, i2, j1, j2))

    return {
        'left_lines': left_lines,
        'right_lines': right_lines,
        'rows': rows,
        'left_path': left_path,
        'right_path': right_path,
        'left_encoding': left_encoding,
        'right_encoding': right_encoding,
        'left_meta': _file_meta(left_path),
        'right_meta': _file_meta(right_path),
    }


# ===================================================================
# Hex (binary) comparison
# ===================================================================
# Binary files get a hexdump -C style aligned comparison instead of a
# text diff.  Both files are chunked into 16-byte rows; the rows are
# aligned with SequenceMatcher over the chunk lists (so inserted /
# deleted 16-byte-multiple blocks produce proper gaps), and 'replace'
# blocks are zipped positionally -- rows are fixed-width, so there is
# nothing smarter to anchor on.  Per-byte change highlighting is
# computed by the UI from the paired rows.
#
# Rendering is capped at HEX_VIEW_MAX_BYTES per side (the UI shows a
# truncation notice); full-file byte equality is still computed on the
# whole files, so 'identical' is always truthful.  Non-16-multiple
# insertions shift every following row and degrade to a long run of
# 'mod' rows -- accepted; BC-grade byte-level realignment is not worth
# the complexity for artifact comparison.


def _read_bytes_window(path: str) -> bytes:
    return read_head(path, HEX_VIEW_MAX_BYTES)


def _hex_chunks(data: bytes) -> List[bytes]:
    return [data[i:i + HEX_BYTES_PER_ROW]
            for i in range(0, len(data), HEX_BYTES_PER_ROW)]


def _hex_rows(lchunks: List[bytes], rchunks: List[bytes]) -> List[Dict[str, Any]]:
    """Aligned hex row model (1-based 16-byte row indices)."""
    n_l, n_r = len(lchunks), len(rchunks)
    if match_cost(lchunks, rchunks) <= _HEX_MAX_MATCH_COST:
        sm = difflib.SequenceMatcher(None, lchunks, rchunks, autojunk=False)
        opcodes = sm.get_opcodes()
    else:
        # Degenerate repetition (zero-page-heavy files): difflib would
        # crawl, and alignment of low-information rows means little --
        # pair rows by offset instead.
        opcodes = [('replace', 0, n_l, 0, n_r)] if (n_l or n_r) else []

    rows: List[Dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            for k in range(i2 - i1):
                rows.append({'t': 'eq', 'l': i1 + k + 1, 'r': j1 + k + 1})
        elif tag == 'delete':
            for i in range(i1, i2):
                rows.append({'t': 'del', 'l': i + 1})
        elif tag == 'insert':
            for j in range(j1, j2):
                rows.append({'t': 'add', 'r': j + 1})
        else:  # replace: positional zip, leftovers become one-sided rows
            n = min(i2 - i1, j2 - j1)
            for k in range(n):
                i, j = i1 + k, j1 + k
                t = 'eq' if lchunks[i] == rchunks[j] else 'mod'
                rows.append({'t': t, 'l': i + 1, 'r': j + 1})
            for i in range(i1 + n, i2):
                rows.append({'t': 'del', 'l': i + 1})
            for j in range(j1 + n, j2):
                rows.append({'t': 'add', 'r': j + 1})
    return rows


def _file_size(path: str) -> int:
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def compute_hex_diff(left_path: str, right_path: str) -> Dict[str, Any]:
    """
    Hexdump-style aligned comparison of two (binary) files.

    Returns a dict with:
      - hex: True                  : marks the payload shape for the UI
      - rows                       : aligned row model; 'l'/'r' are 1-based
                                     16-byte row indices into the windows
      - left_b64 / right_b64       : base64 of the rendered byte windows
      - identical                  : FULL-file byte equality (never lies,
                                     even when the view is truncated)
      - truncated {left, right}    : window smaller than the file?
      - left_total / right_total   : full file sizes in bytes
      - left_binary / right_binary : content-level binary detection per side
      - left_meta / right_meta     : {size, mtime} for the file info bars
      - left_path / right_path
    """
    lb = _read_bytes_window(left_path)
    rb = _read_bytes_window(right_path)
    l_total, r_total = _file_size(left_path), _file_size(right_path)
    identical = (l_total == r_total) and bytes_equal(left_path, right_path)

    lchunks, rchunks = _hex_chunks(lb), _hex_chunks(rb)
    if identical or lb == rb:
        rows = [{'t': 'eq', 'l': i + 1, 'r': i + 1} for i in range(len(lchunks))]
    else:
        rows = _hex_rows(lchunks, rchunks)

    return {
        'hex': True,
        'rows': rows,
        'left_b64': base64.b64encode(lb).decode('ascii'),
        'right_b64': base64.b64encode(rb).decode('ascii'),
        'identical': identical,
        'truncated': {'left': l_total > len(lb), 'right': r_total > len(rb)},
        'left_total': l_total,
        'right_total': r_total,
        'left_binary': is_binary_file(left_path),
        'right_binary': is_binary_file(right_path),
        'left_meta': _file_meta(left_path),
        'right_meta': _file_meta(right_path),
        'left_path': left_path,
        'right_path': right_path,
    }


def compute_hex_single(path: str) -> Dict[str, Any]:
    """Hex payload for one file (unmatched single-file mode)."""
    data = _read_bytes_window(path)
    total = _file_size(path)
    return {
        'b64': base64.b64encode(data).decode('ascii'),
        'truncated': total > len(data),
        'total': total,
        'binary': is_binary_file(path),
        'meta': _file_meta(path),
    }
