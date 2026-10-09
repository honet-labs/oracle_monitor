"""Oracle Database via python-oracledb Thin (requires Oracle 12.1+)."""
OS_NAME='Oracle'
VERSION_SQL="SELECT banner FROM v$version WHERE banner LIKE 'Oracle%' AND ROWNUM = 1"

def format_product_identity(server_version, banner):
    import re
    version=str(server_version or '').strip()
    banner=' '.join(str(banner or '').split())
    if banner:
        banner=re.sub(r'\s+-\s+Production\b.*$','',banner, flags=re.I).strip()
        # Existing Oracle banner includes database edition and release if available.
        return banner[:128]
    return (('Oracle Database '+version).strip() if version else '')[:128]

def get_version_info(conn,cursor):
    version=str(getattr(conn,'version','') or '').strip()
    try:
        cursor.execute(VERSION_SQL)
        row=cursor.fetchone()
        banner=row[0] if row else ''
    except Exception:
        # V$VERSION privilege is not required for core database monitoring.
        # Retain the verified server version when the banner is not readable.
        banner=''
    return format_product_identity(version,banner)


def connect(cfg):
    try:import oracledb
    except ImportError as e:raise RuntimeError('Install python-oracledb in Pandora Discovery Python environment') from e
    host=cfg.get('host','')
    port=int(cfg.get('port') or 1521)
    if not 1<=port<=65535:raise ValueError('Invalid Oracle TCP port')
    service=cfg.get('service','').strip()
    if not service:raise ValueError('Oracle service name is required (not a database/schema name)')
    dsn=oracledb.makedsn(host,port,service_name=service)
    conn=oracledb.connect(user=cfg.get('user',''),password=cfg.get('password',''),dsn=dsn)
    conn.call_timeout=max(1000,int(cfg.get('statement_timeout_ms') or 15000))
    return conn

def init_session(conn,cursor,cfg):
    # One session; no pool. Monitoring DB login should only have SELECT privileges.
    # Oracle transaction read-only protects against accidental modification.
    try:cursor.execute('SET TRANSACTION READ ONLY')
    except Exception:pass  # A read-only monitoring account remains the security boundary.

def before_query(conn,cursor,cfg):pass

def on_query_error(conn,cursor,cfg):
    try:conn.rollback()
    except Exception:pass
    try:cursor.execute('SET TRANSACTION READ ONLY')
    except Exception:pass
