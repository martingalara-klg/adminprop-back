"""Sin excepciones de dominio propias en este modulo (issues #22/#23).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`NotFoundException`, `ExchangeRateRequiredException`,
`PaymentExceedsContractBalanceException`, `RentPeriodAlreadyPaidException`,
`PaymentAlreadyVoidedException`) -- reusarlas evita duplicar el catalogo
de `sdd_03` §"Codigos de Error Globales" (mismo criterio que
`modules/contracts/exceptions.py`).
"""
