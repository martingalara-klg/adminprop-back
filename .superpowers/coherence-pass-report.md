# Coherence pass — skills alineados con sdd_03/sdd_04

Fecha: 2026-08-06

Fuente de verdad usada:
- `docs/sdd/core/sdd_03_api_contracts.md` (catálogo de códigos de error, permisos, endpoints)
- `docs/sdd/core/sdd_04_nonfunctional.md` §1.3 (workers canónicos) y §2.2b (MFA post-MVP)

Decisiones aplicadas:
1. MFA es post-MVP → sin códigos `MFA_*` ni flujos `mfa_challenge_required`/`mfa_enrollment_required`; login MVP = único resultado exitoso `authenticated`.
2. No existe wizard de activación de módulos → sin `FEATURE_NOT_ACTIVATED` ni `WIZARD_INCOMPLETE`.
3. Índices de ajuste = ingreso manual del % → sin integración BCRA/INDEC, sin `indices_worker`, sin `INDEX_SERVICE_UNAVAILABLE`/`INDEX_VALUE_NOT_FOUND`. ICL/IPC Córdoba como índice de referencia informativo del contrato se conservan (no es lo obsoleto).
4. Sin IA en el MVP → sin `ANTHROPIC_API_KEY`.
5. Sin `MAINTENANCE_WORK_ORDER_NOT_ASSIGNED` (maintenance ve todos los work orders de la org) ni `PERIOD_LOCKED`/`PERIOD_OVERLAP` (no hay períodos bloqueados en adminprop).

## Backend (`adminprop-back`, rama `develop`)

### `docs/skills/error-handling.md`
- Reemplazadas las excepciones de ejemplo obsoletas:
  - `FeatureNotActivatedException` → `ContractOverlapException` (409, `CONTRACT_OVERLAP`)
  - `MaintenanceWorkOrderNotAssignedException` → `AdjustmentPendingExistsException` (409, `ADJUSTMENT_PENDING_EXISTS`)
  - `PeriodLockedException` → `SettlementAlreadyExistsException` (409, `SETTLEMENT_ALREADY_EXISTS`)
  - `PeriodOverlapException` → `AdjustmentAlreadyAppliedException` (409, `ADJUSTMENT_ALREADY_APPLIED`)
  - `IndexValueNotFoundException` → `ExchangeRateRequiredException` (400, `EXCHANGE_RATE_REQUIRED`)
  - `MfaInvalidCodeException` / `MfaTokenInvalidException` / `MfaConfirmationInvalidException` → `SettlementExchangeRateRequiredException` (400) + eliminadas las otras dos sin reemplazo directo
  - `WizardIncompleteException` → `RentPeriodAlreadyPaidException` (422, `RENT_PERIOD_ALREADY_PAID`)
  - `IndexServiceUnavailableException` (502) → eliminada (no hay código 502 en el catálogo)
- Actualizado el ejemplo de uso en `PaymentService.create` (reemplaza el chequeo de `PeriodLockedException` por `ExchangeRateRequiredException`, alineado con sdd_03 §9).
- `SENSITIVE_KEYS` del `ScrubFilter`: quitados `mfa_secret`, `mfa_recovery_code`, `totp_code`, `recovery_code`, `confirmation_totp_code`, `confirmation_recovery_code` (columnas MFA no existen en el modelo de datos del MVP); agregado `bank_info` (dato real cifrado con pgcrypto, sdd_04 §2.4).
- Antipatrón "HTTPException con detail=string" actualizado a `ContractOverlapException`.
- Tabla "Stack relevante" y referencias de "Cuándo leer este skill" ya no mencionan ICL/BCRA/IPC/INDEC como fuente de mapeo de errores (sólo Resend).

### `docs/skills/testing.md`
- Test de login `mfa_challenge_required` → reemplazado por `UC-LOGIN-03: 401 ACCOUNT_LOCKED tras 5 intentos` y `UC-LOGIN-04: 200 authenticated`.
- `FEATURE_NOT_ACTIVATED` → reemplazado por verificación de `error.code == "CONTRACT_OVERLAP"` con `details.conflicting_contract_id`.
- Sección "Mocks de servicios externos" reescrita: fixtures y fixture de `mock_icl_client`/`FakeBcraClient` reemplazados por `mock_resend_client`/`FakeResendClient` (único servicio externo real es Resend).
- Antipatrón "llamar a BCRA en CI" → reemplazado por "llamar a Resend en CI".
- Checklist y referencias a `PERIOD_LOCKED`/ICL/IPC actualizadas a `CONTRACT_OVERLAP`/Resend.

