# ADR 0005: `--ask` — recuperación sin veredicto

## Contexto

El ADR 0003 midió un intento de usar embeddings dentro de la auditoría y lo
descartó con números: 18 hallazgos nuevos sobre `click`, `black` y `requests`,
**los 18 falsos**. La causa no era el umbral sino el diseño —

> La similitud coseno encuentra la nota del **mismo tema**; el diseño la
> trataba como la que **refuta** la afirmación. Son dos relaciones distintas
> y solo coinciden por casualidad.

y la regla que quedó escrita:

> La recuperación **localiza**, no **juzga**. Cada vez que se use recuperación
> en este proyecto, el veredicto tiene que salir de código determinista o no
> salir.

El "Paso 2" de aquel ADR —RAG sobre el código, usado *dentro* de la auditoría
para señalar código relacionado con afirmaciones sin evidencia— quedó
**descartado**, porque heredaba el mismo defecto con un corpus 100x más
grande: buscar "el código más parecido a esta afirmación" devuelve la función
que habla del mismo tema, que es justamente lo que uno esperaría encontrar si
la afirmación **es cierta**.

## Decisión

`--ask` es la forma de recuperación que **sí** sobrevive a esa medición, y
sobrevive precisamente porque no afirma nada.

```
python -m auditor <url> --ask "¿dónde maneja reintentos de conexión?"
```

Devuelve los fragmentos de código más relacionados con la pregunta, cada uno
con su `archivo:línea`. **No emite veredicto, no aparece en el reporte de
auditoría, y no participa de ningún `worst_verdict`.**

La diferencia con el Paso 2 descartado no es de implementación sino de quién
saca la conclusión. El Paso 2 iba a convertir "esto es lo más parecido" en
"esto contradice la afirmación" — un salto que la medición demostró
injustificado. `--ask` entrega la lista y ahí termina: **quien pregunta es
quien juzga**, y sabe que preguntó.

Es la misma línea que el agente de triage, que también "agrega señal, no
veredicto" — pero llevada al extremo, porque acá ni siquiera hay una señal
que combinar con otras.

## Por qué es un comando y no un verificador

Un verificador devuelve `VerifierResult` con un `Verdict`, y el orchestrator
lo combina con `worst_verdict`. `--ask` no tiene nada que aportar a esa
combinación: no sabe si algo está bien o mal. Meterlo en el reporte obligaría
a inventarle un veredicto —`APROBADO` sería mentira, `APROBADO_CON_OBSERVACIONES`
convertiría toda pregunta en una observación— así que no entra.

Con `--ask`, los verificadores **no corren**. La pregunta es exploratoria y
pagar `pip-audit` y el scan de secretos para responderla sería gasto puro.

## `cruzar()` se elimina

`embedding_index.py` traía de la rama anterior la función `cruzar()`, que
respondía *"¿cuál es la nota que contradice esta afirmación?"* gobernada por
`_UMBRAL = 0.30`. No la llamaba nadie: es literalmente "el código del cruce
semántico por embeddings, el que juzgaba si una afirmación era falsa".

Se borra, con sus ~15 tests. Dos razones, y la segunda es la que decide:

1. Es código muerto, y dejarlo invita a re-cablearlo.
2. Su umbral estaba calibrado para `all-MiniLM-L6-v2`. Con el modelo nuevo
   ese `0.30` no significa nada, y una constante que parece calibrada pero ya
   no lo está es peor que ninguna.

Lo que se conserva de ese módulo es lo que nunca estuvo en discusión: la
carga perezosa, la normalización, el error tipado y —ahora— `rankear()`.

## Sin umbral

El único parámetro tuneable del diseño que fracasó era `_UMBRAL = 0.30`, y la
medición mostró que no existía punto de operación: al umbral donde morían los
falsos positivos ya no quedaba ningún hallazgo.

Acá el problema es **ordenar**, no **decidir**, y ordenar no requiere punto de
corte. Se listan los `_TOP_N` primeros por similitud y el lector ve el orden.
Un fragmento poco relacionado se nota leyéndolo; no hace falta un número que
decida por él. Eso elimina de raíz la clase de error del ADR 0003.

Se muestra la similitud de cada fragmento **como número crudo**, sin
convertirla a porcentaje ni a etiqueta de confianza: es una distancia coseno,
no una probabilidad de que la respuesta sea correcta.

## El modelo se eligió midiendo, y la primera elección estaba mal

El modelo heredado del ADR 0003 era `all-MiniLM-L6-v2`, que es **solo
inglés**. La primera validación real de `--ask` contra `psf/requests`, con la
pregunta *"¿dónde maneja reintentos de conexión?"*, devolvió esto:

```
  src/requests/cookies.py:564   (similitud 0.215)   def cookiejar_from_dict(
  src/requests/models.py:284    (similitud 0.203)   class Request(...)
  src/requests/sessions.py:511  (similitud 0.199)   def prepare_request(...)
```

