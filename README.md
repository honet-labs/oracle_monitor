# Oracle Database Monitoring for Pandora FMS

> **Version:** 1.0.1  
> **Platform:** Pandora FMS Discovery (Application)  
> **Package:** `pandorafms.oracle_monitor.disco`  
> **Status:** Community-maintained integration; test on your target versions before production use.

## Overview

A community-developed Pandora FMS Discovery extension for Oracle Database monitoring, including instance health, sessions, performance, schema storage, and custom SQL modules.

This repository contains a **Pandora FMS Discovery application**, not a Oracle Database installation and not a standalone Pandora agent. Monitoring runs remotely from the Pandora Discovery server and generates XML data modules ingested by Pandora FMS.

## Features

- **20 built-in SQL monitoring modules**, organized by group and individually enabled/disabled at group level.
- Up to **10 user-defined SQL modules per Discovery task**, entered via separate UI fields (name, type, unit, group, result mode and multiline query).
- Numeric and text modules, including a **readable multi-row/multi-column table** for Pandora snapshot views.
- Collector self-monitoring (connection status, query/error counters and collection time).
- **At most one database connection per execution**, with sequential SQL execution and per-task local overlap locking.
- Single-statement `SELECT`/`WITH` safety checking, query timeout controls, run logs, and per-task configuration supplied by Pandora.

## How it works

```text
Pandora Console (Discovery task wizard)
                 |
       discovery_definition.ini
                 |
        Python SQL collector
                 |
      1 database connection
                 |
        built-in + custom SQL
                 |
       Pandora XML .data file
                 |
        Pandora Data Server
                 |
        Agent / monitoring modules
```

## Requirements

- **Pandora FMS** with the Discovery Applications feature enabled (developed against the Pandora FMS 8.0NG.805 workflow; other versions not guaranteed).
- **Linux Pandora Discovery server** with `/usr/bin/python3` (the path invoked by this package).
- **Python driver:** `oracledb` (`python-oracledb`, default Thin mode).
- **Database/network:** Oracle listener TCP access (default `1521`), a valid **service name** (not SID), and monitoring username/password. Service name is required.
- Write permission to Pandora incoming directory (provided as `__incomingDir__`) and to the configured run-log location.
- Linux `flock` support, used to suppress overlapping executions on the **same host/task**.

### Install and verify the driver

Install `oracledb` into the `/usr/bin/python3` environment used by Discovery (for example, `python3 -m pip install oracledb` where permitted). This package uses **Thin mode**; Oracle Instant Client is not required for supported servers (Oracle Database 12.1 or later).

```bash
/usr/bin/python3 -c "import oracledb; print(oracledb.__version__)"
```

**Important:** Installing a driver into a virtual environment or `root` user environment does not automatically make it available to the system Python executable `/usr/bin/python3` or the service account running Discovery. Match the interpreter, package location, and filesystem permissions.

## Installation in Pandora FMS

1. Obtain `pandorafms.oracle_monitor.disco` from this repository or its GitHub Releases.
2. In Pandora FMS Console, open **Management → Discovery → Applications / Manage DISCO packages** (exact menu label may differ by build).
3. Select **Load/Upload DISCO** and upload the `.disco` file. Do **not** extract the archive before uploading.
4. Create a Discovery **Application** task for this extension, select the Discovery server and an execution interval (start with **300 seconds**).
5. Fill in the target host, port, database/service name, monitoring username/password and Pandora agent/group.
6. Enable the desired built-in groups and optionally create custom SQL modules; save the task.
7. Run the task, then check **Discovery Task execution summary**, resulting Pandora agent/modules and collection log.

The `.disco` file is a ZIP-format archive with a `.disco` extension. `discovery_definition.ini` must be located at the **archive root**.

## Configuration

| Setting | Purpose |
|---|---|
| Target host / port | Database endpoint reachable from Pandora Discovery server |
| Database / service | Database to connect to (Oracle uses a **service name**) |
| Monitoring credentials | Dedicated low-privilege database account |
| Agent name and group | Agent grouping in Pandora FMS |
| Built-in groups | Select which predefined metrics to collect |
| Custom SQL modules | Add up to ten custom monitoring modules |
| Result mode | `Auto`, `Table`, or `Scalar` for custom query output |
| Timeouts and overlap protection | Limit query runtime and duplicate task runs |

**Oracle:** supply the real listener **service name** (for example `ORCLPDB1`) rather than an Oracle SID. This adapter currently uses Thin mode only.

## Built-in monitoring

The bundled SQL catalog contains **20 modules**. Available monitoring groups: **Basic Info; Sessions; Performance; Storage; Security**. Examples include:

- instance version/uptime, sessions, reads/commits, schema-owned segment sizes and user/role information.
- `Sessions:List` and `Storage:TopTables` can produce table-like Pandora snapshot output.

Some built-in metrics require additional privileges or may differ by database edition/version. An individual query error is logged; it does not necessarily indicate a failed network connection.

## Adding custom SQL modules

In the Discovery task wizard, open **Custom SQL modules**, enable the feature, and enter a module in the field-based form. Enable the next module slot when needed.

| Field | Example |
|---|---|
| Name | `Active Connections` |
| Datatype | `generic_data` for numeric or `generic_data_string` / `async_string` for text |
| Result mode | `Auto` (single-cell scalar; multiple rows/columns become table), `Table`, or `Scalar` |
| Unit | `connections`, `bytes`, `%`, `ms`, etc. |
| Module group | `Custom SQL` |
| SQL query | Read-only `SELECT` or `WITH` query |

### Numeric module example

```sql
SELECT COUNT(*) FROM user_tables
```

### String module example

```sql
SELECT SYS_CONTEXT('USERENV', 'INSTANCE_NAME') FROM dual
```

