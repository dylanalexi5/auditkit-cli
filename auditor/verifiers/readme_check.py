import ast
import re
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext

_COVERAGE_CLAIM = re.compile(r"\d{1,3}%\s*(?:test\s*)?coverage", re.IGNORECASE)
_README_NAMES = ("README.md", "README.rst", "README.txt", "README")


def _find_readme(path: Path) -> Path | None:
    for name in _README_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def _count_test_functions(path: Path) -> int:
    count = 0
    for test_file in path.rglob("test_*.py"):
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="ignore"))
        count += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )
    return count


def verify(ctx: RepoContext) -> VerifierResult:
    readme_path = _find_readme(ctx.path)
    if readme_path is None:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
    claims = [
        (line_number, match.group(0))
        for line_number, line in enumerate(readme_text.splitlines(), start=1)
        for match in [_COVERAGE_CLAIM.search(line)]
        if match
    ]
    if not claims:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    if _count_test_functions(ctx.path) > 0:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    evidence = [
        Evidence(
            file=readme_path.name,
            line=line_number,
            note=f'README afirma "{claim}" pero no hay funciones de test en el repo',
        )
        for line_number, claim in claims
    ]
    return VerifierResult(verdict=Verdict.NO_SOSTENIBLE, evidence=evidence)
