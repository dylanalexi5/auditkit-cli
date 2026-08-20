"""Capa de presentación: pinta, no decide (ADR 0006).

Si se borra este archivo entero, el auditor sigue funcionando y produce
exactamente la misma salida que produce hoy en un pipe. Esa es la propiedad
que lo hace seguro, y los tests la fijan: sin terminal, el reporte es
`report.to_markdown` byte por byte.

Tres cosas que esta capa **no puede** hacer, y no tiene forma de hacer:

- cambiar un veredicto — no hay ninguna ruta de acá a `worst_verdict`;
- esconder un hallazgo — puede recortar una nota, y lo declara cuando lo
  hace, pero no puede decidir no mostrar una `Evidence`;
- ensuciar `--json` — con `--json` la capa se desactiva entera.

El progreso se consigue **decorando** los verificadores, no agregándole
callbacks al orquestador: `orchestrator.py` no cambia una línea y ningún
verificador cambia una línea.
"""

import sys
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from auditor.core.models import AuditReport, Evidence, Verdict, VerifierResult
from auditor.report import to_markdown

_COLOR = {
    Verdict.APROBADO: "green",
    Verdict.APROBADO_CON_OBSERVACIONES: "yellow",
    Verdict.NO_SOSTENIBLE: "red",
}
_MARCA = {
    Verdict.APROBADO: "✅",
    Verdict.APROBADO_CON_OBSERVACIONES: "\U0001f504",
    Verdict.NO_SOSTENIBLE: "❌",
}
# Cuántas líneas de una nota se muestran antes de recortar. `build_check` mete
# la salida entera de pytest en el campo `note` — en el reporte de
# `pytransitions/transitions` eso dio una sola "evidencia" de 30 líneas con un
# traceback adentro. La capa lo recorta porque no puede tocar el verificador,
# y dice cuánto dejó afuera: recortar en silencio sería esconder evidencia.
_MAX_LINEAS_NOTA = 3


def _recortar(nota: str) -> str:
    lineas = nota.splitlines()
    if len(lineas) <= _MAX_LINEAS_NOTA:
        return nota
    sobran = len(lineas) - _MAX_LINEAS_NOTA
    plural = "s" if sobran != 1 else ""
    return "\n".join(
        [
            *lineas[:_MAX_LINEAS_NOTA],
            f"[dim](+{sobran} línea{plural} — usá --json para el detalle)[/dim]",
        ]
    )


def _fila_de_evidencia(item: Evidence) -> Text:
    ubicacion = f"{item.file}:{item.line}"
    texto = Text(ubicacion, style="cyan")
    texto.append("  ")
    texto.append_text(Text.from_markup(_recortar(item.note)))
    return texto


@dataclass
class Display:
    """`rico` pinta el reporte; `progreso` (o su ausencia) manda el spinner.

    Son dos decisiones separadas a propósito: con `> salida.txt` en una
    terminal, el archivo tiene que salir en texto plano y el spinner tiene
    que seguir viéndose.
    """

    salida: Console
    progreso: Console | None
    rico: bool

    @classmethod
    def para(cls, *, json_mode: bool) -> "Display":
        """`--json` apaga todo: es una interfaz de máquina."""
        rico = not json_mode and sys.stdout.isatty()
        progreso = (
            Console(file=sys.stderr) if not json_mode and sys.stderr.isatty() else None
        )
        return cls(salida=Console(file=sys.stdout), progreso=progreso, rico=rico)

    def con_progreso(
        self, nombre: str, verify: Callable[..., VerifierResult]
    ) -> Callable[..., VerifierResult]:
        """El mismo verificador, con un spinner alrededor.

        Composición pura sobre el tipo `Verifier` que ya existe: el objeto
        que devuelve el verificador es el mismo objeto que recibe quien lo
        llamó, sin copiar ni reconstruir.
        """

        def envuelto(ctx):
            with self.spinner(nombre):
                resultado = verify(ctx)
            self.termina(nombre, resultado.verdict)
            return resultado

        return envuelto

    def spinner(self, nombre: str):
        """Público porque el triage y `semantic_check` no pasan por el
        orquestador —corren aparte, necesitan ver los resultados de los demás
        (ADR 0002, ADR 0003)— y se anuncian a mano desde `cli.py`."""
        if self.progreso is None:
            return nullcontext()
        return self.progreso.status(f"[cyan]{nombre}[/cyan] corriendo…", spinner="dots")

    def hecho(self, nombre: str) -> None:
        """Un paso que terminó y no tiene veredicto propio, como el triage:
        revisa hallazgos ajenos, no produce los suyos."""
        if self.progreso is None:
            return
        self.progreso.print(f"✅ [bold]{nombre}[/bold]  [dim]listo[/dim]")

    def termina(self, nombre: str, verdict: Verdict) -> None:
        if self.progreso is None:
            return
        color = _COLOR[verdict]
        self.progreso.print(
            f"{_MARCA[verdict]} [bold]{nombre}[/bold]  [{color}]{verdict.value}[/{color}]"
        )

    def salteado(self, nombre: str) -> None:
        if self.progreso is None:
            return
        self.progreso.print(f"⏸️  [bold]{nombre}[/bold]  [dim]no ejecutado[/dim]")

    def reporte(self, report: AuditReport, repo_label: str) -> None:
        if not self.rico:
            # Idéntico a lo que hacía `cli.py` antes de esta capa. No
            # "parecido": es lo que mantiene válidos los tests de to_markdown.
            print(to_markdown(report, repo_label))
            return

        color = _COLOR[report.final_verdict]
        self.salida.print(
            Panel(
                f"[{color}][bold]{_MARCA[report.final_verdict]} {report.final_verdict.value}[/bold][/{color}]",
                title=f"[bold]{repo_label}[/bold]",
                border_style=color,
            )
        )

        tabla = Table(show_header=True, header_style="bold", expand=True)
        tabla.add_column("verificador", no_wrap=True)
        tabla.add_column("veredicto", no_wrap=True)
        tabla.add_column("evidencia")

        for nombre, resultado in report.verifier_results.items():
            estilo = _COLOR[resultado.verdict]
            veredicto = f"{_MARCA[resultado.verdict]} [{estilo}]{resultado.verdict.value}[/{estilo}]"
            if resultado.evidence:
                for indice, item in enumerate(resultado.evidence):
                    tabla.add_row(
                        nombre if indice == 0 else "",
                        veredicto if indice == 0 else "",
                        _fila_de_evidencia(item),
                    )
            else:
                tabla.add_row(nombre, veredicto, Text("(sin evidencia)", style="dim"))

        for nombre in report.skipped_verifiers:
            tabla.add_row(nombre, "⏸️  [dim]no ejecutado[/dim]", Text("pasá --run-tests", style="dim"))

        self.salida.print(tabla)
