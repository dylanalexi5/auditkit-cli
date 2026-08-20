# Contexto del proyecto

Auditor automático de credibilidad de repos: recibe una URL de GitHub y verifica
si el README dice la verdad, contrastándolo contra el código, los tests y las
dependencias reales — no contra lo que el README afirma.

## Reglas no negociables
- Nunca marques una tarea como completa sin correr el comando de test/build real y pegar la salida.
- Nunca inventes que una librería/función existe sin verificarlo en el código.
- Si el build o los tests fallan, repórtalo tal cual — no lo "arregles" cambiando el criterio de éxito.
- Cada verificación debe poder señalar archivo:línea como evidencia, no una afirmación genérica.
- **Antes de commitear un test nuevo, revisá que ninguna aserción compare contra
  la misma constante o atributo del módulo que está probando.** Una aserción como
  `assert mock.call_count == modulo._MAX_INTENTOS` es tautológica: pasa con
  cualquier valor, porque si el valor cambia, cambian los dos lados. Va el
  literal (`== 3`). Esto ya se coló dos veces —`semantic_check.py` y
  `triage_agent.py`— y en ambas la detectó el mutation testing, no la revisión
  del test. Vale para constantes, umbrales, timeouts y cualquier atributo del
  módulo bajo prueba.
- **Ninguna `Evidence` puede llevar una ubicación que ningún test fije contra un
  valor real.** `line=0` es la convención del proyecto para "sin ubicación", y
  es honesta — pero solo si es una decisión, no un default que se coló. Si no
  hay forma confiable de ubicar la línea, la nota tiene que decirlo
  (`ubicacion no determinada`), no caer en `0` ni en `1` en silencio.
  Este patrón —una ubicación fabricada sin test que la fije— ya apareció
  **tres veces en tres módulos distintos**:

  | módulo | qué inventaba | cómo se encontró |
  |---|---|---|
  | `semantic_check._locate_quote` | devolvía `1` cuando no encontraba la cita | revisión de código |
  | `semantic_check` (aviso de recorte) | `line=0` sin test que lo fijara | mutation testing |
  | `build_check` | `line=1` escrito a mano en el literal | mutation testing |

  Es el bug que rompe la promesa central de la herramienta: evidencia que se
  puede verificar, no inventada. Y las dos últimas las encontró el mutation
  testing, no la revisión del test.

  Hay una **cuarta aparición conocida y todavía sin decidir**, anotada abajo
  en "Mejoras futuras": `secrets.py` en notebooks. Es de menor severidad que
  las tres de la tabla y la diferencia importa para no confundirlas — en las
  tres, la ubicación apuntaba a un lugar **equivocado**; en la de notebooks
  apunta al lugar correcto con la **etiqueta equivocada**.

## Mejoras futuras (anotadas, no implementadas)
- **Helper de test compartido para la ubicación de la evidencia.** Que exista
  algo como `assert_ubicacion_real(evidencia, archivo_esperado)` en
  `tests/`, obligatorio para cualquier verificador nuevo, que falle si
  `line` cae en un default sin que la nota lo declare. Hoy la regla de arriba
  se cumple a mano y por eso ya se rompió tres veces. No se implementa
  todavía porque el quinto verificador todavía no existe; cuando aparezca,
  esto va antes que él.
- **`secrets.py` usa el índice de celda como `line` en notebooks.**
  `auditor/verifiers/secrets.py:93` construye
  `Evidence(file=relative, line=cell_index, ...)`: el reporte dice
  `notebook.ipynb:3` y todo consumidor lo lee como *línea 3*, cuando en
  realidad es *celda 3*. La línea real dentro de la celda
  (`secret.line_number`) se descarta.

  **Pendiente de decisión de diseño, no bug listo para arreglar.** No hay una
  opción obviamente correcta: si `line` pasa a ser la línea dentro de la
  celda, apunta a un número que no significa nada en el `.ipynb`, que es un
  JSON. Inclinación registrada para cuando se retome: que la **nota** diga
  `celda N` explícito, en vez de que `line` mienta distinto. Se resuelve
  cuando le toque.
- **`build_check` mete la salida entera de pytest en la nota**
  (`output[-2000:]`), y `semantic_check` la incrusta adentro de las suyas. El
  reporte queda ilegible. Queda para el Bloque C (interfaz), donde se decide
  cómo se presenta la evidencia.

## Comandos
- Tests: `pytest`
- Lint: `ruff check .`
- Ejecutar sobre un repo: `python -m auditor <url-del-repo>`

## Convenciones
- Un módulo por verificador (secretos, readme_check, build_check, deps_check) — cada uno testeable de forma aislada.
- Cada verificador devuelve un veredicto estructurado: APROBADO / APROBADO_CON_OBSERVACIONES / NO_SOSTENIBLE + evidencia.