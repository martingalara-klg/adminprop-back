-- docker/postgres/init.sql
-- Extensiones requeridas por el modelo de datos (spec_data_model.md):
--   pgcrypto    -> gen_random_uuid() en PKs + cifrado columnar (AES-256) de
--                  landlords.bank_info (sdd_04 §3 "Cifrado en reposo").
--   btree_gist  -> restricciones de no-solapamiento de contratos por
--                  propiedad (RN-C, EXCLUDE USING gist).
-- Se ejecuta una sola vez, en la creación inicial del volumen de datos
-- (docker-entrypoint-initdb.d sólo corre contra una base de datos vacía).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS btree_gist;