### Tabular / snapshot module example

```sql
SELECT sid, serial#, username, status, machine
FROM v$session
WHERE username IS NOT NULL
FETCH FIRST 20 ROWS ONLY
```

For a multi-row result select a **string datatype** and **Table** mode. Custom SQL text can span multiple lines; each SQL textarea is materialized into its own Pandora temporary file to avoid truncation of multiline queries. Table output is intentionally bounded by a configurable maximum row count and may be truncated for large results.

**Query safety:** This extension applies a conservative read-only SQL syntax filter; this is **not a security boundary**. Always use read-only database permissions and avoid expensive full-table scans in frequent polling.

## Database permissions and security

Create a dedicated Oracle monitoring account with `CREATE SESSION`; grant only required SELECT privileges. For dynamic performance views, explicit grants on underlying `V_$...` objects are often required (for example `V_$SESSION`, `V_$INSTANCE`); avoid granting broad `SELECT ANY DICTIONARY` unless justified.

- Restrict access to database port(s) from the Pandora Discovery server only.
- Prefer encrypted and certificate-verified transport when supported; do not store credentials in Git, public logs or screenshots.
- Pandora writes sensitive temporary configuration files during execution. Protect the Pandora host, task permissions and temporary-file directories.
- Monitoring metrics that include session SQL text may expose application literals; review access to Pandora modules and logs.

## Database session usage

The collector opens **one `oracledb.connect()` connection** per task execution, runs sequential queries, and closes it at the end. Oracle call timeout is configured; it attempts to make the transaction read-only. A least-privilege DB account remains necessary.

The non-blocking lock prevents **overlapping runs of the same task on one Discovery host**. It is **not a distributed lock**: multiple tasks, different Pandora servers, or external monitoring clients can still create additional database sessions. Tune interval and timeouts according to query cost.

### Inspect collector sessions on the database

```sql
SELECT sid, serial#, username, program, status
FROM v$session
WHERE program LIKE '%Python%' OR program LIKE '%Pandora%';
```

## Troubleshooting

- **Dependency error:** run the driver verification command above using `/usr/bin/python3` on the selected Discovery server.
- **Connection timeout/refused:** check DNS/IP, TCP port, listener/bind address, firewall, DB authentication, and TLS configuration.
- **Permission denied / missing view:** inspect the failing SQL module and grant only the minimum needed database permissions.
- **`N/A` on a table module:** select `Table` with a text datatype and test with a **new module name**, since Pandora may preserve the existing module type. Also inspect task execution and SQL errors.
- **Query result empty:** confirm the query produces rows under the same DB user and database context.
- **Task unexpectedly skipped:** check whether another run holds the local task lock.

**Default run log:** ``/var/log/pandora-scan/oracle_discovery.run.log``.

```bash
tail -100 /var/log/pandora-scan/oracle_discovery.run.log
```

## Source files and packaging

The published `.disco` archive contains:

```text
pandorafms.oracle_monitor.disco
├── discovery_definition.ini
├── pandorafms_oracle.py
├── disco_core.py
├── db_adapter.py
├── queries_builtin.json
└── README.txt
```

To inspect/rebuild from extracted source files (requires `zip` / `unzip`):

```bash
unzip -l pandorafms.oracle_monitor.disco
unzip -t pandorafms.oracle_monitor.disco
# From the directory containing the files above:
zip -j pandorafms.oracle_monitor.disco discovery_definition.ini pandorafms_oracle.py disco_core.py db_adapter.py queries_builtin.json README.txt
```

Do not zip a containing parent directory; the `discovery_definition.ini` file must be directly inside the archive. You can use 7-Zip with **ZIP** output and rename `.zip` to `.disco` as well.

## Compatibility and project status

- **Plugin version:** `1.0.1`.
- Tested at package/parser/syntax level during development; **end-to-end compatibility with every Pandora FMS build or DB engine version is not guaranteed**.
- Monitoring uses database views/statistics and therefore may differ across versions or privilege sets.
- This collector connects using an **Oracle service name** via `oracledb.makedsn()`. Older Oracle Database releases requiring Thick mode are not supported by this packaged adapter without code changes.

## Contributing

Contributions are welcome for additional built-in metrics, query optimization, version compatibility, tests, documentation and safe monitoring use cases. Please include database version, Pandora FMS version, reproduction steps and sanitized logs when filing an issue. Do not submit passwords, private IP inventories or database query results containing sensitive values.

## License and trademarks

**License:** No license is included automatically in this README. The repository owner should add an explicit `LICENSE` file before distributing this project as open-source software. The name Pandora FMS and database/vendor names are trademarks of their respective owners. This is an **unofficial, independently developed** integration, not an official Pandora FMS package. Note that Pandora FMS documentation reserves the `pandorafms.` package `short_name` prefix for official integrations; consider a unique community/vendor prefix before public distribution.

## References

- [Pandora FMS: Discovery plugin/package workflow](https://pandorafms.com/manual/!current/en/documentation/pandorafms/monitoring/17_discovery_2)

- [Pandora FMS: .disco development and discovery_definition.ini](https://pandorafms.com/manual/!current/en/documentation/pandorafms/technical_reference/12_disco_development)

- [Pandora FMS: Data XML interface](https://pandorafms.com/manual/!current/en/documentation/pandorafms/technical_reference/01_development_and_extension)

- [python-oracledb installation](https://python-oracledb.readthedocs.io/en/latest/user_guide/installation.html)

- [Thin and Thick modes](https://python-oracledb.readthedocs.io/en/latest/user_guide/initialization.html)

- [Oracle Database Reference](https://docs.oracle.com/en/database/oracle/oracle-database/19/refrn/)
