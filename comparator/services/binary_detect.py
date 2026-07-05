# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/binary_detect.py                         ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
Binary File Detection & Byte-Level Helpers
------------------------------------------
Decides whether a file is *binary* (non-text) and provides the cheap
byte-level primitives the rest of the engine builds on.

Detection is two-layered:
  1. Extension fast-path -- files whose extension is in
     BINARY_EXTENSIONS are binary by definition (no read needed).
  2. Content sniff -- anything else is sampled (first 8 KB): a NUL
     byte, or a heavy proportion of non-text control bytes, means
     binary.  High bytes (0x80+) count as text so UTF-8 stays text;
     UTF-16 files contain NULs and classify as binary (same rule git
     uses).

Binary files are opaque to the text pipeline: no comment stripping, no
identifier extraction, and **never** any LLM arbitration -- an LLM
cannot judge two blobs of bytes.  They are matched by exact filename
(directory path as the tie-break clue) and compared byte-for-byte.
"""
import difflib
import filecmp
import logging
import os
from collections import Counter
from typing import List

logger = logging.getLogger(__name__)

# Extensions that are binary by definition (never sniffed).  Note the
# scanner's SKIP_DIRS already prunes build-output dirs (target/, bin/,
# obj/...), so in practice these are *resource* artifacts: icons, jars
# checked into lib folders, keystores, seed databases, fonts.
BINARY_EXTENSIONS = {
    # Images (SVG is XML -> text, deliberately absent)
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.webp', '.tif', '.tiff',
    # Archives / packages
    '.zip', '.jar', '.war', '.ear', '.aar', '.7z', '.rar',
    '.gz', '.tgz', '.tar', '.bz2', '.xz', '.nupkg', '.apk',
    # Compiled / linked artifacts
    '.class', '.exe', '.dll', '.so', '.dylib', '.o', '.obj', '.a', '.lib',
    '.pyd', '.pyc', '.wasm', '.bin',
    # Data / documents
    '.db', '.sqlite', '.sqlite3', '.mdb', '.dat', '.pak', '.pdf',
    # Fonts
    '.ttf', '.otf', '.woff', '.woff2', '.eot',
    # Key stores / certificates (binary formats only -- .pem is text)
    '.jks', '.keystore', '.p12', '.pfx', '.der',
    # Media
    '.mp3', '.wav', '.ogg', '.mp4', '.avi', '.mov', '.webm',
}

# Content sniffing
SNIFF_BYTES = 8192          # sample size for content-based detection
_WEIRD_RATIO = 0.30         # more than this fraction of odd bytes -> binary
# Bytes that are fine in text: BEL/BS/TAB/LF/VT/FF/CR/ESC, printable
# ASCII, and everything >= 0x80 (UTF-8 continuation / Latin-1 accents).
_TEXT_BYTES = frozenset({7, 8, 9, 10, 11, 12, 13, 27}) \
    | frozenset(range(0x20, 0x7F)) | frozenset(range(0x80, 0x100))

# Byte-similarity estimation (Phase 2-BIN reporting only)
BIN_SIM_CAP = 65536         # bytes per side fed into the estimate
_CHUNK = 16                 # chunk width, matches the hex view rows
_MAX_MATCH_COST = 8_000_000  # SequenceMatcher work bound (see _match_cost)


def looks_binary_bytes(sample: bytes) -> bool:
    """Heuristic: does this byte sample look like binary content?"""
    if not sample:
        return False
    if 0 in sample:           # NUL byte: the strongest binary signal
        return True
    weird = sum(1 for b in sample if b not in _TEXT_BYTES)
    return (weird / len(sample)) > _WEIRD_RATIO


def is_binary_file(path: str, extension: str = None) -> bool:
    """True when `path` is a binary (non-text) file.

    Extension fast-path first; unknown extensions are content-sniffed.
    Unreadable files count as text (the rest of the pipeline already
    degrades gracefully on read errors).
    """
    ext = extension if extension is not None else os.path.splitext(path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, 'rb') as fh:
            sample = fh.read(SNIFF_BYTES)
    except OSError:
        return False
    return looks_binary_bytes(sample)


def bytes_equal(path_a: str, path_b: str) -> bool:
    """Exact byte-for-byte equality (size short-circuit, chunked read)."""
    try:
        return filecmp.cmp(path_a, path_b, shallow=False)
    except OSError:
        return False


def read_head(path: str, cap: int) -> bytes:
    """First `cap` bytes of the file, b'' when unreadable."""
    try:
        with open(path, 'rb') as fh:
            return fh.read(cap)
    except OSError:
        logger.warning("Unreadable binary file skipped: %s", path)
        return b''


def _chunks(data: bytes) -> List[bytes]:
    return [data[i:i + _CHUNK] for i in range(0, len(data), _CHUNK)]


def match_cost(a: List[bytes], b: List[bytes]) -> int:
    """Upper bound on SequenceMatcher's inner work over chunk lists.

    difflib is near-linear when elements are mostly unique but degrades
    toward O(L*R) when one value dominates both sides (zero-page-heavy
    binaries -- exactly the case autojunk would mis-handle).  The real
    cost driver is sum over values v of count_a(v) * count_b(v); compute
    it cheaply so callers can fall back to positional pairing.
    """
    if not a or not b:
        return 0
    cb = Counter(b)
    return sum(cnt * cb.get(v, 0) for v, cnt in Counter(a).items())


def binary_similarity(path_a: str, path_b: str, cap: int = BIN_SIM_CAP) -> float:
    """Rough 0-100 byte-level similarity estimate between two files.

    Chunk-level SequenceMatcher over the first `cap` bytes of each side,
    guarded by match_cost (degenerate repetitive files fall back to a
    positional chunk comparison).  Display/reporting only -- matching
    decisions for binaries rest on the exact filename, the directory
    clue, and byte identity, never on this estimate.
    """
    da, db = read_head(path_a, cap), read_head(path_b, cap)
    if da == db:
        return 100.0
    if not da or not db:
        return 0.0
    ca, cb = _chunks(da), _chunks(db)
    if match_cost(ca, cb) > _MAX_MATCH_COST:
        same = sum(1 for x, y in zip(ca, cb) if x == y)
        return round(200.0 * same / (len(ca) + len(cb)), 2)
    ratio = difflib.SequenceMatcher(None, ca, cb, autojunk=False).ratio()
    return round(ratio * 100.0, 2)
