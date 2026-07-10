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

Detection is content-first.  Every extension, including an unknown or
missing extension, is sampled.  BOMs and common BOM-less UTF-16/UTF-32
layouts are recognized as text; otherwise NUL bytes or a heavy
proportion of non-text control bytes mean binary.  The extension
catalogue is only documentation/a useful hint for callers -- it never
overrides bytes that are demonstrably text.

Binary files are opaque to the text pipeline: no comment stripping, no
identifier extraction, and **never** any LLM arbitration -- an LLM
cannot judge two blobs of bytes.  They are matched by exact filename
(directory path as the tie-break clue) and compared byte-for-byte.
"""
import codecs
import difflib
import filecmp
import logging
from collections import Counter
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Common binary extensions.  This is deliberately NOT an allow/deny
# list: is_binary_file() always inspects content, so a custom text file
# named example.jar remains text and an unknown example.xyz containing
# binary bytes remains binary.
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

_BOM_ENCODINGS = (
    (codecs.BOM_UTF32_LE, 'utf-32'),
    (codecs.BOM_UTF32_BE, 'utf-32'),
    (codecs.BOM_UTF8, 'utf-8-sig'),
    (codecs.BOM_UTF16_LE, 'utf-16'),
    (codecs.BOM_UTF16_BE, 'utf-16'),
)

SUPPORTED_TEXT_ENCODINGS = (
    'auto',
    'utf-8',
    'utf-8-sig',
    'utf-16',
    'utf-16-le',
    'utf-16-be',
    'utf-32',
    'utf-32-le',
    'utf-32-be',
    'cp1252',
    'latin-1',
    'ascii',
    'shift_jis',
    'gb18030',
    'big5',
    'euc-kr',
)

_ENCODING_ALIASES = {
    'automatic': 'auto',
    'utf8': 'utf-8',
    'utf8-sig': 'utf-8-sig',
    'utf16': 'utf-16',
    'utf16le': 'utf-16-le',
    'utf16be': 'utf-16-be',
    'utf32': 'utf-32',
    'utf32le': 'utf-32-le',
    'utf32be': 'utf-32-be',
    'windows-1252': 'cp1252',
    'windows1252': 'cp1252',
    'latin1': 'latin-1',
    'iso-8859-1': 'latin-1',
    'sjis': 'shift_jis',
    'shift-jis': 'shift_jis',
}

# Byte-similarity estimation (Phase 2-BIN reporting only)
BIN_SIM_CAP = 65536         # bytes per side fed into the estimate
_CHUNK = 16                 # chunk width, matches the hex view rows
_MAX_MATCH_COST = 8_000_000  # SequenceMatcher work bound (see _match_cost)


def _zero_ratio(sample: bytes, offset: int, stride: int) -> float:
    lane = sample[offset::stride]
    return (lane.count(0) / len(lane)) if lane else 0.0


def _unicode_text_encoding(sample: bytes):
    """Return a likely Unicode encoding for NUL-bearing text."""
    for bom, encoding in _BOM_ENCODINGS:
        if sample.startswith(bom):
            return encoding

    if len(sample) >= 8:
        lanes4 = [_zero_ratio(sample, i, 4) for i in range(4)]
        if min(lanes4[1:]) > 0.60 and lanes4[0] < 0.20:
            return 'utf-32-le'
        if min(lanes4[:3]) > 0.60 and lanes4[3] < 0.20:
            return 'utf-32-be'

        even = _zero_ratio(sample, 0, 2)
        odd = _zero_ratio(sample, 1, 2)
        if odd > 0.45 and even < 0.20:
            return 'utf-16-le'
        if even > 0.45 and odd < 0.20:
            return 'utf-16-be'
    return None


def _decodes_as_readable_text(sample: bytes, encoding: str) -> bool:
    try:
        text = sample.decode(encoding, errors='replace')
    except (LookupError, UnicodeError):
        return False
    if not text:
        return True
    readable = sum(
        1 for ch in text
        if ch.isprintable() or ch in '\n\r\t\f\b'
    )
    return readable / len(text) >= 0.80


def looks_binary_bytes(sample: bytes) -> bool:
    """Heuristic: does this byte sample look like non-text content?"""
    if not sample:
        return False

    unicode_encoding = _unicode_text_encoding(sample)
    if unicode_encoding and _decodes_as_readable_text(sample, unicode_encoding):
        return False

    if 0 in sample:
        return True
    weird = sum(1 for b in sample if b not in _TEXT_BYTES)
    return (weird / len(sample)) > _WEIRD_RATIO


def normalize_text_encoding(value) -> str:
    """Return a supported charset name, defaulting invalid values to auto."""
    if not isinstance(value, str):
        return 'auto'
    key = value.strip().lower().replace('_', '-')[:40]
    key = _ENCODING_ALIASES.get(key, key)
    return key if key in SUPPORTED_TEXT_ENCODINGS else 'auto'


def normalize_charsets(value) -> Dict[str, str]:
    """Normalize the API/UI per-side charset override object."""
    if not isinstance(value, dict):
        value = {}
    return {
        'left': normalize_text_encoding(value.get('left', 'auto')),
        'right': normalize_text_encoding(value.get('right', 'auto')),
    }


def detect_text_encoding(sample: bytes) -> str:
    """Best-effort encoding label for bytes already classified as text."""
    unicode_encoding = _unicode_text_encoding(sample)
    if unicode_encoding:
        return unicode_encoding

    try:
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        decoder.decode(sample, final=False)
        return 'utf-8'
    except UnicodeError:
        pass

    try:
        sample.decode('cp1252')
        return 'cp1252'
    except UnicodeError:
        return 'latin-1'


def inspect_file(
    path: str,
    extension: str = None,
    text_encoding: str = 'auto',
) -> Tuple[bool, str]:
    """Return (is_binary, effective_text_encoding) from actual content.

    A charset override controls decoding only after the content has
    passed the binary sniff.  It can never force a true binary into the
    text or LLM pipeline.
    """
    _ = extension
    try:
        with open(path, 'rb') as fh:
            sample = fh.read(SNIFF_BYTES)
    except OSError:
        return False, normalize_text_encoding(text_encoding)

    if looks_binary_bytes(sample):
        return True, ''
    requested = normalize_text_encoding(text_encoding)
    effective = detect_text_encoding(sample) if requested == 'auto' else requested
    return False, effective


def is_binary_file(path: str, extension: str = None) -> bool:
    """True when `path` is a binary (non-text) file.

    The extension argument is retained for API compatibility but never
    gates comparison.  Every readable file is content-sniffed.  An
    unreadable file counts as text so the rest of the pipeline can
    report it without silently dropping it.
    """
    return inspect_file(path, extension)[0]


def normalize_newlines(text: str) -> str:
    """Canonicalize CRLF, CR, and LF to LF for logical text comparison."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def decode_text_bytes(data: bytes, encoding: str = 'auto') -> str:
    """Decode bytes already classified as text.

    Unicode BOMs and common BOM-less UTF-16/32 layouts are handled
    first.  The final Latin-1 fallback is lossless, which keeps unusual
    legacy text visible and comparable.
    """
    requested = normalize_text_encoding(encoding)
    effective = detect_text_encoding(data[:SNIFF_BYTES]) \
        if requested == 'auto' else requested
    if effective:
        try:
            text = data.decode(effective)
        except UnicodeError:
            try:
                text = data.decode(effective, errors='replace')
            except UnicodeError:
                text = data.decode('latin-1')
        return normalize_newlines(text)
    return ''


def read_text_file(path: str, encoding: str = 'auto') -> str:
    """Read any text-mode file and normalize its line endings."""
    try:
        with open(path, 'rb') as fh:
            return decode_text_bytes(fh.read(), encoding)
    except OSError:
        logger.warning("Unreadable text file skipped: %s", path)
        return ''


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
