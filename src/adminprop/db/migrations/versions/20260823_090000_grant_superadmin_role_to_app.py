"""grant_superadmin_role_to_app — habilita SET ROLE real desde adminprop_app

SDD: core/sdd_04_nonfunctional.md §2.3
     + infrastructure/spec_data_model.md §Principios Arquitectonicos
Implements: issue #42 (el pool de runtime conecta como adminprop_app;
            SET ROLE adminprop_superadmin debe funcionar de verdad para
            /superadmin/*), Decision #42 (rol BYPASSRLS para Super Admin)

Contexto: hasta este issue, el pool de la API/workers conectaba con el
superusuario de Postgres (`adminprop`), que puede `SET ROLE` a cualquier
rol sin restricciones -- por eso `SET ROLE adminprop_superadmin` en
`db/session.py::get_superadmin_db_session` era "un no-op funcional": el
superusuario ya bypasseaba RLS antes y despues del SET ROLE, asi que el
cambio de rol no habilitaba ni restringia nada.

El issue #42 mueve el pool de runtime a `adminprop_app` (NOSUPERUSER,
NOBYPASSRLS -- ver `20260812_114322_setup_extensions_and_roles.py`).
Postgres exige que el rol de sesion sea *miembro* del rol destino para
que `SET ROLE <destino>` tenga exito (o que el rol de sesion sea
superusuario). Sin este GRANT, toda conexion `adminprop_app` que
intente `SET ROLE adminprop_superadmin` (rutas `/superadmin/*`) falla
con `permission denied to set role`.

`GRANT adminprop_superadmin TO adminprop_app` con `WITH INHERIT FALSE`
(Postgres 16): la membresia le permite a `adminprop_app` conmutar
explicitamente a `adminprop_superadmin` via `SET ROLE`, pero NO le
otorga automaticamente (por herencia implicita) los privilegios BYPASSRLS
de ese rol en el resto de sus queries -- el bypass solo aplica dentro de
la transaccion donde se ejecuto el `SET ROLE` explicito
(`db/session.py::get_superadmin_db_session` hace `RESET ROLE` al salir).
Esto preserva el invariante de `docs/skills/tenant-isolation.md`: el
bypass de RLS es una accion deliberada, no un efecto colateral de la
membresia.

Ademas, dos correcciones de grants encontradas al verificar el catalogo
real de Postgres (CA-42-04, "grants minimos documentados... verificar
coherencia") una vez que el runtime dejo de ser el superusuario:

1. `alembic_version`: nunca tuvo grant para `adminprop_app` (Alembic
   siempre corrio con el superusuario). Ahora que el runtime puede
   necesitar introspeccionar su propia revision de schema (health checks,
   tests de integracion existentes que verifican `alembic upgrade head`
   via el engine de runtime), se otorga SELECT -- solo lectura, ningun
   riesgo de que la app corrompa el estado de Alembic.
2. `organizations`: `20260812_212704_create_capa0_fundacion.py` otorga
   explicitamente solo `GRANT SELECT ON organizations TO adminprop_app`,
   pero `ALTER DEFAULT PRIVILEGES` (de `20260812_114322_setup_extensions_
   and_roles.py`) ya habia otorgado SELECT/INSERT/UPDATE/DELETE por
   default a toda tabla nueva creada por el mismo rol migrador -- un
   GRANT adicional no revoca privilegios ya obtenidos por otra via, asi
   que `adminprop_app` terminaba con INSERT/UPDATE/DELETE de facto sobre
   `organizations`. Se REVOCAN aca INSERT y DELETE (crear/borrar una
   organizacion sigue siendo exclusivo de `/superadmin/*` via
   `adminprop_superadmin`, RN-D / `CLAUDE.md` §8), pero se CONSERVA
   UPDATE: `modules/administracion/service.py::OrganizationSettingsService`
   (issue #9) es una funcionalidad real del owner/admin del tenant que
   actualiza `organizations.settings` (grace_day, billing_header, etc.)
   con el rol runtime normal -- revocar UPDATE tambien habria roto ese
   flujo ya probado (`tests/integration/administracion/test_organization_
   settings.py`). Mismo patron general que el REVOKE parcial de
   `audit_logs` en el issue #10 (angostar sin over-restringir).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260823_090000"
down_revision: str | None = "20260821_100000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # WITH INHERIT FALSE: adminprop_app no hereda BYPASSRLS automaticamente
    # en cada query -- solo lo obtiene mientras la transaccion actual tenga
    # `SET ROLE adminprop_superadmin` activo. Evita que un bug futuro que
    # olvide `RESET ROLE` dependa de la ausencia de INHERIT para fallar
    # cerrado en vez de abierto.
    op.execute("GRANT adminprop_superadmin TO adminprop_app WITH INHERIT FALSE")

    # CA-42-04: grants minimos -- correcciones encontradas al verificar
    # information_schema.role_table_grants contra el catalogo real.
    op.execute("GRANT SELECT ON alembic_version TO adminprop_app")
    op.execute("REVOKE INSERT, DELETE ON organizations FROM adminprop_app")


def downgrade() -> None:
    op.execute("GRANT INSERT, DELETE ON organizations TO adminprop_app")
    op.execute("REVOKE SELECT ON alembic_version FROM adminprop_app")
    op.execute("REVOKE adminprop_superadmin FROM adminprop_app")
