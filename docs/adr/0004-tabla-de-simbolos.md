# ADR 0004: Tabla de símbolos y verificación de los ejemplos de uso del README

## Contexto

El ADR 0001 prometía, en su tabla de librerías:

> `readme_check.py` | **ninguna nueva** — `ast` + `re` (stdlib) | … Se usa `ast`
> para extraer funciones/clases/entrypoints reales del código (verdad)

Eso nunca se construyó. `readme_check.py` eran 59 líneas con **un** regex
(`N% coverage`) contrastado contra **un** booleano: *¿existe alguna función
`test_*`?*. La "verdad del código" contra la que se comparaba el README no
tenía ni nombres ni ubicaciones.

Ese hueco tiene consecuencias más allá de `readme_check`. El experimento del
ADR 0003 (cruce semántico afirmación↔evidencia) fracasó en parte **porque no
había contra qué cruzar**: la única evidencia disponible eran notas de
higiene de dependencias, y por eso terminó emparejando *"PEP 8 compliant
formatter"* con *"'hatch_vcs' está declarado pero no se usa"*. La conclusión
de aquel ADR fue que **la recuperación localiza, no juzga**. Este ADR
construye lo que sí puede juzgar: hechos estructurales, deterministas y
abribles.

## Decisión 1 — Extiende `readme_check.py`, no es un verificador nuevo

Es el mismo concern (contrastar el README contra el código real), el mismo
input (el README), la misma firma `verify(ctx) -> VerifierResult` y el mismo
lugar en el reporte. `CLAUDE.md` fija "un módulo por verificador" nombrando a
`readme_check` como *el* verificador de README-contra-código; partirlo en dos
fragmentaría un solo concern en dos secciones del reporte que el lector
tendría que volver a juntar mentalmente.

El ADR 0001 ya había declarado fuera de alcance ampliar los tipos de
afirmación *"hasta que se pida"*. Se pidió.

## Decisión 2 — La tabla vive en `core/`, no en `verifiers/`

`auditor/core/symbol_index.py`. No es un verificador: no emite veredicto ni
evidencia, no conoce `Verdict`. Devuelve un hecho —qué nombres define cada
paquete y en qué `archivo:línea`— y quien lo use decide qué significa que un
nombre falte. Es la misma separación que ya tienen `repo_context.py` y
`semantic_client.py`.

Efecto colateral: `_is_test_fixture_path` se movió de `deps_check.py` a
`repo_context.py` como `is_test_fixture_path`. Ahora lo comparten dos
consumidores y `core/` no puede depender de `verifiers/`. Mismo precedente
que `declared_project_names`.

## Decisión 3 — Indexado por paquete, y solo definiciones de nivel superior

**Esta la decidió un control positivo que falló, no el diseño previo.**

La primera versión devolvía un único diccionario plano con todos los nombres
del repo, vía `ast.walk`. Parecía funcionar: 0 falsos positivos sobre `click`,
`black`, `requests` y el propio `auditkit-cli`.

Entonces se hizo el control positivo: se renombró `def echo(` en
`pallets/click` —la función que su propio README usa como ejemplo— y el
verificador **siguió diciendo que `click.echo` existía**. Resolvía contra
`tests/test_utils/test_prompt.py:94`, una función de test que se llamaba
igual. `click.command` también "existía", por un método enterrado en una
clase de `core.py`, no por el decorador real de `decorators.py`.

O sea: un repo grande satisface casi cualquier nombre por accidente. La tabla
plana no medía *"esto es parte de la API del proyecto"* sino *"esta cadena
aparece en algún lado"*. **Los 0 falsos positivos eran en parte un artefacto
de que el chequeo era casi inerte.**

De ahí las dos reglas:

1. **Resolución por paquete raíz.** `click.echo` se busca en el espacio de
   `click`. Los archivos de `tests/` caen bajo la raíz `tests` y no pueden
   responder por `click`.
2. **Solo `tree.body`, no `ast.walk`.** `click.echo` significa un nombre
   exportado por el paquete: un `def`, un `class` o una asignación de módulo.
   Las reexportaciones (`from .utils import echo` en `__init__.py`) quedan
   cubiertas igual, porque `echo` sí es de nivel superior en `utils.py`.

Con eso, el control positivo pasa:

```
README.md:32 — el ejemplo de uso del README cita 'click.echo' pero 'echo'
               no está definido en el paquete 'click'
```

## Decisión 4 — Solo se revisa lo que el README le atribuye al proyecto

Un README normal está lleno de código que no es del proyecto: `os.path.join`,
`pd.DataFrame()`, el `requests.get()` de un ejemplo de integración. Decir que
"no existen" sería una acusación falsa sobre código que ni siquiera vive acá.

Se revisan dos formas, y solo cuando el módulo pertenece al espacio propio
(nombre declarado en `pyproject` ∪ paquetes presentes en el árbol):

- `from <paquete> import X`
- `<paquete>.X`

**Fuera de alcance, explícito:** afirmaciones de calidad ("PEP 8 compliant"),
sociales ("4M repos depend on us"), dinámicas ("thread-safe") y comparativas.
Ninguna es falsable desde `ast`, y este verificador no las toca.

## Decisión 5 — Precisión sobre recall, y el techo del veredicto

Dos recortes deliberados, los dos porque **un falso "esto no existe" es una
acusación, y una revisión de menos es solo un hueco**:

