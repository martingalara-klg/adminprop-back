"""normalize_double_encoded_json_columns — repara `roles.permissions` y
`organizations.settings` doblemente codificados (issue #116)

SDD: infrastructure/spec_data_model.md §Capa 0 "roles"/"organizations" +
     core/sdd_03_api_contracts.md §"Resumen de Autorizacion por Recurso"
     ("el chequeo es siempre por permiso atomico ... nunca por
     `role_name`" -- solo es posible si `permissions` es realmente un
     array de strings).

Causa raiz (confirmada, ver `modules/superadmin/repository.py` e
`modules/administracion/repository.py` antes de este commit):
`bindparam(..., type_=sa.JSON)` YA serializa el valor Python a JSON --
pasarle un valor ya serializado con `json.dumps()` lo codificaba una
SEGUNDA vez, dejando la columna JSONB con un escalar string (contenido =
el JSON del valor real) en vez del objeto/array real. La migracion
`20260828_130000_add_contract_terminate_permission.py` (issue #105)
concateno `permissions || '["contract:terminate"]'::jsonb` sobre filas ya
afectadas, produciendo un array MIXTO: `[<string JSON del array
original>, "contract:terminate"]`. Login devolvia ese array sin aplanar
-> menu vacio en produccion (evidencia completa en el issue #116).

Esta migracion es puramente de DATOS -- no toca el schema (`roles`/
`organizations` ya existen desde `20260812_212704_create_capa0_fundacion.py`)
-- y normaliza CUALQUIERA de las 3 formas observadas a la forma correcta:

`roles.permissions`:
- escalar string con el array serializado adentro -> se parsea a array.
- array con elementos string que a su vez son JSON de un array (forma
  post-#105) -> se aplanan sus elementos.
- array de strings simples -> sin cambios (elementos que no son JSON de
  un array pasan intactos).
- en todos los casos se dedupea preservando el orden de aparicion.

`organizations.settings`:
- escalar string con el objeto serializado adentro -> se parsea a objeto.
- objeto real -> sin cambios.

Idempotente: sobre datos ya normalizados (`jsonb_typeof = 'array'`/
`'object'` sin elementos string-JSON dentro) el `UPDATE`/loop no encuentra
nada que cambiar (mismo criterio de idempotencia via `IS DISTINCT FROM`
que evita un `UPDATE` no-op, y via `jsonb_typeof` para `settings`) --
requisito explicito del issue #116 porque el hotfix manual ya aplicado en
Railway dejo esos datos correctos y esta migracion debe ser inocua sobre
ellos.

No hay `downgrade()` con perdida de informacion posible: revertir a la
forma doblemente codificada reintroduciria a proposito el bug de
seguridad descripto en el issue (matching por subcadena en checks de
permisos) -- se documenta como no-op justificado, mismo criterio que
otras migraciones de datos de este repo cuando la reversion no aporta
(ver docstring de `downgrade()` abajo).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260829_090000"
down_revision: str | None = "20260828_130000"
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


def upgrade() -> None:
    # roles.permissions: aplana cualquier elemento string que a su vez sea
    # JSON de un array (cubre tanto el escalar-string puro -- envuelto acá
    # en un array de 1 elemento para reusar el mismo loop -- como el array
    # mixto post-#105), dedupea preservando orden.
    op.execute(
        """
        DO $$
        DECLARE
            r RECORD;
            arr jsonb;
            elem jsonb;
            elem_text text;
            inner_parsed jsonb;
            inner_elem jsonb;
            result jsonb;
            seen text[];
        BEGIN
            FOR r IN SELECT id, permissions FROM roles LOOP
                IF jsonb_typeof(r.permissions) = 'array' THEN
                    arr := r.permissions;
                ELSIF jsonb_typeof(r.permissions) = 'string' THEN
                    arr := jsonb_build_array(r.permissions #>> '{}');
                ELSE
                    arr := '[]'::jsonb;
                END IF;

                result := '[]'::jsonb;
                seen := ARRAY[]::text[];

                FOR elem IN SELECT * FROM jsonb_array_elements(arr) LOOP
                    IF jsonb_typeof(elem) != 'string' THEN
                        CONTINUE;
                    END IF;
                    elem_text := elem #>> '{}';
                    inner_parsed := NULL;
                    BEGIN
                        inner_parsed := elem_text::jsonb;
                    EXCEPTION WHEN others THEN
                        inner_parsed := NULL;
                    END;

                    IF inner_parsed IS NOT NULL AND jsonb_typeof(inner_parsed) = 'array' THEN
                        FOR inner_elem IN SELECT * FROM jsonb_array_elements(inner_parsed) LOOP
                            IF jsonb_typeof(inner_elem) = 'string' THEN
                                elem_text := inner_elem #>> '{}';
                                IF NOT (elem_text = ANY(seen)) THEN
                                    seen := array_append(seen, elem_text);
                                    result := result || to_jsonb(elem_text);
                                END IF;
                            END IF;
                        END LOOP;
                    ELSE
                        IF NOT (elem_text = ANY(seen)) THEN
                            seen := array_append(seen, elem_text);
                            result := result || to_jsonb(elem_text);
                        END IF;
                    END IF;
                END LOOP;

                IF result IS DISTINCT FROM r.permissions THEN
                    UPDATE roles SET permissions = result, updated_at = now() WHERE id = r.id;
                END IF;
            END LOOP;
        END $$;
        """
    )

    # organizations.settings: un solo nivel de doble-codificacion posible
    # (siempre un objeto, nunca concatenado como `permissions` -- no hay
    # equivalente al array mixto de #105 aca) -- un unico unwrap alcanza.
    op.execute(
        """
        UPDATE organizations
        SET settings = (settings #>> '{}')::jsonb,
            updated_at = now()
        WHERE jsonb_typeof(settings) = 'string'
        """
    )


def downgrade() -> None:
    # No reversible sin perdida deliberada: volver a doble-codificar
    # `permissions`/`settings` reintroduciria a proposito el bug de
    # seguridad (matching por subcadena en checks de permisos) que esta
    # migracion existe para eliminar. No-op documentado (mismo criterio
    # que las migraciones puramente de datos de este repo cuando revertir
    # no aporta valor real).
    pass
