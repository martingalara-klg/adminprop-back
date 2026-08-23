"""Sin excepciones de dominio propias en este modulo (issue #13).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`NotFoundException`, `ForbiddenException`,
`EntityHasDependenciesException`) -- reusarlas evita duplicar el catalogo
de `sdd_03` §"Codigos de Error Globales" (mismo criterio que
`modules/administracion/exceptions.py`).
"""
