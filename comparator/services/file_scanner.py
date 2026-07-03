"""
File Scanner Module
-------------------
Recursively scans directories for source code files, collecting metadata
about each file for the correspondence engine.

Supports user-defined exclusions (the UI's Exclusions dialog): wildcard
patterns for files and directories.  Excluded directories are pruned
from the walk, so their contents are never scanned at all.
"""
import fnmatch
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

SOURCE_EXTENSIONS = {
    # C family
    '.c', '.h', '.cpp', '.hpp', '.cc', '.hh', '.cxx', '.hxx',
    # Java
    '.java',
    # Rust
    '.rs',
    # C#
    '.cs',
    # Go
    '.go',
    # Kotlin / Scala
    '.kt', '.kts', '.scala',
    # Swift / Objective-C
    '.swift', '.m', '.mm',
    # Python
    '.py',
    # JavaScript / TypeScript
    '.js', '.jsx', '.ts', '.tsx',
    # Build / config files that may be relevant
    '.gradle', '.xml', '.properties', '.yaml', '.yml', '.toml',
}

SKIP_DIRS = {
    'node_modules', '__pycache__', '.git', '.svn', '.hg',
    'target', 'build', 'dist', 'bin', 'obj', 'out',
    '.idea', '.vscode', '.gradle', '.settings',
    'vendor', '.cargo', 'debug', 'release',
    # Python environments / installed packages: an embedded runtime or
    # venv carries thousands of third-party files (site-packages alone
    # has ~1000s of __init__.py) that explode Phase 2 candidate sets.
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


@dataclass
class FileInfo:
    """Represents a source file found during directory scanning."""
    filename: str
    relative_dir: str
    full_path: str
    extension: str

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
        }


def scan_directory(
    root_path: str,
    exclusions: Optional[Dict] = None,
) -> List[FileInfo]:
    """
    Scan a directory recursively for source code files.

    Args:
        root_path: Absolute path to the directory to scan.
        exclusions: Optional {'files': [...], 'dirs': [...]} wildcard
            patterns (see normalize_exclusions).  Matching directories
            are pruned from the walk; matching files are skipped.

    Returns:
        List of FileInfo objects for each source file found.

    Raises:
        ValueError: If the directory does not exist.
    """
    root_path = os.path.normpath(root_path)

    if not os.path.isdir(root_path):
        raise ValueError(f"Directory not found: {root_path}")

    excl = normalize_exclusions(exclusions)
    file_pats, dir_pats = excl['files'], excl['dirs']

    files: List[FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        relative_dir = os.path.relpath(dirpath, root_path).replace('\\', '/')
        if relative_dir == '.':
            relative_dir = ''

        # Prune directories we don't want to traverse
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith('.') and d.lower() not in SKIP_DIRS
            and not _excluded(
                d, (relative_dir + '/' + d) if relative_dir else d, dir_pats)
        ]

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SOURCE_EXTENSIONS:
                continue

            rel_path = (relative_dir + '/' + filename) if relative_dir else filename
            if _excluded(filename, rel_path, file_pats):
                continue

            files.append(FileInfo(
                filename=filename,
                relative_dir=relative_dir,
                full_path=os.path.join(dirpath, filename),
                extension=ext,
            ))

    return files
