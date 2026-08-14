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

## Veredictos de deps_check.py

| Situación | Veredicto |
|---|---|
| Vulnerabilidad real conocida (pip-audit, paquete declarado) | `NO_SOSTENIBLE` |
| Import usado sin declarar, con mapeo conocido import↔paquete | `APROBADO_CON_OBSERVACIONES` |
| Import usado sin declarar, de herramienta de build/automatización (`nox`, `tox`…) | `APROBADO_CON_OBSERVACIONES` |
| Import usado sin declarar, sin mapeo conocido | `NO_SOSTENIBLE` |
| Paquete declarado pero no usado en el código | `APROBADO_CON_OBSERVACIONES` |

El peor veredicto entre todos los hallazgos gana (mismo criterio que el
orchestrator aplicará entre verificadores).

### Mapeos conocidos import↔paquete (lista abierta, no exhaustiva)

| Se importa como | Se declara como |
|---|---|
| `sklearn` | `scikit-learn` |
| `bs4` | `beautifulsoup4` |
| `PIL` | `Pillow` |
| `yaml` | `PyYAML` |
| `cv2` | `opencv-python` |

Estos son los casos más comunes donde el nombre de import y el nombre del
paquete en PyPI divergen. La lista se amplía cuando aparezca un caso real,
no se intenta cubrir todos los mismatches posibles del ecosistema.

### Qué NO se reporta como "declarado pero no se usa"

Tres clases de falso positivo detectadas corriendo el auditor contra repos
públicos ajenos (`pypa/sampleproject`, `anxolerd/dvpwa`) y contra sí mismo:

| Caso | Por qué no es un hallazgo |
|---|---|
| Paquete propio del repo en `src/<nombre>/` | Es el código del repo, no una dependencia. `_local_top_level_names()` escanea la raíz **y** `src/`, más el `name` declarado en `[project]`/`[tool.poetry]`. Sin esto el repo se reporta a sí mismo como import no declarado — un `NO_SOSTENIBLE` falso. |
| Pin transitivo anotado `# via X` | Marca que dejan `pip-compile` y los `requirements.txt` anotados. El archivo mismo declara quién arrastra la dependencia; no es una dep directa que el código deba importar. |
| Paquete de herramienta CLI (`ruff`, `coverage`, `nox`, `pytest`, `pip-audit`…) | Se invocan como comando, nunca se importan. Buscar su `import` y no encontrarlo no prueba nada. Lista abierta en `_CLI_ONLY_PACKAGES`. |

Las tres son exclusiones deliberadamente conservadoras: solo suprimen
observaciones (`APROBADO_CON_OBSERVACIONES`) o un `NO_SOSTENIBLE` que era
falso por construcción. Ninguna suprime una vulnerabilidad real de pip-audit.

### Herramientas de build/automatización importadas sin declarar

`nox`, `tox` y `make` son herramientas de automatización: para que el script
que las invoca pueda correr, tienen que estar instaladas **antes** que el
proyecto y por fuera de él. Un `noxfile.py` con `import nox`, o un `tox.ini`,
describen cómo se automatiza el repo — no son código de la aplicación, y su
herramienta no es una dependencia de runtime que el repo deba declarar en
`requirements.txt`/`pyproject.toml`. `pypa/sampleproject`, el ejemplo canónico
de la Python Packaging Authority, tiene exactamente esa forma: `noxfile.py`
importa `nox` y `nox` no está declarado en ningún lado.

Es el mismo tratamiento que `ruff`, `coverage` y `check-manifest`, con la
única diferencia de la dirección en que aparecen: aquellos suelen estar
declarados y no importarse, mientras que `nox`/`tox` suelen importarse sin
estar declarados. Ambas direcciones consultan la misma lista abierta,
`_CLI_ONLY_PACKAGES`.

