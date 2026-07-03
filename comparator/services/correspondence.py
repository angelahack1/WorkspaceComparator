"""
File Correspondence Engine
---------------------------
Core orchestration module.  Given two directory paths, it:

  Phase 1 -- Matches files with identical filename AND relative directory
             (highest confidence: exact path match).

  Phase 2 -- For remaining files with the SAME filename but different
             directories, runs the deterministic-similarity-comparison
             algorithm.  If the deterministic result is confident
             (>85 %) the match is accepted; otherwise the LLM is
             consulted as arbiter.

  Phase 3 -- For still-unmatched files whose filenames are *similar*
             (Levenshtein ratio > 0.7) and share the same extension,
             the same deterministic -> LLM pipeline is applied.

  Phase 4 -- Everything left over is reported as unmatched.

Matched files are returned sorted alphabetically; unmatched files are
returned separately for each side, also sorted alphabetically.
"""
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .file_scanner import FileInfo, scan_directory
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


@dataclass
class MatchResult:
    left_file: FileInfo
    right_file: FileInfo
    match_type: str   # exact_path | deterministic | llm_verified
    similarity: float
    content_status: str = 'different'  # identical | minor | different


@dataclass
class ComparisonResult:
    matched: List[MatchResult] = field(default_factory=list)
    unmatched_left: List[FileInfo] = field(default_factory=list)
    unmatched_right: List[FileInfo] = field(default_factory=list)
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

    def __init__(self, stats: Dict, read=_read_file):
        self.enabled = is_ollama_available()
        self.failures = 0
        self.stats = stats
        self.read = read
        if not self.enabled:
            logger.warning(
                "Ollama unavailable -- LLM arbitration disabled for this run")

    def score(self, left: FileInfo, right: FileInfo) -> int:
        if not self.enabled:
            return -1
        self.stats['llm_calls'] += 1
        pct = compare_with_llm(
            left.filename, self.read(left.full_path),
            right.filename, self.read(right.full_path),
        )
        if pct == -1:
            self.failures += 1
            if self.failures >= LLM_FAILURE_LIMIT:
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

def find_correspondences(left_dir: str, right_dir: str) -> ComparisonResult:
    """
    Compare two project directories and produce file correspondences.
    """
    left_files = scan_directory(left_dir)
    right_files = scan_directory(right_dir)

    result = ComparisonResult()
    result.stats = {
        'total_left': len(left_files),
        'total_right': len(right_files),
        'exact_path_matches': 0,
        'deterministic_matches': 0,
        'llm_matches': 0,
        'llm_calls': 0,
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

    gate = _LLMGate(result.stats, read)

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
    # PHASE 2 -- same filename, different directory
    # ------------------------------------------------------------------
    for li in list(free_left):
        lf = left_files[li]
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
            for sim, confidence, ri in scored[:MAX_LLM_PER_FILE]:
                if sim < LLM_MIN_SIM:
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
    for li in list(free_left):
        lf = left_files[li]
        best: Optional[MatchResult] = None
        best_combined = 0.0
        best_ri: Optional[int] = None

        # (combined, fname_sim, sim, confidence, ri) -- scored first,
        # LLM arbitration bounded, same rationale as Phase 2.
        cands: List[Tuple[float, float, float, str, int]] = []
        for ri in list(free_right):
            rf = right_files[ri]

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
                if llm_used >= MAX_LLM_PER_FILE:
                    break
                if confidence not in ('medium', 'low') or fname_sim <= 0.80:
                    continue
                if sim < LLM_MIN_SIM:
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
    # PHASE 4 -- collect unmatched
    # ------------------------------------------------------------------
    result.unmatched_left = [left_files[i] for i in sorted(free_left)]
    result.unmatched_right = [right_files[i] for i in sorted(free_right)]

    # Final sorting
    result.matched.sort(key=lambda m: m.left_file.filename.lower())
    result.unmatched_left.sort(key=lambda f: f.filename.lower())
    result.unmatched_right.sort(key=lambda f: f.filename.lower())

    return result
