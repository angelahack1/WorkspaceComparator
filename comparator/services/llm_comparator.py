# ╔════════════════════════════════════════════════════════════════════════╗
# ║        ✦ ✦ ✦   W O R K S P A C E   C O M P A R A T O R   ✦ ✦ ✦         ║
# ╠════════════════════════════════════════════════════════════════════════╣
# ║ Module  : comparator/services/llm_comparator.py                        ║
# ║ Author  : Ángela López Mendoza                                         ║
# ║ E-mail  : angela@xaiht.org                                             ║
# ║ © 2026 Ángela López Mendoza — All rights reserved.                     ║
# ╚════════════════════════════════════════════════════════════════════════╝
"""
LLM Comparator Module (Ollama)
-------------------------------
Last-resort comparison engine.  When the deterministic algorithm is
uncertain about whether two files correspond, this module sends both
file contents to an LLM (via Ollama at 127.0.0.1:11434, model
glm-5.2:cloud) with a carefully engineered prompt and asks for a single
integer 0-100 representing correspondence percentage.

Only invoked for genuinely ambiguous cases to minimise latency.
"""
import re
import logging
import requests

logger = logging.getLogger(__name__)

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_GENERATE = f"{OLLAMA_BASE}/api/generate"
MODEL_NAME = "glm-5.2:cloud"

# Maximum characters per file sent to the LLM (keeps context manageable)
MAX_FILE_CHARS = 6000

# Token budget for the reply.  Deliberately generous: if the "think"
# option below is not honoured (older Ollama), reasoning tokens count
# against this budget, and a tight limit (the old 16) cuts generation
# before the number is ever produced -> empty response -> the
# "LLM returned non-numeric answer" failure mode.
NUM_PREDICT = 256

# Whether to send the top-level "think": false parameter.  glm-5.2 is a
# thinking-capable model; without this it burns the token budget on
# reasoning.  Flipped off at runtime if the server rejects the param
# (HTTP 400 from older Ollama versions / non-thinking models).
_send_think_param = True

