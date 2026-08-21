"""Sin excepciones de dominio propias en este modulo (issue #28).

`NotFoundException` (shared) cubre RN-D01 (propiedad/concepto/cargo
inexistente o de otro tenant); `ChargeEntryAlreadyExistsException` se
declara en `shared/errors/codes.py` (nueva en este PR, RF-05/CA-05-08)
-- mismo criterio que `modules/payments/exceptions.py`: reusar el
catalogo transversal de `sdd_03` §"Codigos de Error Globales" evita
duplicarlo por modulo.
"""
