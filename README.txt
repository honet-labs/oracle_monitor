Oracle Database Pandora FMS Discovery v1.0.1

Package: pandorafms.oracle_monitor.disco
Python dependency: python3 -m pip install oracledb ; Thin mode needs no Oracle Instant Client (for supported DB versions).

Built-ins: 20 modules, grouped in Pandora Task UI.
Custom SQL: enable Custom SQL, fill Name, Datatype, Result mode, Unit, Group, Query. Up to 10 modules per task. Multiline SQL is stored via Discovery tempfile per query.
Auto result: 2+ rows or 2+ columns yields ASCII table string for Pandora Snapshot. For existing string modules, preserve async_string vs generic_data_string.
Queries are sequential on ONE database session per task execution; OS flock prevents overlapping runs on same local host. No central cross-host lock: multiple Discovery nodes/tasks can still open separate sessions.
Custom SQL is restricted to single SELECT or WITH query (no stacked SQL). Use dedicated read-only DB account for actual permission enforcement.
Credentials are passed via Pandora temporary config, not the process command line. Their presence in temporary files demands secure permissions on Pandora Discovery server.
Read-only account setup: Read-only database user. Most V$ views need SELECT on specific V_$ underlying views granted explicitly; user_tables and user_segments work with own schema permissions. ORA-00942 on V$ is a privilege error, not loss of connectivity.
Connection: Easy Connect host:port/service_name, python-oracledb Thin.
SQL errors: query errors are written to run log; a connection failure generates Connection=0 and ConnectionError module.
Operational note: Existing modules may keep their original type; test a new module name if replacing a numeric module with table string.
Validation: packages are syntax checked and mocked DB queries are tested; real DB and Pandora 805 end-to-end must be tested on your environment.
References:
https://pandorafms.com/manual/!current/en/documentation/pandorafms/technical_reference/12_disco_development

Version 1.0.1: Fixed Pandora FMS macro format; removed underscores inside macro bodies.
