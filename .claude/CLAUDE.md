# graphify
- **graphify** (`.claude/skills/graphify/SKILL.md`) - any input to knowledge graph. Trigger: `/graphify`
When the user types `/graphify`, use the installed graphify skill or instructions before doing anything else.

## Flujo que ahorra tokens

Este repo ya tiene el grafo construido en `graphify-out/graph.json`. Cuando la
pregunta sea sobre el codebase —cómo funciona X, qué llama a Y, por dónde pasa
el flujo Z— la primera parada es el grafo, no `grep` sobre los archivos:

```
graphify query "<pregunta>"            # travesía BFS, contexto amplio
graphify query "<pregunta>" --dfs      # traza un camino puntual
graphify path "<nodo A>" "<nodo B>"    # camino más corto entre dos conceptos
graphify explain "<nodo>"              # explicación en lenguaje llano
graphify . --update                    # re-extrae solo lo nuevo o cambiado
```

`graphify query` devuelve el contexto ya recortado; leer los archivos crudos
mete todo el byte en la conversación y lo paga el resto de la sesión.

Dos límites que importan y no hay que olvidar:

- **El grafo tiene fecha.** `graphify-out/manifest.json` guarda el `mtime` de
  cada archivo indexado. Si el trabajo cambió código desde la última corrida,
  el grafo responde sobre el pasado. Correr `graphify . --update` después de
  mergear, no antes de preguntar.
- **El grafo no es la verdad, el código sí.** Es la misma regla que rige el
  proyecto entero: una respuesta del grafo que vaya a terminar en un commit,
  un veredicto o un número se confirma contra el archivo real antes de usarla.
  Sirve para saber **dónde mirar**, no para decidir qué es cierto — exactamente
  el mismo recorte que tiene `--ask` en el propio auditor.
