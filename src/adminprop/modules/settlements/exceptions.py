"""Sin excepciones de dominio propias en este modulo (issue #29).

`NotFoundException` (shared) cubre RN-D01 (propietario/liquidacion
inexistente o de otro tenant); `SettlementAlreadyExistsException`/
`SettlementExchangeRateRequiredException`/`BusinessRuleViolationException`
se declaran en `shared/errors/codes.py` -- mismo criterio que
`modules/charges/exceptions.py`: reusar el catalogo transversal de
`sdd_03` §"Codigos de Error Globales" evita duplicarlo por modulo.
"""
