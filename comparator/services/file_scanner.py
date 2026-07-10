# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/file_scanner.py                          ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
File Scanner Module
-------------------
Recursively scans directories for every file, collecting metadata about
each file for the correspondence engine.  Extensions never gate the
scan: content-sniffed text files use deterministic/LLM comparison and
content-sniffed binary files use byte/hex comparison.

Supports user-defined exclusions (the UI's Exclusions dialog): wildcard
patterns for files and directories.  Exclusions are non-destructive:
matching files, and files under matching directories, are returned as
ignored rows so the report stays complete.
"""
import fnmatch
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from .binary_detect import inspect_file, normalize_text_encoding

SKIP_DIRS = {
    # Historical reference only.  These directories are no longer
    # hard-pruned: the UI must account for every non-excluded file.  The
    # matching engine filters ignored files out before scoring, so noisy
    # trees do not explode Phase 2 candidate sets.
    'node_modules', '__pycache__', '.git', '.svn', '.hg',
    'target', 'build', 'dist', 'bin', 'obj', 'out',
    '.idea', '.vscode', '.gradle', '.settings',
    'vendor', '.cargo', 'debug', 'release',
    'site-packages', 'venv', 'virtualenv', 'env', 'envs',
}


# Caps for user-supplied exclusion patterns (defensive: the UI already
# limits input, but the API is open to any client)
MAX_EXCLUSION_PATTERNS = 200
MAX_PATTERN_LENGTH = 300


def normalize_exclusions(exclusions: Optional[Dict]) -> Dict[str, List[str]]:
    """Clean a raw {'files': [...], 'dirs': [...]} exclusions dict.

    Strips whitespace, drops empties/non-strings, normalizes separators
    to forward slashes, removes trailing slashes on dir patterns, and
    caps list size and pattern length.  Always returns both keys.
    """
    clean: Dict[str, List[str]] = {'files': [], 'dirs': []}
    if not isinstance(exclusions, dict):
        return clean
    for key in ('files', 'dirs'):
        raw = exclusions.get(key)
        if not isinstance(raw, (list, tuple)):
            continue
        for pat in raw[:MAX_EXCLUSION_PATTERNS]:
            if not isinstance(pat, str):
                continue
            pat = pat.strip().replace('\\', '/')[:MAX_PATTERN_LENGTH]
            if key == 'dirs':
                pat = pat.rstrip('/')
            if pat:
                clean[key].append(pat)
    return clean


def _excluded(name: str, rel_path: str, patterns: List[str]) -> bool:
    """True when `name` (basename) or `rel_path` matches any pattern.

    Patterns containing '/' match against the relative path (forward
    slashes); plain patterns match the basename anywhere in the tree.
    fnmatch is case-insensitive on Windows (via os.path.normcase).
    """
    for pat in patterns:
        target = rel_path if '/' in pat else name
        if fnmatch.fnmatch(target, pat):
            return True
    return False


def _dir_excluded(relative_dir: str, patterns: List[str]) -> bool:
    """True when relative_dir or any ancestor matches a dir pattern."""
    if not relative_dir:
        return False
    parts = relative_dir.split('/')
    for i, name in enumerate(parts):
        rel = '/'.join(parts[:i + 1])
        if _excluded(name, rel, patterns):
            return True
    return False


@dataclass
class FileInfo:
    """Represents a scanned file.

    ignored=True means the file is intentionally visible in the UI but
    not eligible for matching/diffing.
    """
    filename: str
    relative_dir: str
    full_path: str
    extension: str
    is_binary: bool = False   # determined from bytes, never extension alone
    text_encoding: str = ''   # effective charset for text; empty for binary
    ignored: bool = False
    ignored_reason: str = ''

    @property
    def relative_path(self) -> str:
        if self.relative_dir:
            return self.relative_dir + '/' + self.filename
        return self.filename

    def to_dict(self) -> dict:
        return {
            'name': self.filename,
            'directory': self.relative_dir,
            'full_path': self.full_path,
            'binary': self.is_binary,
            'encoding': self.text_encoding,
            'ignored': self.ignored,
            'ignored_reason': self.ignored_reason,
        }


def scan_directory(
    root_path: str,
    exclusions: Optional[Dict] = None,
    text_encoding: str = 'auto',
) -> List[FileInfo]:
    """
    Scan a directory recursively for all files.

    Args:
        root_path: Absolute path to the directory to scan.
        exclusions: Optional {'files': [...], 'dirs': [...]} wildcard
            patterns (see normalize_exclusions).  Matching files and
            files under matching directories are marked ignored, not
            removed from the report.
        text_encoding: Per-side charset override, or 'auto'.  The
            override never changes binary classification.

    Returns:
        List of FileInfo objects for each file found.  Every real file
        is comparable unless explicitly excluded.  Directory aliases
        and excluded files have ignored=True.

    Raises:
        ValueError: If the directory does not exist.
    """
    root_path = os.path.normpath(root_path)

    if not os.path.isdir(root_path):
        raise ValueError(f"Directory not found: {root_path}")

    excl = normalize_exclusions(exclusions)
    file_pats, dir_pats = excl['files'], excl['dirs']
    requested_encoding = normalize_text_encoding(text_encoding)

    files: List[FileInfo] = []

    # The filesystem does not yield literal "." and ".." entries during
    # a walk, but the report can still show them as explicit,
    # non-comparable directory aliases when the user wants every visible
    # thing accounted for.
    files.append(FileInfo(
        filename='.',
        relative_dir='',
        full_path=root_path,
        extension='',
        ignored=True,
        ignored_reason='Directory alias (not a file)',
    ))
    files.append(FileInfo(
        filename='..',
        relative_dir='',
        full_path=os.path.dirname(root_path),
        extension='',
        ignored=True,
        ignored_reason='Directory alias (not a file)',
    ))

    for dirpath, dirnames, filenames in os.walk(root_path):
        relative_dir = os.path.relpath(dirpath, root_path).replace('\\', '/')
        if relative_dir == '.':
            relative_dir = ''

        in_excluded_dir = _dir_excluded(relative_dir, dir_pats)

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            rel_path = (relative_dir + '/' + filename) if relative_dir else filename
            ignored_reasons: List[str] = []
            full_path = os.path.join(dirpath, filename)
            is_binary, effective_encoding = inspect_file(
                full_path, ext, requested_encoding)

            if in_excluded_dir:
                ignored_reasons.append('Excluded directory pattern')
            if _excluded(filename, rel_path, file_pats):
                ignored_reasons.append('Excluded file pattern')

            files.append(FileInfo(
                filename=filename,
                relative_dir=relative_dir,
                full_path=full_path,
                extension=ext,
                is_binary=is_binary,
                text_encoding=effective_encoding,
                ignored=bool(ignored_reasons),
                ignored_reason='; '.join(ignored_reasons),
            ))

    return files
