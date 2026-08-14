"""Sin modelos SQLAlchemy declarativos en este modulo (issue #9).

`roles`, `organization_members`, `organization_invitations` y
`organizations` fueron creadas por la migracion de Capa 0
(`20260812_212704_create_capa0_fundacion.py`, issue #5) y son compartidas
entre varios modulos (`auth`, `superadmin`, `administracion`) que todavia
no definieron un dueno ORM comun -- mismo criterio documentado en
`modules/auth/repository.py` y `modules/superadmin/repository.py`: SQL
crudo via `text()` en `repository.py`, sin declarar un modelo aca para no
imponer un ownership prematuro.
"""
