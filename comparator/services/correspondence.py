# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/correspondence.py                        ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
File Correspondence Engine
---------------------------
Core orchestration module.  Given two directory paths, it:

  Phase 1 -- Matches files with identical filename AND relative directory
             (highest confidence: exact path match).  Binary pairs are
             compared byte-for-byte for their content status.

  Phase 2-BIN -- Binary files (is_binary flag from the scanner) with
             the SAME filename in different directories.  Binary bytes
             are opaque to text similarity and meaningless to an LLM,
             so the exact filename is the only key; the directory path
             is the tie-break clue among several candidates, and byte
             identity trumps everything.  The LLM is NEVER consulted
             for binary files.

  Phase 2 -- For remaining TEXT files with the SAME filename but
             different directories, runs the deterministic-similarity
             comparison algorithm.  If the deterministic result is
             confident (>85 %) the match is accepted; otherwise the
             LLM is consulted as arbiter.

  Phase 3 -- For still-unmatched text files whose filenames are
             *similar* (Levenshtein ratio > 0.7) and share the same
             extension, the same deterministic -> LLM pipeline is
             applied.  Binary files never enter Phases 3/3b: a renamed
             binary is undecidable, so it stays unmatched.

  Phase 4 -- Everything left over is reported as unmatched.

Matched files are returned sorted alphabetically; unmatched files are
returned separately for each side, also sorted alphabetically.
"""
import difflib
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .binary_detect import binary_similarity, bytes_equal
from .file_scanner import FileInfo, normalize_exclusions, scan_directory
from .deterministic import compute_filename_similarity, compute_similarity, compute_content_status
from .llm_comparator import compare_with_llm, is_ollama_available

logger = logging.getLogger(__name__)

# Thresholds
DETERMINISTIC_HIGH = 85.0   # Above this -> auto-match
DETERMINISTIC_UNCERTAIN = 40.0  # Below this with same name -> still ask LLM
LLM_MATCH_THRESHOLD = 70    # LLM must return >= this to match
FILENAME_SIM_THRESHOLD = 0.70  # Minimum filename similarity for Phase 3
LLM_FAILURE_LIMIT = 3       # Consecutive LLM failures before bypassing it
MAX_LLM_PER_FILE = 3        # LLM-arbitrate only the top-N candidates per file
LLM_MIN_SIM = 15.0          # Noise floor: below this, don't bother the LLM
CONTENT_SIM_THRESHOLD = 60.0  # Phase 3b: content-only match (renamed files)

# User-tunable engine settings (the UI's Engine Settings dialog sends
# these in the compare request body).  Values outside the bounds are
# clamped; unknown keys are ignored.  content_sim_threshold's floor is
# 10, not 0 -- at ~0 Phase 3b would greedily pair *everything*.
SETTING_BOUNDS = {
    'llm_failure_limit':     (1, 20),
    'max_llm_per_file':      (0, 20),
    'llm_min_sim':           (0.0, 100.0),
    'content_sim_threshold': (10.0, 100.0),
}


def resolve_settings(settings: Optional[Dict] = None) -> Dict:
    """Merge user overrides over the engine defaults, clamped to bounds."""
    cfg = {
        'llm_failure_limit': LLM_FAILURE_LIMIT,
        'max_llm_per_file': MAX_LLM_PER_FILE,
        'llm_min_sim': LLM_MIN_SIM,
        'content_sim_threshold': CONTENT_SIM_THRESHOLD,
    }
    if not settings:
        return cfg
    for key, (lo, hi) in SETTING_BOUNDS.items():
        if key not in settings:
            continue
        try:
            val = float(settings[key])
        except (TypeError, ValueError):
            continue
        val = max(lo, min(hi, val))
        cfg[key] = int(round(val)) if isinstance(lo, int) else val
    return cfg


@dataclass
class MatchResult:
    left_file: FileInfo
    right_file: FileInfo
    match_type: str   # exact_path | binary | deterministic | llm_verified | content
    similarity: float
    content_status: str = 'different'  # identical | minor | different


@dataclass
class ComparisonResult:
    matched: List[MatchResult] = field(default_factory=list)
    unmatched_left: List[FileInfo] = field(default_factory=list)
    unmatched_right: List[FileInfo] = field(default_factory=list)
    ignored_left: List[FileInfo] = field(default_factory=list)
    ignored_right: List[FileInfo] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)


def _read_file(path: str) -> str:
    """Read a file with encoding fallback.

    Returns '' when the file is undecodable OR unreadable (e.g. it
    vanished between the initial scan and this comparison -- long runs
    over trees with transient dirs must degrade, not crash).
    """
    for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
        try:
            with open(path, 'r', encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError:
            logger.warning("Unreadable file skipped: %s", path)
            return ''
    return ''


def _content_status(left: FileInfo, right: FileInfo, read=_read_file) -> str:
    c1 = read(left.full_path)
    c2 = read(right.full_path)
    return compute_content_status(c1, c2, left.extension)


def _binary_status(left: FileInfo, right: FileInfo) -> str:
    """Content status for binary pairs: bytes either match or they don't.

    There is no 'minor' for binaries -- whitespace/comment normalization
    has no meaning in a byte stream.
    """
    return 'identical' if bytes_equal(left.full_path, right.full_path) else 'different'


def _dir_similarity(a: str, b: str) -> float:
    """0-1 similarity between two relative directory paths (the Phase
    2-BIN tie-break clue)."""
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _run_deterministic(left: FileInfo, right: FileInfo, read=_read_file) -> Tuple[float, str]:
    c1 = read(left.full_path)
    c2 = read(right.full_path)
    return compute_similarity(c1, c2, left.extension)


class _LLMGate:
    """Circuit breaker around the LLM arbiter.

    Ambiguous Phase 2/3 candidates escalate to the LLM.  When the
    backend is broken (unreachable, timing out, or answering garbage)
    every escalation costs a full round-trip and fails -- on large
    unrelated trees that means hundreds of doomed calls and a compare
    that never finishes.  After LLM_FAILURE_LIMIT *consecutive*
    failures the gate closes and further requests short-circuit to -1,
    so the run completes on deterministic scoring alone.
    """

    def __init__(self, stats: Dict, read=_read_file,
                 failure_limit: int = LLM_FAILURE_LIMIT):
        self.enabled = is_ollama_available()
        self.failures = 0
        self.failure_limit = failure_limit
        self.stats = stats
        self.read = read
        if not self.enabled:
            logger.warning(
                "Ollama unavailable -- LLM arbitration disabled for this run")

    def score(self, left: FileInfo, right: FileInfo) -> int:
        if not self.enabled:
            return -1
        content_left = self.read(left.full_path)
        content_right = self.read(right.full_path)
        if '\x00' in content_left or '\x00' in content_right:
            # Binary content that slipped past extension detection (the
            # latin-1 fallback decodes anything, NULs included) must
            # never reach the LLM -- behave like an unavailable backend
            # without charging the circuit breaker.
            return -1
        self.stats['llm_calls'] += 1
        pct = compare_with_llm(
            left.filename, content_left,
            right.filename, content_right,
        )
        if pct == -1:
            self.failures += 1
            if self.failures >= self.failure_limit:
                self.enabled = False
                logger.warning(
                    "LLM arbitration disabled after %d consecutive failures; "
                    "continuing with deterministic scoring only",
                    self.failures,
                )
        else:
            self.failures = 0
        return pct


# ===================================================================
# Main entry point
# ===================================================================

def find_correspondences(
    left_dir: str,
    right_dir: str,
    settings: Optional[Dict] = None,
    exclusions: Optional[Dict] = None,
) -> ComparisonResult:
    """
    Compare two project directories and produce file correspondences.

    `settings` optionally overrides the tunable engine constants for
    this run (see SETTING_BOUNDS); values are clamped, unknown keys
    ignored, and the effective values echoed in stats['settings'].

    `exclusions` is an optional {'files': [...], 'dirs': [...]} dict of
    wildcard patterns.  Exclusions are visible/non-destructive:
    matching files, and files inside matching directories, are reported
    under ignored_* and do not take part in matching.  Effective
    patterns are echoed in stats['exclusions'].
    """
    cfg = resolve_settings(settings)
    excl = normalize_exclusions(exclusions)
    left_entries = scan_directory(left_dir, excl)
    right_entries = scan_directory(right_dir, excl)
    left_files = [f for f in left_entries if not f.ignored]
    right_files = [f for f in right_entries if not f.ignored]
    ignored_left = [f for f in left_entries if f.ignored]
    ignored_right = [f for f in right_entries if f.ignored]

    result = ComparisonResult()
    result.stats = {
        'total_left': len(left_entries),
        'total_right': len(right_entries),
        'comparable_left': len(left_files),
        'comparable_right': len(right_files),
        'ignored_left': len(ignored_left),
        'ignored_right': len(ignored_right),
        'exact_path_matches': 0,
        'deterministic_matches': 0,
        'binary_matches': 0,
        'llm_matches': 0,
        'llm_calls': 0,
        'settings': cfg,
        'exclusions': excl,
    }

    free_left: Set[int] = set(range(len(left_files)))
    free_right: Set[int] = set(range(len(right_files)))

    # Per-run file-content cache: Phase 2/3 compare the same files
    # against many candidates -- without this every comparison re-reads
    # both files from disk.
    _cache: Dict[str, str] = {}

    def read(path: str) -> str:
        if path not in _cache:
            _cache[path] = _read_file(path)
        return _cache[path]

    gate = _LLMGate(result.stats, read, cfg['llm_failure_limit'])

    # Build lookup indexes
    right_by_name: Dict[str, List[int]] = {}
    for i, f in enumerate(right_files):
        right_by_name.setdefault(f.filename, []).append(i)

    # ------------------------------------------------------------------
    # PHASE 1 -- exact path match (same filename + same relative dir)
    # ------------------------------------------------------------------
    for li in list(free_left):
        lf = left_files[li]
        candidates = right_by_name.get(lf.filename, [])
        for ri in candidates:
            if ri not in free_right:
                continue
            rf = right_files[ri]
            if rf.relative_dir == lf.relative_dir:
                if lf.is_binary or rf.is_binary:
                    status = _binary_status(lf, rf)
                    result.stats['binary_matches'] += 1
                else:
                    status = _content_status(lf, rf, read)
                result.matched.append(MatchResult(
                    left_file=lf,
                    right_file=rf,
                    match_type='exact_path',
                    similarity=100.0,
                    content_status=status,
                ))
                free_left.discard(li)
                free_right.discard(ri)
                result.stats['exact_path_matches'] += 1
                break

    # ------------------------------------------------------------------
    # PHASE 2-BIN -- binary files: same filename, different directory
    # ------------------------------------------------------------------
    # Binary content is opaque to the text pipeline and meaningless to
    # the LLM, so the EXACT filename is the only reliable key; the
    # directory path is the tie-break clue among several same-named
    # candidates, and byte identity trumps everything.  Runs BEFORE the
    # text Phase 2 so no binary file can ever reach text scoring or LLM
    # arbitration.
    for li in list(free_left):
        lf = left_files[li]
        if not lf.is_binary:
            continue
        candidates = [
            ri for ri in right_by_name.get(lf.filename, [])
            if ri in free_right and right_files[ri].is_binary
        ]
        if not candidates:
            continue

        best_ri: Optional[int] = None
        best_key: Optional[Tuple[int, float]] = None
        best_identical = False
        for ri in candidates:
            rf = right_files[ri]
            identical = bytes_equal(lf.full_path, rf.full_path)
            key = (1 if identical else 0,
                   _dir_similarity(lf.relative_dir, rf.relative_dir))
            if best_key is None or key > best_key:
                best_ri, best_key, best_identical = ri, key, identical

        rf = right_files[best_ri]
        sim = 100.0 if best_identical else binary_similarity(lf.full_path, rf.full_path)
        logger.info(
            "Phase2-BIN %s <-> %s : %s (dir clue %.2f, est. sim %.1f%%)",
            lf.relative_path, rf.relative_path,
            'identical' if best_identical else 'different',
            best_key[1], sim,
        )
        result.matched.append(MatchResult(
            lf, rf, 'binary', sim,
            content_status='identical' if best_identical else 'different',
        ))
        free_left.discard(li)
        free_right.discard(best_ri)
        result.stats['binary_matches'] += 1

    # ------------------------------------------------------------------
    # PHASE 2 -- same filename, different directory (text files)
    # ------------------------------------------------------------------
    for li in list(free_left):
        lf = left_files[li]
        if lf.is_binary:
            continue  # binaries were handled in Phase 2-BIN or stay unmatched
        candidates = [
            ri for ri in right_by_name.get(lf.filename, [])
            if ri in free_right
        ]
        if not candidates:
            continue

        best: Optional[MatchResult] = None
        best_score = 0.0
        best_ri: Optional[int] = None

        # Score all candidates deterministically first, best-first.
        # LLM arbitration is bounded (MAX_LLM_PER_FILE, LLM_MIN_SIM):
        # unbounded per-candidate escalation melts down on trees with
        # many same-named files (site-packages-style __init__.py swarms).
        scored: List[Tuple[float, str, int]] = []
        for ri in candidates:
            rf = right_files[ri]
            sim, confidence = _run_deterministic(lf, rf, read)
            logger.info(
                "Phase2 deterministic %s <-> %s : %.1f%% (%s)",
                lf.relative_path, rf.relative_path, sim, confidence,
            )
            scored.append((sim, confidence, ri))
        scored.sort(key=lambda t: t[0], reverse=True)

        # Confident deterministic winner: take the highest-scoring one.
        for sim, confidence, ri in scored:
            if confidence == 'high' and sim > DETERMINISTIC_HIGH:
                rf = right_files[ri]
                best_score = sim
                status = _content_status(lf, rf, read)
                best = MatchResult(lf, rf, 'deterministic', sim, content_status=status)
                best_ri = ri
                break

        if best is None:
            # Ambiguous: arbitrate only the most promising candidates.
            for sim, confidence, ri in scored[:cfg['max_llm_per_file']]:
                if sim < cfg['llm_min_sim']:
                    break  # sorted desc: everything below is noise
                rf = right_files[ri]
                llm_pct = gate.score(lf, rf)
                logger.info(
                    "Phase2 LLM %s <-> %s : %d",
                    lf.relative_path, rf.relative_path, llm_pct,
                )

                if llm_pct >= LLM_MATCH_THRESHOLD and llm_pct > best_score:
                    best_score = float(llm_pct)
                    status = _content_status(lf, rf, read)
                    best = MatchResult(lf, rf, 'llm_verified', float(llm_pct), content_status=status)
                    best_ri = ri
                elif llm_pct == -1 and sim > DETERMINISTIC_UNCERTAIN:
                    # LLM unavailable: accept if deterministic is reasonable
                    if sim > best_score:
                        best_score = sim
                        status = _content_status(lf, rf, read)
                        best = MatchResult(lf, rf, 'deterministic', sim, content_status=status)
                        best_ri = ri

        if best is not None and best_ri is not None:
            result.matched.append(best)
            free_left.discard(li)
            free_right.discard(best_ri)
            if best.match_type == 'llm_verified':
                result.stats['llm_matches'] += 1
            else:
                result.stats['deterministic_matches'] += 1

    # ------------------------------------------------------------------
    # PHASE 3 -- similar filename (not exact), compatible extension
    # ------------------------------------------------------------------
    # Text files only: a *renamed* binary is undecidable (no readable
    # content, no LLM), so binaries require the exact filename and
    # anything else stays unmatched.
    for li in list(free_left):
        lf = left_files[li]
        if lf.is_binary:
            continue
        best: Optional[MatchResult] = None
        best_combined = 0.0
        best_ri: Optional[int] = None

        # (combined, fname_sim, sim, confidence, ri) -- scored first,
        # LLM arbitration bounded, same rationale as Phase 2.
        cands: List[Tuple[float, float, float, str, int]] = []
        for ri in list(free_right):
            rf = right_files[ri]
            if rf.is_binary:
                continue

            # Extensions must match
            if lf.extension != rf.extension:
                continue

            # Filename must be similar but not identical (identical were
            # handled in Phase 2)
            if lf.filename == rf.filename:
                continue

            fname_sim = compute_filename_similarity(lf.filename, rf.filename)
            if fname_sim < FILENAME_SIM_THRESHOLD:
                continue

            sim, confidence = _run_deterministic(lf, rf, read)
            combined = fname_sim * 30.0 + sim * 0.70

            logger.info(
                "Phase3 %s <-> %s : fname=%.2f content=%.1f%% combined=%.1f",
                lf.filename, rf.filename, fname_sim, sim, combined,
            )
            cands.append((combined, fname_sim, sim, confidence, ri))
        cands.sort(key=lambda t: t[0], reverse=True)

        for combined, fname_sim, sim, confidence, ri in cands:
            if confidence == 'high' and sim > DETERMINISTIC_HIGH:
                rf = right_files[ri]
                best_combined = combined
                status = _content_status(lf, rf, read)
                best = MatchResult(lf, rf, 'deterministic', sim, content_status=status)
                best_ri = ri
                break

        if best is None:
            llm_used = 0
            for combined, fname_sim, sim, confidence, ri in cands:
                if llm_used >= cfg['max_llm_per_file']:
                    break
                if confidence not in ('medium', 'low') or fname_sim <= 0.80:
                    continue
                if sim < cfg['llm_min_sim']:
                    continue
                rf = right_files[ri]
                llm_used += 1
                llm_pct = gate.score(lf, rf)
                if llm_pct >= LLM_MATCH_THRESHOLD:
                    c = fname_sim * 30.0 + llm_pct * 0.70
                    if c > best_combined:
                        best_combined = c
                        status = _content_status(lf, rf, read)
                        best = MatchResult(
                            lf, rf, 'llm_verified', float(llm_pct), content_status=status)
                        best_ri = ri

        if best is not None and best_ri is not None:
            result.matched.append(best)
            free_left.discard(li)
            free_right.discard(best_ri)
            if best.match_type == 'llm_verified':
                result.stats['llm_matches'] += 1
            else:
                result.stats['deterministic_matches'] += 1

    # ------------------------------------------------------------------
    # PHASE 3b -- renamed files: very different name, same content
    # ------------------------------------------------------------------
    # A rename beyond FILENAME_SIM_THRESHOLD never reaches Phase 3, so
    # sweep the leftovers purely by content: same extension and a
    # deterministic similarity >= CONTENT_SIM_THRESHOLD pair up no
    # matter how different the filenames are.  A cheap length bound
    # prunes most of the O(L*R) sweep before any expensive comparison
    # (a rename candidate can't be several times larger or smaller).
    for li in list(free_left):
        lf = left_files[li]
        if lf.is_binary:
            continue  # renamed binaries are undecidable -- never content-swept
        l_len = len(read(lf.full_path))
        if l_len == 0:
            continue  # empty/unreadable: content carries no signal

        best: Optional[MatchResult] = None
        best_sim = 0.0
        best_ri: Optional[int] = None

        for ri in list(free_right):
            rf = right_files[ri]
            if rf.is_binary:
                continue
            if lf.extension != rf.extension:
                continue
            r_len = len(read(rf.full_path))
            if r_len == 0:
                continue
            if 200.0 * min(l_len, r_len) / (l_len + r_len) < cfg['content_sim_threshold']:
                continue

            sim, confidence = _run_deterministic(lf, rf, read)
            if sim >= cfg['content_sim_threshold'] and sim > best_sim:
                best_sim = sim
                status = _content_status(lf, rf, read)
                best = MatchResult(lf, rf, 'content', sim, content_status=status)
                best_ri = ri

        if best is not None and best_ri is not None:
            logger.info(
                "Phase3b content match %s <-> %s : %.1f%%",
                lf.relative_path, best.right_file.relative_path, best_sim,
            )
            result.matched.append(best)
            free_left.discard(li)
            free_right.discard(best_ri)
            result.stats['deterministic_matches'] += 1

    # ------------------------------------------------------------------
    # PHASE 4 -- collect unmatched
    # ------------------------------------------------------------------
    result.unmatched_left = [left_files[i] for i in sorted(free_left)]
    result.unmatched_right = [right_files[i] for i in sorted(free_right)]
    result.ignored_left = ignored_left
    result.ignored_right = ignored_right

    # Final sorting -- the LEFT side (the user's original project) is
    # the anchor: primary key = left filename, secondary key = left
    # directory.  The right file rides along with its partner even when
    # its own name is completely different (renamed/content matches).
    result.matched.sort(key=lambda m: (
        m.left_file.filename.lower(), m.left_file.relative_dir.lower()))
    result.unmatched_left.sort(key=lambda f: (
        f.filename.lower(), f.relative_dir.lower()))
    result.unmatched_right.sort(key=lambda f: (
        f.filename.lower(), f.relative_dir.lower()))
    result.ignored_left.sort(key=lambda f: (
        f.filename.lower(), f.relative_dir.lower()))
    result.ignored_right.sort(key=lambda f: (
        f.filename.lower(), f.relative_dir.lower()))

    return result
