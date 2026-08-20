# ADR 0006: interfaz de terminal — presentación, no lógica

> Numerado 0006 y no 0005 porque el 0005 ya es `--ask`. La primera versión de
> este documento se llamó `0005-interfaz-terminal.md` y colisionaba.

## Contexto

Una auditoría completa no es instantánea. Medido sobre repos reales en las
corridas del Bloque B:

| etapa | costo observado |
|---|---|
| `git clone --depth 1` | 2–15 s según el repo |
| `deps_check` (pip-audit, red) | 5–30 s |
| `build_check` (`--run-tests`) | segundos a minutos, sin techo conocido |
| `semantic_check` (Groq) | 3–10 s |
| triage (Groq, hasta 10 hallazgos × 3 iteraciones) | hasta ~1 min |

Durante todo eso la salida es **nada**. El proceso parece colgado, y el
usuario no tiene forma de saber si está esperando a `pip-audit`, a la red, o a
un `pytest` ajeno que va a tardar tres minutos. Después aparece el reporte
entero de golpe.

Hay además un problema de legibilidad ya anotado en `CLAUDE.md`: `build_check`
mete `output[-2000:]` —la salida cruda de pytest, multilínea— dentro del campo
`note` de una `Evidence`, y `semantic_check` la vuelve a incrustar dentro de
las suyas. En el reporte de `pytransitions/transitions` eso produjo una sola
"evidencia" de 30 líneas con un traceback adentro.

## Decisión

Una capa de presentación nueva, `auditor/cli_display.py`, que:

1. Muestra progreso en vivo por verificador mientras corre, con ✅ / 🔄 / ❌
   apenas cada uno termina.
2. Renderiza el reporte final con color y estructura cuando la salida va a una
   terminal.
3. **Degrada al texto plano de hoy, byte por byte, cuando no.**

Y que **no toca la lógica de ningún verificador, ni el orquestador, ni el
veredicto**. Si se borra el archivo entero, el auditor sigue funcionando y
produciendo exactamente la misma salida que produce hoy en un pipe.

### `rich`, no `textual`

`textual` es un framework de aplicaciones TUI: toma la pantalla completa,
entra en el buffer alternativo del terminal y corre su propio event loop. Eso
convierte a `auditor` en una aplicación interactiva, y `auditor` es un comando
de una sola pasada cuya salida se redirige, se pipea y se pega en un issue.

`rich` es una librería de *renderizado*: escribe a un stream, detecta si ese
stream es una terminal, y si no lo es emite texto sin escapes. Es la
herramienta que corresponde a "salida linda cuando se puede, texto plano
cuando no", que es exactamente el requisito.

### Cómo se engancha sin tocar nada

El orquestador corre los verificadores con un dict comprehension:

```python
results = {name: verify(ctx) for name, verify in verifiers.items()}
```

La tentación es agregarle callbacks (`on_start=`, `on_result=`). No hace
falta, y evitarlo es el punto: **el progreso se consigue decorando las
funciones antes de pasárselas**, en `cli.py`.

```python
verifiers = {
    nombre: display.con_progreso(nombre, verify)
    for nombre, verify in verifiers.items()
}
```

`orchestrator.py` no cambia una línea. Ningún verificador cambia una línea.
La decoración es composición pura sobre el tipo `Verifier` que ya existe
(`Callable[[RepoContext], VerifierResult]`), así que tampoco cambia el tipo.

Los dos verificadores que no pasan por el orquestador —el triage y
`semantic_check`, que corren aparte porque necesitan ver los resultados de los
demás (ADR 0002, ADR 0003)— se anuncian a mano con el mismo objeto, con dos
llamadas explícitas en `cli.py`.

### Qué stream recibe qué

| contenido | stream | condición |
|---|---|---|
| progreso en vivo | `stderr` | solo si `stderr.isatty()` |
| reporte final | `stdout` | siempre |
| color en el reporte | `stdout` | solo si `stdout.isatty()` y no `--json` |

