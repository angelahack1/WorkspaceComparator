"""Content-aware language/format hints for deterministic and LLM comparison.

Extensions are useful clues, never authority.  Content signatures are
scored first so a text file called ``program.exe`` that contains Java
is described as Java to the LLM.  Unknown formats fall back to generic
text analysis instead of becoming unsupported.
"""
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


_EXTENSION_GROUPS = {
    'Java': {'.java'},
    'C': {'.c', '.h'},
    'C++': {'.cc', '.cpp', '.cxx', '.c++', '.hh', '.hpp', '.hxx', '.h++', '.ixx', '.cppm'},
    'CUDA': {'.cu', '.cuh'},
    'OpenCL': {'.cl'},
    'C#': {'.cs', '.csx'},
    'Objective-C': {'.m', '.mm'},
    'Assembly': {'.asm', '.s', '.S', '.inc', '.nasm', '.masm'},
    'Rust': {'.rs'},
    'Go': {'.go'},
    'Python': {'.py', '.pyw', '.pyi', '.pyx'},
    'JavaScript': {'.js', '.jsx', '.mjs', '.cjs'},
    'TypeScript': {'.ts', '.tsx', '.mts', '.cts'},
    'PHP': {'.php', '.php3', '.php4', '.php5', '.phtml', '.phps'},
    'Perl': {'.pl', '.pm', '.pod', '.t'},
    'Ruby': {'.rb', '.rake', '.gemspec'},
    'Shell': {'.sh', '.bash', '.zsh', '.fish', '.ksh'},
    'PowerShell': {'.ps1', '.psm1', '.psd1'},
    'Batch': {'.bat', '.cmd'},
    'Kotlin': {'.kt', '.kts'},
    'Scala': {'.scala', '.sc'},
    'Swift': {'.swift'},
    'Dart': {'.dart'},
    'Groovy': {'.groovy', '.gradle'},
    'Lua': {'.lua'},
    'R': {'.r', '.rmd'},
    'Julia': {'.jl'},
    'Fortran': {'.f', '.for', '.f77', '.f90', '.f95', '.f03', '.f08'},
    'COBOL': {'.cob', '.cbl', '.cpy'},
    'Pascal': {'.pas', '.pp', '.inc'},
    'Ada': {'.ada', '.adb', '.ads'},
    'Haskell': {'.hs', '.lhs'},
    'OCaml': {'.ml', '.mli'},
    'Lisp': {'.lisp', '.lsp', '.cl', '.el', '.scm', '.ss', '.rkt'},
    'Erlang': {'.erl', '.hrl'},
    'Elixir': {'.ex', '.exs'},
    'SQL': {'.sql', '.ddl', '.dml'},
    'HTML': {'.html', '.htm', '.xhtml', '.shtml', '.jsp', '.jspx', '.asp', '.aspx', '.cshtml'},
    'XML': {'.xml', '.xsd', '.xsl', '.xslt', '.svg', '.plist', '.pom'},
    'Markdown': {'.md', '.markdown', '.mdown', '.mkd', '.mdx'},
    'JSON': {'.json', '.json5', '.jsonc', '.geojson', '.ipynb'},
    'YAML': {'.yaml', '.yml'},
    'TOML': {'.toml'},
    'INI/config': {'.ini', '.cfg', '.conf', '.config', '.properties', '.env'},
    'CSS': {'.css', '.scss', '.sass', '.less'},
    'GraphQL': {'.graphql', '.gql'},
    'Protocol Buffers': {'.proto'},
    'WebAssembly text': {'.wat', '.wast'},
    'WGSL shader': {'.wgsl'},
    'GLSL shader': {'.glsl', '.vert', '.frag', '.geom', '.tesc', '.tese', '.comp'},
    'HLSL shader': {'.hlsl', '.fx', '.fxh'},
    'Make/build': {'.mk', '.make', '.cmake', '.ninja', '.bazel', '.bzl'},
}

EXTENSION_LANGUAGE: Dict[str, str] = {
    ext.lower(): language
    for language, extensions in _EXTENSION_GROUPS.items()
    for ext in extensions
}

