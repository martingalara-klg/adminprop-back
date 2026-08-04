---
name: AdminProp - Gestión de Alquileres
description: Contexto general de AdminProp, sistema de gestión de alquileres para administradoras de propiedades, desarrollado con flujo SDD dirigido por agente
type: project
version: 1.0
fecha: 2026-08-04
---

AdminProp es un sistema de gestión de alquileres para corredores inmobiliarios y administradoras de propiedades. Nace para la operación de un corredor inmobiliario que administra más de 30 propiedades de múltiples dueños, y está diseñado multi-tenant desde el inicio para poder ofrecerse como SaaS a otras administradoras en el futuro.

**Por qué:** hoy la operación es manual: la secretaria cobra los alquileres, controla que los impuestos de cada propiedad se paguen mes a mes, calcula moras y ajustes a mano, y arma las liquidaciones de cada propietario en Excel. El ciclo mensual consume tiempo, depende de la memoria de dos personas y no deja rastro consultable.

**Cómo aplicar:** al discutir arquitectura, features o implementación, mantener este contexto presente. Los SDDs de `docs/sdd/` son la fuente de verdad.

## Módulos

0. **Superadmin** — Alta de administradoras (organizaciones), invitación del owner, deshabilitación. Portal separado, sin auto-registro.
1. **Propiedades** — Inventario de inmuebles: dirección, dueño, y registro informativo de números de cuenta de servicios e impuestos (rentas, municipalidad, luz con n° cliente y n° contrato, gas, agua, expensas/otros).
2. **Personas** — Propietarios (con % de comisión propio) e inquilinos (con sus datos). Son registros, no usuarios: no tienen login en MVP.
3. **Contratos** — Contrato de locación: propiedad + inquilino, moneda (ARS/USD), monto, duración, % de mora diaria, y para contratos en pesos: frecuencia de ajuste + índice de referencia (ICL, IPC Córdoba u otro, informativo). El ajuste se aplica con ingreso manual del % por el operador; el sistema avisa cuándo toca y lleva el historial. Los contratos en USD no se actualizan.
4. **Cobranzas** — El 1° de cada mes cada contrato activo genera su alquiler como "pendiente". Registro de cobros: efectivo o transferencia, en pesos o dólares (tipo de cambio libre si un contrato USD se paga en ARS), destino administración o cuenta del propietario ("dinero ya rendido"). Mora sugerida por el sistema a partir del día 11 con el % diario del contrato; el operador imputa libre y puede perdonar el interés total o parcialmente. Estado de deuda por inquilino.
5. **Liquidaciones** — Rendición mensual por propietario: total cobrado de sus propiedades − comisión de administración (% del dueño) − impuestos del mes (rentas y muni, valores variables cargados a mano) − reparaciones pagadas por la administración − dinero ya rendido. Export en Excel y PDF.
6. **Mantenimiento** — Pedido de reparación → cotización por el encargado (con fotos) → aviso → aprobación → ejecución → aviso de terminado. Cada reparación registra quién paga: **Dueño** (solo historial) o **Administración** (historial + descuento en la liquidación). Historial por propiedad.
7. **Administración** — Usuarios del equipo, roles y permisos, configuración de la administradora.

**Notificaciones (transversal):** avisos in-app y por email — ajuste de alquiler próximo/pendiente, cotización recibida, reparación terminada, contrato por vencer.

## Roles

- **Owner (corredor):** visibilidad y control total.
- **Admin (secretaria):** operación completa — cobranzas, contratos, liquidaciones, mantenimiento — excepto gestión de usuarios y configuración.
- **Maintenance (encargado de reparaciones):** solo el módulo de mantenimiento — ve las órdenes asignadas, sube cotizaciones y fotos, marca trabajos terminados. Nunca accede a contratos, cobranzas ni liquidaciones.
- **Super Admin:** empleado de la plataforma (`is_super_admin=true`); opera solo el portal `/superadmin/*`.

## Fuera del MVP

Facturación electrónica AFIP/ARCA (comisiones), portal para propietarios e inquilinos, carga automática de índices, recordatorios automáticos a inquilinos, KPIs avanzados, multi-idioma, cobranza SaaS integrada.
