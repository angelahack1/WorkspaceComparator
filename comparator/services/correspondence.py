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

  Phase 2-ID -- Reserve same-stem text identities across all free files:
             identical nonempty text first, then declared type identity.
             Content status and similarity remain independent of pairing.

  Phase 2 -- For remaining TEXT files with the SAME filename but
             different directories, runs the deterministic-similarity
             comparison algorithm.  If the deterministic result is
             confident (>85 %) the match is accepted; otherwise the
             LLM is consulted as arbiter.

  Phase 3 -- For still-unmatched text files whose filenames are
             *similar* (Levenshtein ratio > 0.7), regardless of
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

from .binary_detect import (
    binary_similarity,
    bytes_equal,
    normalize_charsets,
    read_text_file,
)
from .file_scanner import FileInfo, normalize_exclusions, scan_directory
from .deterministic import (
    compute_filename_similarity, compute_similarity, compute_content_status,
    extract_type_names,
)
from .llm_comparator import compare_with_llm, is_ollama_available, OllamaUnavailable

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


def _read_file(path: str, encoding: str = 'auto') -> str:
    """Read any content-sniffed text file, regardless of extension."""
    return read_text_file(path, encoding)


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
    that never finishes. Transport/HTTP failures close the gate at once;
    LLM_FAILURE_LIMIT consecutive malformed answers also close it.
    Further requests short-circuit to -1, so the run completes on
    deterministic scoring alone. Readiness is checked lazily once.
    """

    def __init__(self, stats: Dict, read=_read_file,
                 failure_limit: int = LLM_FAILURE_LIMIT, enabled: bool = True):
        self.enabled = enabled
        self.checked = False
        self.failures = 0
        self.failure_limit = failure_limit
        self.stats = stats
        self.read = read

    def score(self, left: FileInfo, right: FileInfo) -> int:
        if not self.enabled:
            return -1
        if left.is_binary or right.is_binary:
            return -1
        content_left = self.read(left.full_path)
        content_right = self.read(right.full_path)
        if '\x00' in content_left or '\x00' in content_right:
            # Secondary defense: NUL-bearing content must never reach
            # the LLM even if a file changed after the scanner sniff.
            return -1
        if not self.checked:
            self.checked = True
            self.enabled = is_ollama_available()
            if not self.enabled:
                logger.warning("Ollama/model unavailable -- using deterministic scoring")
                return -1
        self.stats['llm_calls'] += 1
        try:
            pct = compare_with_llm(
                left.filename, content_left,
                right.filename, content_right,
                left.text_encoding, right.text_encoding,
                raise_unavailable=True,
            )
        except OllamaUnavailable:
            self.enabled = False
            logger.warning("Ollama service failed -- using deterministic scoring for this run")
            return -1
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
    charsets: Optional[Dict] = None,
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
    charset_cfg = normalize_charsets(charsets)
    left_entries = scan_directory(left_dir, excl, charset_cfg['left'])
    right_entries = scan_directory(right_dir, excl, charset_cfg['right'])
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
        'charsets': charset_cfg,
    }

    free_left: Set[int] = set(range(len(left_files)))
    free_right: Set[int] = set(range(len(right_files)))

    # Per-run file-content cache: Phase 2/3 compare the same files
    # against many candidates -- without this every comparison re-reads
    # both files from disk.
    _cache: Dict[str, str] = {}

    encodings = {
        f.full_path: f.text_encoding
        for f in left_entries + right_entries
        if not f.is_binary
    }

    def read(path: str) -> str:
        if path not in _cache:
            _cache[path] = _read_file(path, encodings.get(path, 'auto'))
        return _cache[path]

    gate = _LLMGate(result.stats, read, cfg['llm_failure_limit'],
                    enabled=cfg['max_llm_per_file'] > 0)

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
    # PHASE 2-ID -- reserve strong text identities before greedy scoring.
    # Rank across all left files to protect identical duplicates from
    # earlier, weaker matches. Exact paths retain the highest priority.
    # ------------------------------------------------------------------
    right_by_stem: Dict[str, List[int]] = {}
    for ri in sorted(free_right):
        rf = right_files[ri]
        if not rf.is_binary:
            right_by_stem.setdefault(os.path.splitext(rf.filename)[0], []).append(ri)

    type_cache: Dict[str, Set[str]] = {}

    def types(f: FileInfo) -> Set[str]:
        if f.full_path not in type_cache:
            type_cache[f.full_path] = extract_type_names(read(f.full_path), f.extension)
        return type_cache[f.full_path]

    identity_edges = []
    for li in sorted(free_left):
        lf = left_files[li]
        if lf.is_binary:
            continue
        stem = os.path.splitext(lf.filename)[0]
        for ri in right_by_stem.get(stem, []):
            rf = right_files[ri]
            same_name = lf.filename == rf.filename
            lc, rc = read(lf.full_path), read(rf.full_path)
            identical = bool(lc.strip()) and lc == rc
            lt, rt = types(lf), types(rf)
            # Shared helpers alone are insufficient. Prefer the type named
            # by the file, or a single shared type for snake_case modules.
            same_type = stem in lt & rt or (len(lt) == 1 and lt == rt)
            if not (identical or same_type):
                continue
            sim, _ = _run_deterministic(lf, rf, read)
            identity_edges.append((
                identical, same_name, same_type, sim,
                _dir_similarity(lf.relative_dir, rf.relative_dir), li, ri,
            ))

    identity_edges.sort(key=lambda edge: (
        -int(edge[0]), -int(edge[1]), -int(edge[2]), -edge[3], -edge[4],
        left_files[edge[5]].relative_path, right_files[edge[6]].relative_path,
    ))
    for _identical, _same_name, _same_type, sim, _dir, li, ri in identity_edges:
        if li not in free_left or ri not in free_right:
            continue
        lf, rf = left_files[li], right_files[ri]
        result.matched.append(MatchResult(
            lf, rf, 'deterministic', sim,
            content_status=_content_status(lf, rf, read),
        ))
        free_left.remove(li)
        free_right.remove(ri)
        result.stats['deterministic_matches'] += 1

    # ------------------------------------------------------------------
    # PHASE 2 -- same filename, different directory (text files)
    # ------------------------------------------------------------------
    for li in list(free_left):
        lf = left_files[li]
        if lf.is_binary:
            continue  # binaries were handled in Phase 2-BIN or stay unmatched
        candidates = [
            ri for ri in right_by_name.get(lf.filename, [])
            if ri in free_right and not right_files[ri].is_binary
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
            # Explicit offline mode must retain the same deterministic
            # fallback as an unavailable service, independent of AI limits.
            if cfg['max_llm_per_file'] == 0 and scored[0][0] > DETERMINISTIC_UNCERTAIN:
                sim, _confidence, ri = scored[0]
                rf = right_files[ri]
                best = MatchResult(lf, rf, 'deterministic', sim,
                                   content_status=_content_status(lf, rf, read))
                best_ri = ri

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
    # PHASE 3 -- similar filename (not exact), any text extension
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
    # PHASE 3b -- renamed files: very different name, any text format
    # ------------------------------------------------------------------
    # A rename beyond FILENAME_SIM_THRESHOLD never reaches Phase 3, so
    # sweep the leftovers by content regardless of extension.  Unknown,
    # custom, and changed extensions use the same generic tokenizer.
    # Deterministic winners pair immediately; otherwise the best few
    # candidates may use the same bounded LLM fallback as earlier
    # phases.  A cheap length bound prunes the O(L*R) sweep first.
    for li in list(free_left):
        # The sweep reads each left file eagerly, so stop as soon as the
        # right side has no free text candidate at all (empty or
        # binary-only right projects, or candidates consumed mid-loop).
        if not any(not right_files[ri].is_binary for ri in free_right):
            break
        lf = left_files[li]
        if lf.is_binary:
            continue  # renamed binaries are undecidable -- never content-swept
        l_len = len(read(lf.full_path))
        if l_len == 0:
            continue  # empty/unreadable: content carries no signal

        best: Optional[MatchResult] = None
        best_sim = 0.0
        best_ri: Optional[int] = None
        cands: List[Tuple[float, str, int]] = []

        for ri in list(free_right):
            rf = right_files[ri]
            if rf.is_binary:
                continue
            r_len = len(read(rf.full_path))
            if r_len == 0:
                continue
            if 200.0 * min(l_len, r_len) / (l_len + r_len) < cfg['content_sim_threshold']:
                continue

            sim, confidence = _run_deterministic(lf, rf, read)
            cands.append((sim, confidence, ri))

        cands.sort(key=lambda item: item[0], reverse=True)
        if cands and cands[0][0] >= cfg['content_sim_threshold']:
            best_sim, _confidence, best_ri = cands[0]
            rf = right_files[best_ri]
            status = _content_status(lf, rf, read)
            best = MatchResult(lf, rf, 'content', best_sim, content_status=status)

        if best is None:
            for sim, _confidence, ri in cands[:cfg['max_llm_per_file']]:
                if sim < cfg['llm_min_sim']:
                    break
                rf = right_files[ri]
                llm_pct = gate.score(lf, rf)
                logger.info(
                    "Phase3b LLM %s <-> %s : %d",
                    lf.relative_path, rf.relative_path, llm_pct,
                )
                if llm_pct >= LLM_MATCH_THRESHOLD:
                    best_sim = float(llm_pct)
                    status = _content_status(lf, rf, read)
                    best = MatchResult(
                        lf, rf, 'llm_verified', best_sim,
                        content_status=status,
                    )
                    best_ri = ri
                    break

        if best is not None and best_ri is not None:
            logger.info(
                "Phase3b content match %s <-> %s : %.1f%%",
                lf.relative_path, best.right_file.relative_path, best_sim,
            )
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