_CONTENT_SIGNALS: Dict[str, Tuple[Tuple[str, int], ...]] = {
    'CUDA': ((r'\b__(?:global|device|host|shared)__\b', 5), (r'\bcuda(?:Malloc|Memcpy|Free)\b|<<<', 4)),
    'Java': ((r'(?m)^\s*package\s+[\w.]+\s*;', 4), (r'(?m)^\s*import\s+java[\w.]*\s*;', 4),
             (r'\bpublic\s+(?:class|interface|enum|record)\s+\w+', 3), (r'\bSystem\.out\.', 2)),
    'C#': ((r'(?m)^\s*using\s+System(?:\.|;)', 5), (r'\bnamespace\s+[\w.]+\s*[;{]', 3),
           (r'\b(?:async\s+)?Task(?:<[^>]+>)?\b', 2)),
    'Rust': ((r'(?m)^\s*(?:pub\s+)?fn\s+\w+', 4), (r'(?m)^\s*use\s+(?:std|crate|super)::', 4),
             (r'\b(?:impl|trait)\s+\w+|\blet\s+mut\b', 2)),
    'Go': ((r'(?m)^\s*package\s+\w+\s*$', 4), (r'(?m)^\s*func\s+(?:\([^)]*\)\s*)?\w+\s*\(', 4),
           (r'(?m)^\s*import\s*(?:\(|")', 2), (r':=', 1)),
    'Python': ((r'(?m)^#!.*\bpython\d*\b', 6), (r'(?m)^\s*(?:async\s+)?def\s+\w+\s*\(', 3),
               (r'(?m)^\s*(?:from\s+\S+\s+)?import\s+', 2), (r"if\s+__name__\s*==\s*['\"]__main__['\"]", 4)),
    'PHP': ((r'<\?php\b', 7), (r'(?m)^\s*(?:namespace|use)\s+[\\\w]+', 3), (r'\$[A-Za-z_]\w*', 1)),
    'Perl': ((r'(?m)^#!.*\bperl\b', 6), (r'(?m)^\s*use\s+(?:strict|warnings)\s*;', 5),
             (r'\bmy\s+[$@%][A-Za-z_]\w*', 3)),
    'C++': ((r'(?m)^\s*#\s*include\s*<(?:iostream|vector|string|memory|algorithm)>', 4),
            (r'\bstd::|\bnamespace\s+\w+\s*{|\btemplate\s*<', 3), (r'\bclass\s+\w+\s*(?::[^\n{]+)?{', 2)),
    'C': ((r'(?m)^\s*#\s*include\s*<[\w./]+\.h>', 3), (r'\btypedef\s+struct\b', 3),
          (r'\b(?:printf|malloc|free)\s*\(', 2)),
    'TypeScript': ((r'(?m)^\s*(?:export\s+)?(?:interface|type)\s+\w+', 4),
                   (r'\b(?:string|number|boolean)\s*[;,)={]', 2), (r'(?m)^\s*import\s+.+\s+from\s+["\']', 2)),
    'JavaScript': ((r'(?m)^\s*(?:export\s+)?(?:async\s+)?function\s+\w+', 3),
                   (r'(?m)^\s*(?:const|let|var)\s+\w+\s*=', 2), (r'=>', 1)),
    'Assembly': ((r'(?m)^\s*(?:global|section)\s+[._A-Za-z]', 4),
                 (r'(?im)^\s*(?:mov|lea|push|pop|jmp|call|ret)\s+', 2),
                 (r'\b(?:eax|ebx|ecx|edx|rax|rbx|rsp|x0|r0)\b', 2)),
    'WGSL shader': ((r'@(vertex|fragment|compute)\b', 5), (r'\bvar<(?:uniform|storage|private|workgroup)>', 3)),
    'GLSL shader': ((r'(?m)^\s*#version\s+\d+', 5), (r'\bgl_(?:Position|FragCoord|GlobalInvocationID)\b', 4)),
    'HTML': ((r'(?is)<!doctype\s+html\b', 7), (r'(?is)<(?:html|head|body|script|div)\b', 3)),
    'XML': ((r'(?s)^\s*<\?xml\b', 7), (r'(?s)^\s*<[A-Za-z_][\w:.-]*(?:\s|>|/>)', 2)),
    'JSON': ((r'(?s)^\s*[\[{]\s*["\'][^\n:]+["\']\s*:', 4), (r'"[^"\n]+"\s*:\s*(?:["\d\[{]|true|false|null)', 2)),
    'Markdown': ((r'(?m)^#{1,6}\s+\S', 3), (r'(?m)^```\w*\s*$', 3), (r'\[[^\]]+\]\([^)]+\)', 2)),
    'SQL': ((r'(?im)^\s*(?:select|insert|update|delete|create|alter)\b', 4),
            (r'(?i)\b(?:from|where|join|table|values)\b', 1)),
    'CSS': ((r'(?m)^\s*[@.#]?[A-Za-z][^{\n]*\{\s*$', 2), (r'(?m)^\s*[\w-]+\s*:\s*[^;]+;', 2)),
    'Shell': ((r'(?m)^#!.*\b(?:ba|z|k|fi)?sh\b', 6), (r'\$\([^)]+\)|\$\{[^}]+\}', 2)),
}