**Veredicto: `APROBADO_CON_OBSERVACIONES`, no `APROBADO`.** Un import sin
declarar sigue siendo un dato que quien lee el reporte debería ver — que la
herramienta sea de build explica por qué no está declarada, no borra el
hecho de que correr ese script requiere instalarla aparte. Lo que deja de
ser es `NO_SOSTENIBLE`: no es un repo roto.

`make` se documenta acá por completitud del criterio, pero no aparece en la
lista: no es un paquete de PyPI y no existe `import make`, así que nunca
llega a este verificador.

### Falsos positivos encontrados contra pallets/click y psf/black

Tres clases más, encontradas auditando dos repos externos reales y grandes
(no toy repos):

| Caso | Verificador | Por qué no es un hallazgo |
|---|---|---|
| `.pre-commit-config.yaml` | `secrets.py` | Cada hook declara `rev: <sha git de 40 hex>` ("frozen" a un commit) — es un hash de versión, no un secreto. Excluido por nombre de archivo. |
| Metadata de `.ipynb` (hash de intérprete, uuid de celda estilo Kaggle) | `secrets.py` | detect-secrets no tiene plugin para notebooks. La metadata de Jupyter está llena de hex que parece secreto sin serlo — nunca la escribió un humano. Se sanitiza cada celda a un archivo temporal con solo su `source` antes de escanear; un secreto de verdad pegado en una celda de código sí se detecta. |
| `import foo`/`import hello` en `tests/data/cases/*.py` | `deps_check.py` | Código de ejemplo usado como dato de entrada de un test (formateadores, parsers), no dependencias reales. `tests/data/` y `tests/fixtures/` (a cualquier profundidad) se excluyen del `ast` scan. |
| Import dentro de `if TYPE_CHECKING:` | `deps_check.py` | Nunca corre en runtime, solo lo lee un type-checker. `_RuntimeImportVisitor` reconoce el guard (`Name` o `Attribute` terminado en `TYPE_CHECKING`, cualquier alias) y no desciende a su body. |
| `[dependency-groups]` (PEP 735) sin leer | `deps_check.py`/`repo_context.py` | Tabla top-level nueva del ecosistema (`uv`, entre otros) para declarar grupos de deps — pallets/click la usa para sus deps de docs en vez de `[project.optional-dependencies]`. Agregada al parseo de `pyproject.toml`. |

Mismo criterio que la sección anterior: exclusiones conservadoras, ninguna
suprime una vulnerabilidad real ni un import genuinamente no declarado fuera
de estos casos puntuales. Quedan hallazgos genuinos en ambos repos que no son
bugs del auditor (un hash de ejemplo en un docstring de `black`, Pillow sin
declarar en un script de `examples/` de `click`) — se reportan tal cual, no
se suprimen.

## Veredictos de build_check.py: "no verificado" vs. "corridos y fallaron"

`APROBADO` de `build_check` no significa siempre lo mismo, y la diferencia
importa: un veredicto blando porque *no pudimos comprobar* no es lo mismo que
uno porque *comprobamos y está bien*. Estados exactos:

| Situación | Veredicto | ¿Se corrió pytest? | Significado |
|---|---|---|---|
| Sin `pyproject.toml` / `setup.py` / `setup.cfg` | `APROBADO` | **No** | **No verificado.** No se detectó proyecto Python, no se ejecutó nada. |
| `pytest` termina con exit 0 | `APROBADO` | Sí | **Verificado: los tests corrieron y pasaron.** |
| `ModuleNotFoundError` de un paquete declarado en `requirements.txt`/`pyproject.toml` | `APROBADO_CON_OBSERVACIONES` | Sí, falló al importar | **No verificado.** Falta una dependencia externa que deliberadamente no instalamos; no es un fallo del repo. |
| `pytest` falla por cualquier otra causa | `NO_SOSTENIBLE` | Sí | **Verificado: los tests corrieron y fallaron.** |

El `ModuleNotFoundError` del **paquete propio del repo** ya no cae en ninguna
de estas filas: `_pytest_env()` agrega la raíz y `src/` al `PYTHONPATH` antes
de correr pytest, así que el paquete del repo es importable sin instalarlo.
Antes de eso, cualquier repo con layout `src/` (incluido `pypa/sampleproject`,
cuya suite pasa perfectamente) se reportaba `NO_SOSTENIBLE` sin estar roto.

