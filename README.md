> ⚠️ **Advertencia de seguridad:** este auditor clona y ejecuta código de
> repositorios de terceros que **no son confiables por default**. Con
> `--run-tests` (o al confirmar el prompt interactivo), corre `pytest` real
> del repo auditado **sin sandbox** — cualquier código en sus tests o en
> `conftest.py` se ejecuta con los privilegios del proceso que corre
> `auditor`. No lo corras contra repos que no estés dispuesto a ejecutar
> directamente en tu máquina. Ver la sección [Seguridad](#seguridad) abajo.

# auditkit-cli

Auditor automático de credibilidad de repos: recibe una URL de GitHub y
verifica si el README dice la verdad, contrastándolo contra el código, los
tests y las dependencias reales — no contra lo que el README afirma.

## Uso

```
python -m auditor <url-del-repo>                # solo verificadores pasivos
python -m auditor <url-del-repo> --run-tests     # incluye build_check (corre pytest real)
python -m auditor <url-del-repo> --json          # salida en JSON
```

Por default corren `secrets`, `readme_check` y `deps_check` — no ejecutan
código del repo auditado. `build_check` corre `pytest` real y por eso
requiere `--run-tests` explícito, o confirmación interactiva si corrés en
una terminal.

## Verificadores

- **secrets** — busca secretos reales en el código (`detect-secrets`).
- **readme_check** — contrasta afirmaciones del README contra el código
  real (ej. "100% test coverage" sin tests de verdad).
- **build_check** — corre el comando de test real y captura el resultado.
  Solo con `--run-tests`.
- **deps_check** — vulnerabilidades reales (`pip-audit`) y dependencias
  usadas-sin-declarar / declaradas-sin-usar.

Cada uno devuelve APROBADO / APROBADO_CON_OBSERVACIONES / NO_SOSTENIBLE con
evidencia archivo:línea. Detalle de diseño y limitaciones conocidas en
[docs/adr/0001-arquitectura.md](docs/adr/0001-arquitectura.md).

## Seguridad

Dos limitaciones de seguridad aceptadas para este MVP (encontradas por una
revisión de seguridad, documentadas en detalle en el ADR):

- **RCE inherente en `build_check.py`.** Corre `pytest` del repo clonado
  sin sandbox — `conftest.py` y cualquier `test_*.py` se ejecutan con los
  privilegios del proceso auditor. No es un bug parcheable, es inherente a
  "correr el comando de test real" sobre código no confiable. Por eso
  `build_check` nunca corre por default (ver [Uso](#uso)).
- **El downgrade de dependencias declaradas es gameable.** Cuando
  `build_check` encuentra un `ModuleNotFoundError` de un paquete declarado
  en `requirements.txt`/`pyproject.toml`, baja el veredicto a
  `APROBADO_CON_OBSERVACIONES` en vez de `NO_SOSTENIBLE`. El repo auditado
  controla ese archivo por completo, así que puede declarar cualquier
  nombre plausible para esquivar un `NO_SOSTENIBLE` real. El MVP asume
  buena fe en lo declarado, no verifica que el paquete exista de verdad
  antes de aceptar el downgrade.