- **Se exige fence de Python explícito** (```` ```python ````). Medido contra
  `psf/requests`: su bloque ```` ```shell ```` con
  `git clone https://github.com/psf/requests.git` salía reportado como *"el
  ejemplo cita 'git' pero no existe"*. Un fence sin etiqueta puede ser shell,
  texto o salida de consola, así que tampoco se revisa.
- **Techo `APROBADO_CON_OBSERVACIONES`.** El chequeo de coverage sí llega a
  `NO_SOSTENIBLE` porque *"no hay una sola función de test"* no admite lectura
  alternativa. Un identificador ausente sí la admite: API generada
  dinámicamente, API planeada, o un símbolo que viene de una extensión C.
  Promover el techo requeriría medirlo sobre muchos más repos.

Con el índice truncado por el tope de archivos, "no está en la tabla" pasa a
significar "no lo miramos": se emite una nota `(sin verificar)` en vez de
hallazgos. Mismo criterio que `_no_corrio()` en el agente de triage.

## Medición sobre repos reales

```
repo              seg  archivos  paquetes  citas  faltan
pallets/click    0.14        78         4      4       0
psf/black        0.56        75         8      0       0
psf/requests     0.06        37         4      1       0
auditkit-cli     0.08        30         2      0       0
```

Las 5 citas resuelven a ubicaciones verificables abriendo el archivo:

| cita | resuelve a |
|---|---|
| `click.command` | `src/click/decorators.py:138` |
| `click.option` | `src/click/decorators.py:352` |
| `click.echo` | `src/click/utils.py:252` |
| `requests.get` | `src/requests/api.py:74` |

**Límite honesto:** 5 citas en 4 repos es poco. `black` y `auditkit-cli` dan
cero porque son CLIs y sus READMEs no tienen ejemplos de uso en Python. El
chequeo es estrecho por diseño. Lo que sí garantiza es que cuando dice algo,
lo dice con `archivo:línea` y sin modelo de por medio.

## Dos bugs que encontró el propio proceso

**Un arreglo que se pasó de largo.** El primer filtro de URLs miraba el token
entero sin espacios. En `requests.get('https://httpbin.org/x')` ese token
incluye la URL del **argumento**, así que `psf/requests` pasó de 1 falso
positivo a **cero citas revisadas**. Ahora se recorre solo la cadena punteada
que empieza en el match y se mira su terminador. Hay test de regresión para
las dos direcciones.

**`rglob("*.py")` también devuelve directorios.** Un repo con un directorio
llamado `pkg.py` hacía reventar el verificador con `PermissionError` en
Windows (`IsADirectoryError` en POSIX) — y `readme_check` corre **por
defecto** sobre repos ajenos. Lo encontró un test escrito para matar un
mutante, no una revisión de código.

## Mutation testing

| Módulo | Mutantes | Muertos | Sobrevivientes | Anotaciones `X \| None` |
|---|---|---|---|---|
| `symbol_index.py` | 104 | 87 | 17 | 11 |
| `readme_check.py` | 191 | 165 | 26 | 13 |

Primera corrida: 75/100 y 144/179. Los 36 sobrevivientes que no eran
anotaciones se aplicaron uno por uno; la mayoría eran huecos de test:

- **Cinco `continue` → `break` sobrevivían por la misma razón:** en todos los
  fixtures el elemento a saltear quedaba último, así que `break` y `continue`
  coincidían. Los tests nuevos ponen algo relevante *después* del salteo.
- **`>=` → `is` en el tope de archivos.** Sobrevivía porque CPython cachea los
  enteros −5..256 y el test usaba tope 3, o sea que `is` funcionaba por
  accidente. Con tope 257 compara identidad de objetos distintos, da siempre
  `False` y **el tope no corta nunca** — justo en los repos grandes, que son
  su única razón de ser.
- **`limite += 1` → `+= 2`.** Con un solo dominio de prueba la paridad hacía
  que el escaneo cayera igual sobre la barra. Con otra longitud la saltea y
  aparece un falso positivo. El test usa dos dominios de largo distinto: la
  propiedad real es que el recorrido mire *todos* los caracteres.
- `!=` → `>` sobre `py_file.stem` descartaba los módulos que ordenan antes que
  `__init__` (las mayúsculas van antes que el guion bajo en ASCII); `!=` →
  `is not` metía `__init__` como submódulo citable.

Los 19 sobrevivientes finales que no son anotaciones se verificaron
equivalentes aplicando el mutante real: el valor de `_MAX_ARCHIVOS`
(requeriría un repo de ~2000 archivos), `== 1` → `<= 1` bajo una guarda que
ya garantiza `len >= 1`, `partes[0]` → `partes[-1]` dentro de la rama donde
la lista tiene un solo elemento, `>=` → `==` sobre un contador que crece de a
uno, y `break` → `continue` después de truncar, donde la lista ya no crece.

### Advertencia operativa: cosmic-ray subcuenta muertes en Windows

`cosmic_ray/testing.py:77` hace `stdout.decode("utf-8")` en la rama de
`KILLED`. Las notas de este proyecto llevan em-dash, pytest las emite en
cp1252 al fallar, el decode explota y el mutante **muerto** se registra como
`INCOMPETENT`. Medido sobre `semantic_check.py`: 16 muertos / 77 incompetentes
sin la variable, **92 / 1** con `PYTHONIOENCODING=utf-8`, y los 47
sobrevivientes idénticos entre las dos corridas. El bug subcuenta muertes;
nunca convierte un sobreviviente en muerto.

## Verificación

```
230 passed, 2 skipped, 1 xfailed
All checks passed!
```

Los 2 skips son preexistentes y declarados: symlinks en Windows (WinError
1314) y la sonda de cuota de Groq.
