# ADR 0002: Verificador semántico (semantic_check.py)

## Contexto

`readme_check.py` solo contrasta un tipo de afirmación del README contra el
código: `N% coverage` vía regex, contra funciones `test_*` reales vía `ast`
(ADR 0001, "Qué NO se reporta..."). Cualquier otra afirmación en prosa libre
("production-ready", "sin vulnerabilidades conocidas", "soporta 10k
requests/segundo") queda fuera de alcance — un regex no entiende prosa
libre. `semantic_check.py` cubre ese hueco usando un LLM (Groq, cliente ya
construido en `auditor/core/semantic_client.py`) **solo para extraer** esas
afirmaciones como texto estructurado — nunca para juzgarlas.

Verificador nuevo, quinto, **opt-in**: no forma parte del pipeline por
default ni con `--run-tests`. Requiere `--semantic` explícito.

## Qué hace

1. Si hay README, se lo manda al modelo con un prompt que pide *solo*
   extracción: una lista de `{afirmacion, cita_textual_del_readme}` en JSON,
   `temperature=0`. El prompt es explícito en que el modelo no decide si
   son ciertas — ese trabajo es de código determinístico, no del LLM.
2. Para cada afirmación extraída, cruza sus palabras clave contra la
   evidencia que ya produjeron los otros 4 `VerifierResult` de esa misma
   corrida (`secrets`, `readme_check`, `build_check`, `deps_check`). Si
   encuentra evidencia relacionada — y toda evidencia en este sistema
   representa un problema, nunca una confirmación positiva — la trata como
   contradicción de la afirmación.
3. Devuelve `APROBADO_CON_OBSERVACIONES` con la cita del README + la
   evidencia contradictoria si encontró algo; `APROBADO` si no hay
   afirmaciones o ninguna choca con evidencia existente. **Nunca
   `NO_SOSTENIBLE` por esto solo** (ver "Reglas de seguridad" abajo).

## Por qué no decide solo

Dos sentidos distintos, los dos importan:

**(a) El modelo no juzga verdad.** El prompt pide únicamente extracción +
cita textual — nunca "¿es cierto que...?". Verificar si una afirmación es
sostenible es exactamente el trabajo que este proyecto entero existe para
hacer con evidencia verificable (CLAUDE.md, "Reglas no negociables"); pedirle
a un LLM que lo decida por su cuenta sería tercerizar el propio producto,
el mismo argumento que en el ADR 0001 descartó usar una librería de
terceros para `readme_check.py`. El veredicto sale de código determinístico
(cruce de keywords), no de la opinión del modelo.

**(b) No es un verificador par de los otros cuatro.** Los cuatro existentes
cumplen `verify(ctx: RepoContext) -> VerifierResult` — independientes entre
sí, sin ver el resultado de nadie más (ADR 0001, "cada uno testeable de
forma aislada"). `semantic_check.py` necesita ver la evidencia que **ya**
produjeron los otros cuatro para poder cruzarla, así que no puede correr en
paralelo ni con la misma firma. Su función es:

```python
def verify(ctx: RepoContext, other_results: dict[str, VerifierResult]) -> VerifierResult:
```

`orchestrator.py`/`cli.py` lo corren como un paso aparte, después de los
cuatro base, y funden su resultado en el mismo `AuditReport` (mismo
`worst_verdict` para el veredicto final). No es una ampliación del tipo
`Verifier` existente — es deliberadamente distinto, para no forzar a los
otros cuatro a cargar una dependencia que no necesitan.

## Reglas de seguridad

- **Opt-in real, no default con excepción.** Sin `--semantic`, el código ni
  siquiera importa/instancia el cliente de Groq — no hay intento de red,
  no hay chequeo de API key. Mismo criterio que `--run-tests` para
  `build_check.py` (ADR 0001): una capacidad que cuesta plata/tiempo/riesgo
  no se activa sin pedirla.
- **Nunca crashea el pipeline entero.** Tres fallos posibles, los tres se
  capturan y se traducen a un `VerifierResult` con nota explícita, nunca a
  una excepción que suba:
  - `MissingApiKeyError` (ya la lanza `semantic_client.py`) → "verificador
    semántico saltado: falta GROQ_API_KEY".
  - Timeout o error de red/API (`groq.APIError` y subclases, con
    `timeout=` explícito en la llamada — mismo espíritu que el timeout de
    `pip-audit` en `deps_check.py`, ADR 0001) → observación, no cuelgue.
  - JSON inválido o que no cumple el schema esperado → observación, **sin
    reintentar**. Un modelo que no devuelve JSON válido una vez no es más
    confiable la segunda vez con el mismo prompt determinístico
    (`temperature=0`); reintentar solo gasta cuota de API sin cambiar el
    resultado esperado.
- **Nunca decide `NO_SOSTENIBLE` por sí solo.** El cruce de evidencia es una
  heurística de palabras clave simple (ver limitaciones) — no una prueba.
  Un falso positivo semántico no debería poder tumbar un repo sano al nivel
  más severo; el peor que puede hacer es marcar una observación para que un
  humano la revise. Esto es una decisión de diseño distinta a
  `readme_check.py`, que sí llega a `NO_SOSTENIBLE` — pero `readme_check.py`
  usa `ast`, hechos verificables sin ambigüedad; acá la "evidencia" de que
  una afirmación es falsa es en sí misma un cruce probabilístico.
- **La API key no se expone.** No se toca `semantic_client.py` — sigue sin
  exportar la clave al entorno del proceso, sigue sin loggearla. El nuevo
  código nunca imprime la clave ni la pasa a ningún `Evidence`/nota.

## Cruce de afirmación contra evidencia existente

Palabras clave simples, sin NLP: tokens de 4+ caracteres de la afirmación
extraída, comparados por intersección de conjunto contra los tokens de cada
`note` de evidencia ya producida por los otros verificadores. Cualquier
intersección no vacía cuenta como "hay evidencia relacionada" — no hace
falta nada más sofisticado para este MVP.

**Bug real encontrado auditando psf/black:** el nombre del proyecto
("black") aparece en casi cualquier afirmación extraída sobre sí mismo
("_Black_ is...", "_Black_ has..."), y coincidía con evidencia de un módulo
interno sin relación (`_black_version` sin declarar) — la misma evidencia le
pegaba a **todas** las afirmaciones, sin importar el tema (hasta a "licencia
MIT"). Dos causas, dos fixes:

1. **Vocabulario genérico de nuestras propias notas.** Palabras como
   "declarado", "versión", "código", "requirements" aparecen en casi
   cualquier nota de evidencia sin aportar señal temática. Sumadas al
   stopword list existente.
2. **El nombre del propio proyecto no se excluía.** `declared_project_names()`
   (movida a `repo_context.py`, compartida con `deps_check.py`) se tokeniza
   igual que cualquier otro texto y se resta de ambos lados del cruce — de
   la afirmación y de la evidencia. Sin esto, un proyecto llamado "black" (o
   "django", "requests"...) hace que su propio nombre actúe como un
   comodín que matchea cualquier cosa consigo mismo.

Ninguno de los dos fixes agrega falsos negativos donde antes había una
detección real: un match genuino (ej. "test coverage" en la afirmación
contra "no hay funciones de test" en la evidencia) sigue funcionando — el
fix solo saca del cruce las palabras que nunca aportaron señal real.

## Librería externa

Ninguna nueva — reusa `auditor/core/semantic_client.py` y el SDK `groq`
(ya declarados en `pyproject.toml` desde el PR anterior). Modelo:
`llama-3.3-70b-versatile`, confirmado contra la API real que soporta
`response_format={"type": "json_object"}` con el prompt de extracción antes
de escribir el verificador (no se asume, se probó).

## Limitaciones conocidas

- **El cruce de keywords es cross-language ciego.** Las notas de evidencia
  de los otros cuatro verificadores están en español; un README en inglés
  ("100% test coverage") puede no compartir ningún token con una nota como
  "README afirma... pero no hay funciones de test". Es una heurística
  deliberadamente simple (así se pidió), no una traducción ni un embedding
  semántico — falsos negativos (afirmación contradicha que no se detecta)
  son esperables y no se resuelven en este MVP.
- **La cita textual puede no ser substring exacto del README.** El modelo
  puede parafrasear levemente pese al prompt. Si `cita_textual_del_readme`
  no aparece literal en el archivo, la evidencia cae a línea 1 en vez de la
  línea real — mismo patrón de fallback que el resto del proyecto (ADR
  0001, `_locate`/`_deps_file_fallback` en `deps_check.py`).
- **Requiere red y una API key real** (Groq) — a diferencia de los otros
  cuatro verificadores, que corren offline salvo `deps_check.py` (que ya
  necesita red para PyPI/OSV). Con `--semantic` sin `GROQ_API_KEY`, el
  verificador se salta con observación explícita, nunca falla en silencio
  ni bloquea el resto del pipeline.
- **El catálogo de modelos de Groq cambia con el tiempo — y cambió.** Esta
  deuda se anotó como hipotética ("si Groq lo deprecara...") y se volvió
  real: `llama-3.3-70b-versatile` desapareció por completo del catálogo
  (`client.models.list()` ya no lo lista; toda la familia Llama se fue de
  esta cuenta). El síntoma fue exactamente el anticipado — `groq.APIError`
  (un `NotFoundError`, subclase de `APIError`) capturado y reportado como
  observación, nunca un crash — pero eso significa que `--semantic` y
  `--triage` degradaban en silencio a "la API no respondió" en cada
  corrida real, sin que nada lo hiciera evidente salvo correr contra la API
  de verdad.

  Reemplazado por `qwen/qwen3.6-27b`, elegido midiendo y no a ojo: sobre
  los 5 escenarios reales de `triage_agent` (ver ADR 0003), da 5/5 en dos
  corridas — mejor que el mejor resultado medido con el modelo anterior
  (3/4, con un caso que quedó `xfail` permanente). Se probó primero
  `openai/gpt-oss-120b`: funciona bien para JSON mode (este módulo) pero
  falla 3 de 5 casos de triage porque intenta responder el segundo turno
  con una llamada a una herramienta inventada (`"JSON"`) en vez de texto
  plano, y Groq rechaza esa llamada con 400 antes de que el código la vea.
  `qwen/qwen3.6-27b` no tiene ese problema y sirve para los dos usos, así
  que se usa uno solo — mismo diseño original.

  Sigue sin haber mecanismo de fallback automático: el nombre del modelo
  sigue hardcodeado, y la próxima deprecación se va a notar de la misma
  forma — degradación silenciosa, detectable solo corriendo los tests
  reales. Deuda conocida, no resuelta, ahora con un caso real documentado.

## Verificación

- `pytest` corre y pasa: casos mockeados (afirmación con evidencia
  contradictoria, afirmación sin evidencia relacionada, JSON malformado del
  modelo, sin `GROQ_API_KEY`) + un test contra la API real de Groq
  (mismo criterio que el test de `pip-audit` real en `deps_check.py`).
- Mutation testing sobre `semantic_check.py` (`cosmic-ray` — `mutmut` no
  soporta Windows nativo, `mutatest` rompe con `random.sample` en Python
  3.14). 123 mutantes, 36 sobrevivientes en la primera vuelta con
  fixtures mas fuertes (evidencia no relacionada con conjunto no vacío,
  claims individuales malformados dentro de una lista válida, múltiples
  afirmaciones, `_locate_quote` probado directo). Los 36 sobrevivientes
  finales, los tres explicados, ninguno es un gap real:
  - 33 en anotaciones de retorno `X | None` (líneas 37/58/108) — Python
    3.14 difiere la evaluación de anotaciones (PEP 649, confirmado
    corriendo `def f(x) -> 1/0: ...` sin que explote hasta acceder a
    `__annotations__`); mutarlas no cambia ningún comportamiento en
    tiempo de ejecución.
  - 1 en `choices[0]` → `choices[-1]` (línea 78) — equivalente para una
    respuesta de un solo `choice`, que es lo único que pide este cliente.
  - 2 en `index == -1` → `index <= -1` / `index is -1` (línea 101) —
    `str.find()` nunca devuelve menos de -1 (equivalente matemático) y
    CPython cachea enteros chicos (`is -1` se comporta igual que `== -1`
    para ese valor).
  Descontando esos 36, **100% de los mutantes no equivalentes murieron**
  (87/87). Dos hallazgos reales que esto encontró y arregló: una
  aserción tautológica propia (comparaba `kwargs["timeout"]` contra
  `semantic_check._TIMEOUT_SECONDS`, el mismo atributo que mutaba) y un
  fixture de `_locate_quote` que no distinguía `count(..., 0, ...)` de
  `count(..., 1, ...)` por casualidad del texto de prueba.
- `ruff check .` limpio.
- Commit en rama aparte, PR en draft — no se mergea sin revisión.