Se descartó `pip install -e .` para lograrlo: instalar el repo ejecuta su
build backend — código del repo auditado, la misma clase de RCE que este ADR
ya documenta — y además resuelve y descarga sus dependencias externas. Extender
`PYTHONPATH` cubre el paquete propio sin ejecutar nada extra ni tocar la red.

**Limitación que queda abierta:** la primera fila de la tabla sigue
devolviendo `APROBADO` para "no verificado", igual que la segunda para
"verificado y pasó". El reporte no distingue ambos casos hoy. Es la misma
confusión que `deps_check._run_pip_audit()` sí evita (devuelve `None` para "no
se pudo verificar", distinto de `[]` para "verificado, limpio"). Queda
registrada acá como deuda conocida, no como comportamiento deseado.

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
- **`build_check.py` nunca instala las dependencias del repo auditado.**
  Corre `pytest` en el entorno del propio auditor, sin `pip install` previo
  del repo clonado. Sí hace importable el paquete propio del repo vía
  `PYTHONPATH` (raíz + `src/`), que no requiere instalar ni ejecutar nada —
  ver la tabla de veredictos arriba. Por eso no puede detectar fallos que solo aparecen con
  las librerías reales instaladas (bugs de integración, versiones
  incompatibles) — solo detecta si un import falla o no. Para mitigar el
  falso negativo más obvio (marcar como roto un repo sano solo porque no
  instalamos sus deps), un `ModuleNotFoundError` de un paquete declarado en
  `requirements.txt`/`pyproject.toml` (`RepoContext.declared_dependencies`)
  se reporta como `APROBADO_CON_OBSERVACIONES`, no `NO_SOSTENIBLE` — la
  ausencia de instalación queda visible como observación, no como fallo
  real del repo.
- **`build_check.py` ejecuta código no confiable del repo auditado, sin
  sandbox.** `subprocess.run([sys.executable, "-m", "pytest"], cwd=ctx.path)`
  hace que pytest importe y corra `conftest.py` y cada `test_*.py` que
  colecta — cualquier código ahí corre con los privilegios del propio
  proceso auditor (filesystem, red, todo). Un repo malicioso con
  `import os; os.system(...)` en un test alcanza para ejecutar código
  arbitrario. Es un riesgo real y conocido, no un bug parcheable: es
  inherente a "correr el comando de test real" sobre código no confiable.
  Sandboxing (contenedor efímero sin red ni privilegios, filesystem de solo
  lectura salvo `/tmp`) queda fuera de alcance del MVP — decisión de riesgo
  aceptado, no descuido.
- **El downgrade a `APROBADO_CON_OBSERVACIONES` en `build_check.py` confía
  en `requirements.txt` del propio repo auditado.** `declared_dependencies`
  lo controla por completo el repo que se está auditando — puede declarar
  cualquier nombre plausible para convertir un `NO_SOSTENIBLE` real
  (dependencia no declarada, bug real) en un veredicto blando. Es una
  limitación de scope conocida: el MVP asume buena fe en lo declarado, no
  verifica que el paquete exista de verdad antes de aceptar el downgrade.
- **`deps_check.py` necesita acceso a red** (consulta PyPI/OSV vía
  pip-audit para vulnerabilidades reales) — a diferencia de los otros tres
  verificadores, que corren completamente offline sobre el repo clonado.

## Verificación

- `pytest` corre y pasa sobre los módulos de `verifiers/` usando `RepoContext`
  de fixture (repos de prueba con secretos conocidos, README con afirmaciones
  falsas conocidas, build que falla a propósito, dep fantasma a propósito).
- `ruff check .` limpio.
- `python -m auditor <url-real>` sobre un repo público real, pegar la salida
  del reporte con veredictos y evidencia file:línea.
