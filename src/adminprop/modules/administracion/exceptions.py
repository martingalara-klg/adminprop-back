"""Sin excepciones de dominio propias en este modulo (issue #9).

Todos los `error.code` que este modulo necesita ya viven en
`shared/errors/codes.py` (`UserAlreadyMemberException`,
`InvitationPendingExistsException`, `LastOwnerRequiredException`,
`SystemRoleImmutableException`, `RoleNotFoundException`, `NotFoundException`,
`ForbiddenException`, `ValidationError`) -- reusarlas evita duplicar el
catalogo de `sdd_03` §"Codigos de Error Globales".
"""
