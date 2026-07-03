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
from .llm_comparator import compare_with_llm

logger = logging.getLogger(__name__)

# Thresholds
DETERMINISTIC_HIGH = 85.0   # Above this -> auto-match
DETERMINISTIC_UNCERTAIN = 40.0  # Below this with same name -> still ask LLM
LLM_MATCH_THRESHOLD = 70    # LLM must return >= this to match
FILENAME_SIM_THRESHOLD = 0.70  # Minimum filename similarity for Phase 3


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
    """Read a file with encoding fallback."""
    for enc in ('utf-8', 'utf-8-sig', 'latin-1', 'cp1252'):
        try:
            with open(path, 'r', encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ''


def _content_status(left: FileInfo, right: FileInfo) -> str:
    c1 = _read_file(left.full_path)
    c2 = _read_file(right.full_path)
    return compute_content_status(c1, c2, left.extension)


def _run_deterministic(left: FileInfo, right: FileInfo) -> Tuple[float, str]:
    c1 = _read_file(left.full_path)
    c2 = _read_file(right.full_path)
    return compute_similarity(c1, c2, left.extension)


def _run_llm(left: FileInfo, right: FileInfo) -> int:
    c1 = _read_file(left.full_path)
    c2 = _read_file(right.full_path)
    return compare_with_llm(left.filename, c1, right.filename, c2)


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
                status = _content_status(lf, rf)
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

        for ri in candidates:
            rf = right_files[ri]
            sim, confidence = _run_deterministic(lf, rf)
            logger.info(
                "Phase2 deterministic %s <-> %s : %.1f%% (%s)",
                lf.relative_path, rf.relative_path, sim, confidence,
            )

            if confidence == 'high' and sim > DETERMINISTIC_HIGH:
                # Confident deterministic match
                if sim > best_score:
                    best_score = sim
                    status = _content_status(lf, rf)
                    best = MatchResult(lf, rf, 'deterministic', sim, content_status=status)
                    best_ri = ri
            else:
                # Uncertain -- ask the LLM
                result.stats['llm_calls'] += 1
                llm_pct = _run_llm(lf, rf)
                logger.info(
                    "Phase2 LLM %s <-> %s : %d",
                    lf.relative_path, rf.relative_path, llm_pct,
                )

                if llm_pct >= LLM_MATCH_THRESHOLD and llm_pct > best_score:
                    best_score = float(llm_pct)
                    status = _content_status(lf, rf)
                    best = MatchResult(lf, rf, 'llm_verified', float(llm_pct), content_status=status)
                    best_ri = ri
                elif llm_pct == -1 and sim > DETERMINISTIC_UNCERTAIN:
                    # LLM unavailable: accept if deterministic is reasonable
                    if sim > best_score:
                        best_score = sim
                        status = _content_status(lf, rf)
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

            sim, confidence = _run_deterministic(lf, rf)
            combined = fname_sim * 30.0 + sim * 0.70

            logger.info(
                "Phase3 %s <-> %s : fname=%.2f content=%.1f%% combined=%.1f",
                lf.filename, rf.filename, fname_sim, sim, combined,
            )

            if confidence == 'high' and sim > DETERMINISTIC_HIGH:
                if combined > best_combined:
                    best_combined = combined
                    status = _content_status(lf, rf)
                    best = MatchResult(lf, rf, 'deterministic', sim, content_status=status)
                    best_ri = ri
            elif confidence in ('medium', 'low') and fname_sim > 0.80:
                result.stats['llm_calls'] += 1
                llm_pct = _run_llm(lf, rf)
                if llm_pct >= LLM_MATCH_THRESHOLD:
                    c = fname_sim * 30.0 + llm_pct * 0.70
                    if c > best_combined:
                        best_combined = c
                        status = _content_status(lf, rf)
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
