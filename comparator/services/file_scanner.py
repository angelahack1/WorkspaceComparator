"""
File Scanner Module
-------------------
Recursively scans directories for source code files, collecting metadata
about each file for the correspondence engine.
"""
import os
from dataclasses import dataclass
from typing import List

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
}


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


def scan_directory(root_path: str) -> List[FileInfo]:
    """
    Scan a directory recursively for source code files.

    Args:
        root_path: Absolute path to the directory to scan.

    Returns:
        List of FileInfo objects for each source file found.

    Raises:
        ValueError: If the directory does not exist.
    """
    root_path = os.path.normpath(root_path)

    if not os.path.isdir(root_path):
        raise ValueError(f"Directory not found: {root_path}")

    files: List[FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root_path):
        # Prune directories we don't want to traverse
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith('.') and d.lower() not in SKIP_DIRS
        ]

        for filename in filenames:
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SOURCE_EXTENSIONS:
                continue

            full_path = os.path.join(dirpath, filename)
            relative_dir = os.path.relpath(dirpath, root_path)
            # Normalize to forward slashes for consistency
            relative_dir = relative_dir.replace('\\', '/')
            if relative_dir == '.':
                relative_dir = ''

            files.append(FileInfo(
                filename=filename,
                relative_dir=relative_dir,
                full_path=full_path,
                extension=ext,
            ))

    return files