Cookies para una pregunta sobre reintentos. Los reintentos viven en
`src/requests/adapters.py` (`HTTPAdapter`, `max_retries`) y no aparecían.
Ese es exactamente el fallo silencioso que este proyecto existe para no dejar
pasar — y venía de la misma ceguera cross-language que el ADR 0002 ya tenía
documentada para el cruce de keywords.

**La primera explicación también estaba mal.** Con una sola pregunta parecía
"el modelo anda en inglés y falla en español". Una batería de 5 preguntas con
verdad de campo conocida sobre `psf/requests` —reintentos, autenticación
básica, cookies, redirecciones, subida multipart— contando si el archivo
correcto entra en el top-5, dice otra cosa:

| modelo | inglés | español | costo total |
|---|---|---|---|
| `all-MiniLM-L6-v2` | 4/5 | 4/5 | 17.6s |
| `paraphrase-multilingual-MiniLM-L12-v2` | **5/5** | **5/5** | 21.4s |

El modelo inglés no falla "solo en español": falla uno en cada idioma. El
multilingüe es **estrictamente mejor** por 3.8s más. Se cambia el default.

Con el modelo nuevo, la misma pregunta en español devuelve lo correcto en
primer lugar:

```
  src/requests/adapters.py:158  (similitud 0.324)   class HTTPAdapter(BaseAdapter):
```

**Limitación medida que queda abierta:** los archivos de test del repo
auditado compiten por los lugares del top-5 (en ese resultado, 4 de 5 son de
`tests/`). No impidieron que el archivo correcto entrara —la batería da
5/5— pero ocupan lugar. Excluir `tests/` mejoraría la señal para preguntas
del tipo "¿dónde está implementado X" y la empeoraría para "¿dónde se prueba
X"; queda sin decidir hasta tener una medición que lo justifique.

## Costo, y carga perezosa

Medido con el modelo ya cacheado:

| Concepto | Tiempo |
|---|---|
| `import sentence_transformers` | 4.7s |
| cargar `paraphrase-multilingual-MiniLM-L12-v2` | 4.4s |
| `psf/requests` — 807 fragmentos | +12.3s |
| **total sobre `psf/requests`** | **21.4s** |

Es caro, y por eso es **opt-in**: sin `--ask` no se importa nada. Dentro de
`--ask` el modelo tampoco se toca si el repo no tiene un solo fragmento
indexable. Mismo criterio con el que el agente de triage no gastó una llamada
de API en `pallets/click`.

**Tope de fragmentos** (`_MAX_FRAGMENTOS`): un monorepo no puede colgar la
corrida. Si se corta, se dice explícitamente cuántos fragmentos se indexaron
de cuántos archivos — porque con el corpus truncado "no aparece en los
resultados" deja de significar "no está en el repo".

## Fragmentos: función/clase, métodos incluidos

A diferencia de `symbol_index.py` (ADR 0004), que indexa **API pública** y por
eso mira solo definiciones de nivel superior, acá se indexan también los
métodos: la respuesta a "¿dónde maneja reintentos?" suele vivir en un método
(`HTTPAdapter.send`), no en una función de módulo. Son dos preguntas
distintas sobre el mismo árbol, y por eso son dos módulos y no uno.

Se comparte `is_test_fixture_path`: los `.py` de `tests/data/` son input de
prueba, no código del repo, y contestarían preguntas con archivos que no son
del proyecto.

## Degradación

`ModeloNoDisponibleError` (librería ausente o descarga fallida — ya pasó una
vez con `SSL: CERTIFICATE_VERIFY_FAILED`) se reporta con un mensaje explícito
y código de salida distinto de cero. No hay "degradación silenciosa" posible
acá: sin modelo no hay respuesta, y fingir una lista vacía sería peor que
fallar.

## Dependencias declaradas

`sentence-transformers` y `numpy` se importaban sin estar declaradas en
`pyproject.toml`. Además de ser el bug que este proyecto existe para
encontrar —**el auditor fallaba su propia auditoría**: `deps_check` marcaría
`NO_SOSTENIBLE` contra este repo— era insostenible con `--ask` como feature
real y no experimento.

Van como extra opcional (`pip install auditkit-cli[rag]`) y no como
dependencia principal: `sentence-transformers` arrastra torch (~2GB) para un
camino opt-in, y el manejo de `ModeloNoDisponibleError` ya existe y es
exactamente el complemento correcto de un extra opcional.

`faiss-cpu` **no** se declara: no se importa en ningún archivo. El propio
ADR 0003 lo midió en 2ms de construcción y 0.1ms por query contra un `matmul`
de numpy que a esta escala es sub-milisegundo. Una dependencia que no compra
nada medible no entra.

## Lo que este comando NO es

Queda escrito acá y en el README, con las mismas palabras:

> **`--ask` te muestra dónde mirar, no te dice si algo es cierto.**

No responde la pregunta: devuelve los lugares del código donde probablemente
esté la respuesta. Si los fragmentos no tienen que ver, eso también es
información — significa que el repo no habla de eso donde el modelo esperaba,
no que la funcionalidad no exista.

Y no se le pregunta al modelo si algo es verdad. No hay LLM en este camino:
solo embeddings, producto punto y un `argsort`.
