\set ON_ERROR_STOP on
-- The server reads its granted secret files; passwords never enter shell argv.
SELECT format('CREATE ROLE sage_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
              rtrim(pg_read_file('/run/secrets/db_migrate_password'), E'\r\n')) \gexec
SELECT format('CREATE ROLE sage_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
              rtrim(pg_read_file('/run/secrets/db_runtime_password'), E'\r\n')) \gexec
ALTER DATABASE sage_preview OWNER TO sage_migrate;
REVOKE ALL ON DATABASE sage_preview FROM PUBLIC;
GRANT CONNECT ON DATABASE sage_preview TO sage_runtime;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO sage_migrate;
GRANT USAGE ON SCHEMA public TO sage_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE sage_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sage_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE sage_migrate IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO sage_runtime;
