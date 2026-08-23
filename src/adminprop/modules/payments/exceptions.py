"""Sin excepciones de dominio propias en este modulo (issues #22/#23/#24).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`NotFoundException`, `ExchangeRateRequiredException`,
`PaymentExceedsContractBalanceException`, `RentPeriodAlreadyPaidException`,
`PaymentAlreadyVoidedException`, `BusinessRuleViolationException` --
issue #24, RF-07: recibo sobre un cobro anulado) -- reusarlas evita
duplicar el catalogo de `sdd_03` §"Codigos de Error Globales" (mismo
criterio que `modules/contracts/exceptions.py`).
"""