### `docs/skills/external-integrations.md`
- Reescrito completo: único servicio externo del MVP es **Resend**. Eliminados los ejemplos de fetch ICL/BCRA (`BCRA_ICL_ENDPOINT`, `get_icl_index`) e IPC/datos.gob.ar (`IPC_SERIES_ENDPOINT`, `get_ipc_index`).
- Conservado intacto el patrón `RetryableError`/`NonRetryableError` y la regla "nunca llamar al servicio real desde CI — fixtures", ejemplificado con Resend.
- Agregada la nota pedida: los índices de ajuste son ingreso manual (sdd_03 §8); si post-MVP se automatizan, este skill aplica al fetch de índices.

### `docs/skills/async-worker.md`
- Lista canónica alineada con sdd_04 §1.3: `notification_worker` + `documents_worker` + tareas Beat `generate_rent_periods` / `detect_due_adjustments` / `detect_expiring_contracts`. Eliminado `indices_worker` de `celery_app.include` y del `beat_schedule`.
- Ejemplo principal reemplazado: `indices_worker.aplicar_ajuste_contrato` → `documents_worker.generate_settlement_files(settlement_id, organization_id, request_id)`, conservando el patrón `set_tenant_context`, transiciones de status y propagación de `request_id`.
- Tabla "Política de reintentos por tipo de tarea", categorización Retryable/NonRetryable, antipatrones y checklist actualizados para no mencionar índices/BCRA/INDEC.

### `docs/runbooks/RUNBOOK-LOCAL-001-backend.md`
- Quitada la fila `ANTHROPIC_API_KEY` de la tabla de variables de entorno (§2.2).
- Actualizada la mención de "workers ilustrativos" (§4) a la lista canónica real (`notification_worker`, `documents_worker`, Beat).

### Otros archivos corregidos (detectados en el barrido adicional, no listados originalmente pero necesarios para que el grep de verificación diera vacío)
- `docs/skills/code-review.md`: ejemplos `PERIOD_LOCKED` → `CONTRACT_OVERLAP`.
- `docs/skills/git-workflow.md`: ejemplo de commit `feat(indices): wire BCRA ICL fetch into indices_worker` → `feat(liquidaciones): wire settlement PDF generation into documents_worker`.
- `docs/prompts/session-start.md`: checklist con `mfa_secret` en campos sensibles y "ICL/IPC/Resend" → `bank_info`/Resend.

**No se tocó** `docs/sdd` ni `docs/superpowers` (sdd_04 §2.2b menciona MFA legítimamente como post-MVP; el handoff es registro histórico).

## Frontend (`adminprop-front`, rama `develop`)

### `docs/skills/error-handling.md`
- Mapa de mensajes (`errorMessages`): quitados `FEATURE_NOT_ACTIVATED`, `WIZARD_INCOMPLETE`, `MFA_INVALID_CODE`, `MFA_TOKEN_INVALID`, `INDEX_SERVICE_UNAVAILABLE`, `INDEX_VALUE_NOT_FOUND`, `MAINTENANCE_WORK_ORDER_NOT_ASSIGNED`, `PERIOD_LOCKED`, `PERIOD_OVERLAP`; agregados con mensajes es-AR coherentes: `CONTRACT_OVERLAP` ("La propiedad ya tiene un contrato vigente en ese rango de fechas."), `CONTRACT_NOT_ACTIVE`, `EXCHANGE_RATE_REQUIRED`, `SETTLEMENT_EXCHANGE_RATE_REQUIRED`, `SETTLEMENT_ALREADY_EXISTS`.
- Tabla "Mapeo error.code → UX" actualizada (Page-level ya no lista `FEATURE_NOT_ACTIVATED`; Toast ya no lista `INDEX_SERVICE_UNAVAILABLE`; Inline alert usa `CONTRACT_OVERLAP`).
- Componente `ErrorStateByCode`: quitada la rama `FEATURE_NOT_ACTIVATED`/`FeatureNotActivatedState` y la rama `INDEX_SERVICE_UNAVAILABLE`; `PERIOD_LOCKED` → `CONTRACT_OVERLAP`.
- `securityMessages`: quitados los mensajes de recovery codes / mfa_via (MFA post-MVP).
- Template `useCreate<Module>` y ejemplo `PaymentForm`: `PERIOD_LOCKED` → `CONTRACT_OVERLAP` / `EXCHANGE_RATE_REQUIRED`.