# -----------------------------------------------------------------------
# Prompt template
# -----------------------------------------------------------------------
PROMPT_TEMPLATE = """/no_think
You are an expert source-code analyst specialising in software-project
migration, refactoring detection, and codebase archaeology.

CONTEXT
-------
Two software projects are being compared.  The project structure may
have changed significantly between versions (e.g. plain Java -> Maven,
package renaming, build-system migration, framework upgrades).  Your
job is to decide whether FILE A and FILE B are *the same logical
component* -- that is, one is an evolved / migrated / refactored
version of the other.

EVALUATION RULES
----------------
1. **Functional purpose** -- do both files implement the same feature,
   service, algorithm, or domain concept?
2. **Core logic** -- are the fundamental algorithms, control flow, and
   data transformations equivalent?
3. **API surface** -- do they expose similar public methods, endpoints,
   or contracts?
4. **Naming signals** -- do class / struct / function names suggest the
   same intent even after renaming?
5. **Structural pattern** -- do they follow the same architectural role
   (DAO, controller, service, model, utility, etc.)?

DIFFERENCES TO IGNORE (expected in migrations)
------------------------------------------------
- Package / namespace / module path changes
- Import / include / use statement differences
- Build-system artefacts (pom.xml annotations, Cargo.toml metadata)
- Whitespace, indentation, and comment changes
- Minor API-version swaps  (javax.* <-> jakarta.*, Python 2 <-> 3)
- Logging-framework changes
- Variable / method renames that preserve semantic meaning

SCORING GUIDE
-------------
  90 -- 100  : Same file, minor edits only
  70 --  89  : Same component, notable modifications but clearly the
               same logical unit
  40 --  69  : Partially related; overlapping functionality but
               substantially different
   0 --  39  : Different files with different purposes

---------- FILE A: {filename_a} ----------
```
{content_a}
```

---------- FILE B: {filename_b} ----------
```
{content_b}
```

Based STRICTLY on the rules above, respond with **ONLY a single
integer** between 0 and 100 (the correspondence percentage).
No explanation.  No text.  Just the number."""


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def is_ollama_available() -> bool:
    """Quick health-check against the Ollama server."""
    try:
        r = requests.get(OLLAMA_BASE, timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def compare_with_llm(
    filename_a: str,
    content_a: str,
    filename_b: str,
    content_b: str,
) -> int:
    """
    Ask the LLM to score the correspondence between two source files.

    Returns
    -------
    int
        0-100 correspondence percentage, or **-1** when the LLM is
        unreachable or returns an unparseable answer.
    """
    prompt = PROMPT_TEMPLATE.format(
        filename_a=filename_a,
        content_a=_smart_truncate(content_a),
        filename_b=filename_b,
        content_b=_smart_truncate(content_b),
    )

    try:
        data = _post_generate(prompt)
        answer = (data.get("response") or "").strip()
        thinking = (data.get("thinking") or "").strip()
        logger.info("LLM raw answer for %s <-> %s: %r",
                     filename_a, filename_b, answer)

        pct = _parse_score(answer, thinking)
        if pct == -1:
            logger.warning(
                "LLM returned non-numeric answer: %r (thinking: %r)",
                answer, thinking[:200],
            )
        return pct

    except requests.exceptions.ConnectionError:
        logger.warning("Ollama not reachable at %s", OLLAMA_BASE)
        return -1
    except requests.exceptions.Timeout:
        logger.warning("Ollama request timed out")
        return -1
    except Exception as exc:
        logger.exception("Unexpected LLM error: %s", exc)
        return -1


# -----------------------------------------------------------------------
# Internals
# -----------------------------------------------------------------------

# <think> ... </think> blocks leaked into the visible response by
# thinking models (closing tag may be missing when generation is cut).
_THINK_BLOCK = re.compile(r'<think>.*?(?:</think>|$)', re.DOTALL)


def _post_generate(prompt: str) -> dict:
    """POST to Ollama /api/generate, disabling model 'thinking'.

    If the server rejects the "think" parameter (HTTP 400 on older
    Ollama versions or non-thinking models), retry once without it and
    remember the choice for the rest of the process.
    """
    global _send_think_param

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.05,
            "num_predict": NUM_PREDICT,
        },
    }
    if _send_think_param:
        payload["think"] = False

    resp = requests.post(OLLAMA_GENERATE, json=payload, timeout=120)
    if resp.status_code == 400 and _send_think_param:
        logger.info("Ollama rejected 'think' parameter -- retrying without it")
        _send_think_param = False
        payload.pop("think", None)
        resp = requests.post(OLLAMA_GENERATE, json=payload, timeout=120)

    resp.raise_for_status()
    return resp.json()


def _parse_score(answer: str, thinking: str = '') -> int:
    """Extract the 0-100 score from a model reply.

    Tolerates leaked reasoning: strips <think> blocks, and when the
    visible response is empty falls back to the separate 'thinking'
    text.  When extra prose surrounds the number, the LAST number wins
    (reasoning ends with the conclusion; the first number is usually a
    rule reference or line quote).
    """
    cleaned = _THINK_BLOCK.sub('', answer).strip()
    if not cleaned:
        cleaned = thinking.strip()
    if not cleaned:
        return -1

    if re.fullmatch(r'\d+', cleaned):
        return max(0, min(100, int(cleaned)))

    # "85/100" or "85 out of 100": drop the denominator so the last
    # number is the score, not the scale.
    cleaned = re.sub(r'(?i)\s*(?:/|out\s+of)\s*100\b', '', cleaned)

    numbers = re.findall(r'\d+', cleaned)
    if numbers:
        return max(0, min(100, int(numbers[-1])))
    return -1


def _smart_truncate(content: str, limit: int = MAX_FILE_CHARS) -> str:
    """
    Truncate long files while keeping the most informative parts:
      - first  40 %  (imports, declarations, class header)
      - middle 35 %  (core logic)
      - last   25 %  (closing implementations)
    """
    if len(content) <= limit:
        return content

    head_n = int(limit * 0.40)
    mid_n = int(limit * 0.35)
    tail_n = limit - head_n - mid_n - 40  # 40 chars for separators

    head = content[:head_n]
    mid_start = len(content) // 2 - mid_n // 2
    mid = content[mid_start:mid_start + mid_n]
    tail = content[-tail_n:]

    return (
        f"{head}\n/* ... truncated ... */\n"
        f"{mid}\n/* ... truncated ... */\n"
        f"{tail}"
    )
