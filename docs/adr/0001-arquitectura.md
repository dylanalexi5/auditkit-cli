# ADR 0001: Arquitectura de auditkit-cli

## Contexto

Auditor que recibe una URL de GitHub y verifica si el README dice la verdad,
contrastándolo contra código, tests y dependencias reales. CLAUDE.md fija: 4
verificadores (secretos, readme_check, build_check, deps_check), cada uno un
módulo testeable en aislamiento, veredicto estructurado APROBADO /
APROBADO_CON_OBSERVACIONES / NO_SOSTENIBLE + evidencia file:línea. Lenguaje:
Python (comandos declarados son pytest / ruff / `python -m auditor`).

## Módulos

```
auditor/
  cli.py              # entry point: python -m auditor <url>
  core/
    models.py         # Evidence, VerifierResult, Verdict (dataclasses/enum)
    repo_context.py   # clona el repo, expone path local, README, lenguaje detectado
    orchestrator.py    # corre los 4 verificadores sobre el mismo RepoContext, agrega
  verifiers/
    secrets.py
    readme_check.py
    build_check.py
    deps_check.py
  report.py           # arma el reporte final (markdown/json) a partir de los VerifierResult
```

**Comunicación entre módulos:** pipeline lineal, sin bus de eventos ni async.
`orchestrator.py` construye un `RepoContext` y lo pasa por parámetro a cada
verificador. Cada verificador es una función pura
`verify(ctx: RepoContext) -> VerifierResult`, sin depender de los otros — así
cada uno es testeable aislado con un `RepoContext` de fixture, sin correr el
pipeline completo. El orchestrator solo agrega veredictos (el peor gana) y
arma el reporte.

**Clonado del repo:** `subprocess` + `git clone --depth 1` (stdlib, no
GitPython — es un shallow clone de una sola vez, no necesitamos abstracción
de objetos git).

## Librerías externas por verificador

| Verificador | Librería | Por qué esta y no otra |
|---|---|---|
| `secrets.py` | **detect-secrets** (Yelp) | Pip-instalable, corre in-process (no binario externo que gestionar como gitleaks/trufflehog), salida con archivo:línea:tipo de secreto lista para el veredicto estructurado. Plugins de entropía + regex ya maduros — no reinventar detección de secretos. |
| `readme_check.py` | **ninguna nueva** — `ast` + `re` (stdlib) | Comparar afirmación (README) contra código real es el corazón del producto — delegar esa lógica a una lib de terceros sería tercerizar el producto. `ast` extrae funciones/clases/entrypoints reales del código (verdad); regex/parsing simple (stdlib) extrae afirmaciones del README. |
| `build_check.py` | **ninguna nueva** — `subprocess` (stdlib) | La verdad de "¿compila/pasan los tests?" es correr el comando real (pytest, npm test, make...). Ninguna librería reemplaza mejor la ejecución real que ejecutarla. |
| `deps_check.py` | **pip-audit** + `importlib.metadata`/`ast` (stdlib) | pip-audit verifica que las dependencias declaradas existan de verdad en PyPI y no tengan vulnerabilidades conocidas. `importlib.metadata` + `ast` detectan dependencias fantasma o no declaradas. |

Ninguna otra dependencia nueva: no ORM, no framework CLI (`argparse` stdlib
basta), no motor de reportes (markdown armado a mano desde los
`VerifierResult`).

## Limitaciones conocidas

- **Solo se audita HEAD, no el historial de git.** El clonado usa `git clone
  --depth 1` (shallow). Un secreto removido del código actual pero
  recuperable en un commit viejo por SHA directa (aunque ya no aparezca en
  `git log`) **no será detectado** por `secrets.py` en este MVP. Es una
  decisión consciente de scope por simplicidad/velocidad — no un descuido, y
  no es la misma garantía que ofrecería un scan de historial completo. Si se
  necesita auditar historial completo, cambiar a clone sin `--depth`
  (trade-off: mucho más lento y pesado en repos grandes).
- **Solo se auditan repos Python.** Aplica a todo el MVP como una sola
  limitación de scope, no una por verificador: `readme_check.py` usa `ast`
  (solo parsea Python) para extraer la verdad del código, y `deps_check.py`
  usa pip-audit (ecosistema PyPI). Repos en otros lenguajes quedan fuera de
  alcance hasta que se pida.
- **`readme_check.py` valida solo existencia de tests, no que pasen.** Cuenta
  funciones `test_*` reales vía `ast` para contrastar contra una afirmación
  de coverage en el README — confirma que hay una suite de tests, no que esa
  suite pase. Que los tests efectivamente pasen es responsabilidad de
  `build_check.py`, que corre el comando real y captura el resultado.
- **`readme_check.py` solo cubre afirmaciones de coverage (`N% coverage`).**
  Es el único tipo de afirmación verificado en este MVP — no badges de CI,
  no frases como "production-ready" o "battle-tested", no conteo de
  features. Ampliar la cobertura de afirmaciones queda fuera de alcance
  hasta que se pida.

## Verificación

- `pytest` corre y pasa sobre los módulos de `verifiers/` usando `RepoContext`
  de fixture (repos de prueba con secretos conocidos, README con afirmaciones
  falsas conocidas, build que falla a propósito, dep fantasma a propósito).
- `ruff check .` limpio.
- `python -m auditor <url-real>` sobre un repo público real, pegar la salida
  del reporte con veredictos y evidencia file:línea.