### `docs/skills/flow-implementation.md`
- Ejemplo de login simplificado al flujo sin MFA: `LoginFlowState` ahora sólo `idle | loading | authenticated | error` (se quitaron `mfa_challenge_required` / `mfa_enrollment_required`), conservando la estructura didáctica de state machine y el comentario de anti-enumeration + `ACCOUNT_LOCKED`.
- Referencia a `MAINTENANCE_WORK_ORDER_NOT_ASSIGNED` → `404 NOT_FOUND` (RN-D01).
- Antipatrón de discriminación por `error.code`: `PERIOD_LOCKED` → `CONTRACT_OVERLAP`.

### `docs/skills/testing.md`
- Mismos reemplazos que el testing del backend: login `ACCOUNT_LOCKED`/`authenticated` en vez de MFA; `FEATURE_NOT_ACTIVATED` → `CONTRACT_OVERLAP`; fixtures/mocks de ICL/BCRA → Resend únicamente.

### `docs/runbooks/RUNBOOK-LOCAL-002-frontend.md`
- Fila de troubleshooting `401 UNAUTHORIZED con MFA_TOKEN_INVALID` → `401 UNAUTHORIZED en /auth/refresh` (verificar `withCredentials`).

### Otros archivos corregidos (barrido adicional)
- `docs/skills/code-review.md`: `PERIOD_LOCKED` → `CONTRACT_OVERLAP`.
- `docs/skills/tenant-context.md`: quitado el campo `mfa_via?: 'totp' | 'recovery_code'` del shape de sesión/JWT (dos ocurrencias).
- `docs/prompts/session-start.md`: quitada la mención de "recovery codes" / `mfa_via=recovery_code` del checklist de mensajes de seguridad.

## Verificación final

Backend y frontend, mismo comando sobre `docs/skills docs/runbooks docs/prompts`:

```
grep -rniE "MFA_|mfa_challenge|mfa_enrollment|FEATURE_NOT_ACTIVATED|WIZARD_INCOMPLETE|INDEX_SERVICE_UNAVAILABLE|INDEX_VALUE_NOT_FOUND|MAINTENANCE_WORK_ORDER_NOT_ASSIGNED|PERIOD_LOCKED|PERIOD_OVERLAP|ANTHROPIC|indices_worker|BCRA_ICL" docs/skills docs/runbooks docs/prompts
```

Resultado: sin coincidencias en ambos repos (exit code 1 / output vacío).

## Concerns / notas para el usuario

- El barrido tocó algunos archivos no listados explícitamente en el encargo (`code-review.md`, `git-workflow.md`, `tenant-context.md`, `session-start.md` en ambos repos) porque contenían las mismas referencias obsoletas y el grep de verificación exigía output vacío sobre todo `docs/skills`, `docs/runbooks` y `docs/prompts`.
- En `docs/skills/error-handling.md` (backend) también se ajustó `SENSITIVE_KEYS`/la tabla de scrubbing quitando claves específicas de MFA (`mfa_secret`, `mfa_recovery_code`, etc.) que no tienen columna en el modelo de datos del MVP; se agregó `bank_info` (dato real cifrado, sdd_04 §2.4) que faltaba en la lista.
- `AccountLockedException` en el skill backend sigue declarada con `status_code = 403`, pero `sdd_03` dice `ACCOUNT_LOCKED` es 401. Esta discrepancia es preexistente y **no** estaba en el alcance de esta tarea (no es un código obsoleto, es un status code potencialmente desalineado) — se deja señalada aquí para una futura pasada de coherencia, no se corrigió.
- No se tocó `docs/sdd` ni `docs/superpowers` en el backend, tal como se indicó.
