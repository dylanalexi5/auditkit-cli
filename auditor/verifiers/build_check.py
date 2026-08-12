import re
import subprocess
import sys
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext, normalize_dependency_name

_TIMEOUT_SECONDS = 300
_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?\.py)(?:::\S+)?(?:\s+-\s+(.*))?$")
_ERROR_DETAIL_LINE = re.compile(r"^E\s+(.+)$")
_MODULE_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_UNDECLARED_DEP_NOTE = "dependencia declarada no instalada - no verificado, no es un fallo real"


def _is_python_project(path: Path) -> bool:
    return any((path / marker).is_file() for marker in _PROJECT_MARKERS)


def verify(ctx: RepoContext) -> VerifierResult:
    if not _is_python_project(ctx.path):
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    result = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=ctx.path,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )

    if result.returncode == 0:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    output = result.stdout + result.stderr
    lines = output.splitlines()
    error_details = [m.group(1) for m in map(_ERROR_DETAIL_LINE.match, lines) if m]
    fallback_detail = error_details[0] if error_details else "pytest fallo"

    missing_module = next(
        (
            normalize_dependency_name(match.group(1))
            for detail in error_details
            if (match := _MODULE_NOT_FOUND.search(detail))
        ),
        None,
    )
    is_declared_missing_dep = (
        missing_module is not None and missing_module in ctx.declared_dependencies
    )

    evidence = [
        Evidence(
            file=match.group(1),
            line=1,
            note=(
                _UNDECLARED_DEP_NOTE
                if is_declared_missing_dep
                else (match.group(2) or fallback_detail).strip()
            ),
        )
        for match in map(_SUMMARY_LINE.match, lines)
        if match
    ]
    if not evidence:
        evidence = [
            Evidence(
                file="pytest",
                line=0,
                note=f"pytest fallo (exit {result.returncode}): {output[-2000:].strip()}",
            )
        ]

    verdict = (
        Verdict.APROBADO_CON_OBSERVACIONES if is_declared_missing_dep else Verdict.NO_SOSTENIBLE
    )
    return VerifierResult(verdict=verdict, evidence=evidence)
