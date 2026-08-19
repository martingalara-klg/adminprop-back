"""Sin excepciones de dominio propias en este modulo (issue #22).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`NotFoundException`, `ExchangeRateRequiredException`,
`PaymentExceedsContractBalanceException`, `RentPeriodAlreadyPaidException`) --
reusarlas evita duplicar el catalogo de `sdd_03` §"Codigos de Error
Globales" (mismo criterio que `modules/contracts/exceptions.py`).
"""
