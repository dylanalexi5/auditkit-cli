# Contexto del proyecto

Auditor automático de credibilidad de repos: recibe una URL de GitHub y verifica
si el README dice la verdad, contrastándolo contra el código, los tests y las
dependencias reales — no contra lo que el README afirma.

## Reglas no negociables
- Nunca marques una tarea como completa sin correr el comando de test/build real y pegar la salida.
- Nunca inventes que una librería/función existe sin verificarlo en el código.
- Si el build o los tests fallan, repórtalo tal cual — no lo "arregles" cambiando el criterio de éxito.
- Cada verificación debe poder señalar archivo:línea como evidencia, no una afirmación genérica.

## Comandos
- Tests: `pytest`
- Lint: `ruff check .`
- Ejecutar sobre un repo: `python -m auditor <url-del-repo>`

## Convenciones
- Un módulo por verificador (secretos, readme_check, build_check, deps_check) — cada uno testeable de forma aislada.
- Cada verificador devuelve un veredicto estructurado: APROBADO / APROBADO_CON_OBSERVACIONES / NO_SOSTENIBLE + evidencia.