El progreso va a **stderr** y no a stdout por una razón concreta: así
`python -m auditor <url> > salida.txt` deja el archivo con el reporte y nada
más, mientras el spinner sigue animándose en la terminal. Mezclarlo en stdout
convertiría el archivo en un log con escapes adentro.

### Las tres degradaciones, explícitas

1. **`--json`**: no se decora nada, no se imprime progreso, y el JSON sale por
   `print()` como hoy. `--json` es una interfaz de máquina; un solo escape
   ANSI adentro la rompe.
2. **stdout redirigido a archivo o pipe**: el reporte sale como el markdown de
   hoy, **idéntico**. No "parecido": la función `to_markdown` no se toca y se
   sigue usando tal cual. Esto no es solo prolijidad — es lo que garantiza que
   los tests que ya existen sobre `to_markdown` sigan siendo válidos.
3. **`NO_COLOR`**: `rich` respeta la variable de entorno por su cuenta. No hay
   que implementar nada, pero sí hay que no romperlo: se usa `Console()` y no
   se fuerza `force_terminal=True`.

## Qué NO hace esta capa

- **No cambia ningún veredicto.** No hay ninguna ruta desde `cli_display.py`
  hacia `worst_verdict` ni hacia un `VerifierResult`.
- **No oculta evidencia.** Puede reformatearla —y ese es el punto para el
  `output[-2000:]` de `build_check`— pero no puede decidir no mostrar un
  hallazgo. Un reporte más lindo que muestra menos es peor que uno feo.
- **No toca `report.py`.** `to_markdown` y `to_json` quedan como están y
  siguen siendo la única salida en modo no interactivo.
- **No agrega estado.** No hay archivo de configuración, no hay tema, no hay
  flag `--no-color` propio: `NO_COLOR` es el estándar y ya existe.

## La salida de pytest dentro de una nota

Con esta capa, `build_check` puede seguir metiendo el traceback entero en la
nota —no se toca su lógica— y la presentación decide mostrarlo recortado, con
el detalle completo disponible en `--json`. Eso cierra el punto anotado en
`CLAUDE.md` **sin tocar el verificador**, que era la parte difícil.

Si más adelante se decide que la nota no debería llevar el traceback entero,
eso es un cambio en `build_check` y va en su propio ADR. Acá solo se deja de
castigar al lector por un problema que no es suyo.

## Dependencia

`rich` entra como dependencia principal en `pyproject.toml`, no como extra.

Esto contradice al ADR 0001, que decía *"no motor de reportes (markdown armado
a mano desde los `VerifierResult`)"*. La contradicción es aparente: el markdown
se sigue armando a mano y `report.py` no cambia. `rich` no genera el reporte —
lo pinta cuando hay una terminal que lo justifique.

Va como dependencia principal y no como extra opcional (a diferencia de
`sentence-transformers` en el ADR 0005) porque el criterio es distinto:
`sentence-transformers` arrastra torch, ~2 GB, para una funcionalidad opt-in.
`rich` es Python puro, sin dependencias compiladas, y la interfaz es la
experiencia **por defecto**. Un extra que hay que instalar para que el comando
se vea bien es un extra que nadie instala.

## Verificación

Además de los tests unitarios, tres comprobaciones que se hacen y se pegan:

1. `python -m auditor <url> > salida.txt` — y `cat salida.txt` no tiene ni un
   escape ANSI. Se verifica con una búsqueda explícita de `\x1b[`, no a ojo.
2. `python -m auditor <url> --json | python -m json.tool` — parsea.
3. La corrida real en una terminal interactiva, con el spinner y los ✅/🔄/❌
   apareciendo de a uno.

El punto 1 es el que más fácil se rompe y el que menos se nota: un escape ANSI
en un archivo no se ve cuando lo mirás con `cat` en la misma terminal que lo
generó.

## Limitación conocida

Los emoji de veredicto (✅ 🔄 ❌ ⏸️) ya se usan en el markdown de hoy y ya
dependen de que la consola los soporte. `cli.py` reconfigura stdout/stderr a
UTF-8 al arrancar (`_use_utf8_streams`) justamente por eso. Esta capa no
mejora ni empeora esa situación: hereda el mismo supuesto.