_GUIDANCE = {
    'Java': 'Compare package-independent classes, interfaces, methods, annotations, and JVM behavior.',
    'C': 'Compare functions, structs, macros, headers, memory behavior, and ABI-facing declarations.',
    'C++': 'Compare classes, templates, namespaces, functions, ownership, and public API behavior.',
    'CUDA': 'Compare host/device functions, kernels, launch geometry, memory transfers, and algorithm intent.',
    'Assembly': 'Compare labels, control flow, register/data movement, calling convention, and observable behavior.',
    'Perl': 'Compare packages, subroutines, regex/data transformations, side effects, and script purpose.',
    'PHP': 'Compare namespaces, classes/functions, request/data flow, templates, and observable behavior.',
    'Markdown': 'Compare document structure, headings, lists, links, code blocks, and semantic prose changes.',
    'JSON': 'Compare keys, nesting, arrays, values, and schema meaning rather than formatting or key order alone.',
    'HTML': 'Compare DOM structure, attributes, embedded code, forms, and rendered semantic purpose.',
    'XML': 'Compare element/attribute structure, namespaces, values, and schema/build meaning.',
    'SQL': 'Compare schema/query intent, selected data, predicates, joins, mutations, and transactional behavior.',
    'Generic text': 'Infer structure from tokens, repeated sections, declarations, keys, and semantic purpose.',
}


@dataclass(frozen=True)
class TextProfile:
    language: str
    source: str
    confidence: str
    extension: str
    encoding: str

    def prompt_line(self, side: str) -> str:
        ext = self.extension or '(none)'
        guidance = _GUIDANCE.get(
            self.language,
            'Compare language-appropriate structure, declarations, control flow, data, and semantic purpose.',
        )
        return (
            f"FILE {side}: content profile={self.language}; detection={self.source} "
            f"({self.confidence}); filename extension hint={ext}; charset={self.encoding or 'unknown'}. "
            f"{guidance}"
        )


def _score_signals(content: str) -> Iterable[Tuple[int, str]]:
    for language, signals in _CONTENT_SIGNALS.items():
        score = sum(weight for pattern, weight in signals if re.search(pattern, content))
        if score:
            yield score, language


def detect_text_profile(filename: str, content: str, encoding: str = '') -> TextProfile:
    """Detect a text language/format with content taking precedence."""
    extension = os.path.splitext(filename)[1].lower()
    ranked = sorted(_score_signals(content[:20000]), reverse=True)
    if ranked and ranked[0][0] >= 4:
        score, language = ranked[0]
        confidence = 'high' if score >= 6 else 'medium'
        return TextProfile(language, 'content', confidence, extension, encoding)

    language = EXTENSION_LANGUAGE.get(extension)
    if language:
        return TextProfile(language, 'extension hint', 'medium', extension, encoding)
    return TextProfile('Generic text', 'content-generic fallback', 'low', extension, encoding)


def build_dynamic_system_prompt(left: TextProfile, right: TextProfile) -> str:
    """Build the per-candidate Ollama system message."""
    return "\n".join((
        'You are a source-code and structured-text correspondence analyst.',
        'The application has already verified that both inputs are text, not native binary data.',
        'Treat extensions only as filename hints. Content detection has priority when they disagree.',
        left.prompt_line('A'),
        right.prompt_line('B'),
        'Decide whether the files are the same logical component after migration, rename, format conversion, or refactoring.',
        'Never refuse analysis merely because a format or extension is unknown.',
    ))
