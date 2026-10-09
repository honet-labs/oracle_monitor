"""Oracle Database via python-oracledb Thin (requires Oracle 12.1+)."""
OS_NAME='Oracle'

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
