"""Sin excepciones de dominio propias en este modulo (issue #15).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`NotFoundException`, `EntityHasDependenciesException`)
-- reusarlas evita duplicar el catalogo de `sdd_03` §"Codigos de Error
Globales" (mismo criterio que `modules/people/exceptions.py`).
"""
