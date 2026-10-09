#!/usr/bin/env python3
"""Shared Pandora FMS Discovery SQL collector; read-only monitoring, single connection."""
from __future__ import annotations
import argparse
import datetime
import fcntl
import html
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "1.0.5"
STRING_TYPES = {'generic_data_string', 'async_string'}
NUMERIC_TYPES = {'generic_data', 'async_data', 'generic_proc', 'async_proc'}
ALLOWED_TYPES = STRING_TYPES | NUMERIC_TYPES


def as_bool(value, default=False):
    if value is None: return default
    return str(value).strip().lower() in ('1', 'yes', 'true', 'on')


def as_int(value, fallback, low, high):
    try: value = int(float(value))
    except (ValueError, TypeError): value = fallback
    return max(low, min(value, high))


def escape(value):
    return html.escape(str('' if value is None else value), quote=True)


def xml_text(value):
    v = str('' if value is None else value)
    return ''.join(c for c in v if c in '\t\n\r' or 0x20 <= ord(c) <= 0xd7ff or 0xe000 <= ord(c) <= 0xfffd or 0x10000 <= ord(c) <= 0x10ffff)


def cdata(value):
    return xml_text(value).replace(']]>', ']]]]><![CDATA[>')


def name_clean(value, fallback='DB'):
    return re.sub(r'\s+', ' ', str(value or '').strip())[:200] or fallback


def agent_clean(value):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', str(value or 'DB')).strip('_') or 'DB'


def read_cfg(path):
    cfg = {}
    raw=Path(path).read_text(encoding='utf-8')
    # PHP INI keeps \n as two literal chars in a single-line tempfile spec.
    # Pandora versions may also materialize these as actual newlines.
    if '\n' not in raw and r'\n' in raw:
        raw=raw.replace(r'\n', '\n')
    for line in raw.splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            k,v = line.split('=',1)
            cfg[k.strip()] = v.strip()
    return cfg


def log(path, level, message):
    if not path: return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path,'a',encoding='utf-8') as f:
            f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} [{level}] {message}\n')
    except OSError: pass


def mask_error(exc, cfg):
    msg = str(exc)
    for secret in [cfg.get('password',''), cfg.get('ssl_ca_password','')]:
        if secret and len(secret)>=3: msg=msg.replace(secret,'[REDACTED]')
    return msg[:900]


def error_advice(message, cfg, engine='MySQL'):
    """Actionable English troubleshooting guidance for Discovery Task Summary.

    Never include credentials or connection-string secrets here.
    """
    msg = str(message).lower()
    host = cfg.get('host', '').strip()
    dbname = cfg.get('database', '').strip()
    if 'certificate key too weak' in msg or 'ee certificate key too weak' in msg or 'ca md too weak' in msg:
        return ('SQL Server TLS certificate uses a weak key or signature. Replace it with a trusted, modern server certificate '
                '(for example RSA 2048+ bits with SHA-256). For isolated troubleshooting only, enable '
                '"Trust server certificate (test only)" while keeping "Encrypt connection" enabled; '
                'this bypasses certificate verification but might not resolve every TLS handshake failure.')
    if 'dh key too small' in msg or 'no suitable signature algorithm' in msg:
        return ('TLS negotiation rejected weak cryptographic parameters. Upgrade the SQL Server TLS certificate/configuration; '
                'do not lower the Pandora server-wide OpenSSL security policy.')
    if 'odbc driver not installed' in msg:
        return ('Choose an installed SQL Server ODBC driver in the Discovery Task or install Microsoft ODBC Driver 17/18. '
                'Inspect installed drivers using: odbcinst -q -d.')
    if any(s in msg for s in ('name or service not known', 'name resolution', 'nodename nor servname', 'getaddrinfo')):
        if host.lower() == dbname.lower() and host:
            return f'The Host/IP value is identical to the database name. Enter the {engine} server hostname or IP address, not the database name.'
        return 'Check the database Host/IP field and DNS resolution from the Pandora Discovery server.'
    if any(s in msg for s in ('certificate verify failed','certificate chain','self-signed certificate','ssl provider','unknown ca')):
        if engine == 'MSSQL':
            return ('Check the SQL Server TLS certificate, issuing CA, hostname and key strength. '
                    'For temporary testing only, enable "Trust server certificate (test only)". '
                    'For production, install a trusted server certificate with strong cryptography.')
        return 'Verify TLS settings, the trusted CA certificate and the hostname on the server certificate.'
    if any(s in msg for s in ('access denied','authentication failed','login failed','ora-01017')):
        return f'Check the {engine} monitoring username/password, database permissions and applicable authentication settings.'
    if 'connection refused' in msg or 'errno 111' in msg:
        return f'Check the {engine} service status, TCP port, bind/listen configuration and target firewall.'
    if 'timed out' in msg or 'timeout' in msg:
        return 'Check network routing, firewall rules, target TCP port and connection timeout.'
    if 'permission denied' in msg or 'insufficient privileges' in msg:
        return f'Grant the necessary read-only monitoring permissions to the {engine} monitoring account.'
    return 'Review the full error and collector log on the Pandora Discovery server.'

def sql_is_safe_readonly(sql, allow_show=False):
    """Defense in depth. Real authorization MUST use a read-only DB account."""
    value=str(sql or '').strip()
    # SQL comments at beginning and trailing single semicolon accepted.
    while True:
        m=re.match(r'^(?:--[^\n]*(?:\n|$)|/\*[\s\S]*?\*/\s*)',value)
        if not m: break
        value=value[m.end():].lstrip()
    # Reject any additional statements; semicolon in a string literal is allowed.
    # Conservative lexer: single/double quoted strings, backtick and square bracket identifiers.
    state = None
    statements = 0
    i = 0
    while i<len(value):
        c=value[i]
        if state:
            if (state=='[' and c==']') or (state!='[' and c==state):
                if i+1<len(value) and value[i+1]==c and state!="[": i+=2;continue
                state=None
        elif c in ('\"',"'",'`','['):
            state=c
        elif c==';':
            statements+=1
            if value[i+1:].strip(): return False
        i+=1
    if statements>1: return False
    if re.search(r'\bINTO\s+(?:OUTFILE|DUMPFILE)\b|\bFOR\s+UPDATE\b',value,re.I): return False
    return bool(re.match(r'^(SELECT|WITH)\b',value,re.I) or (allow_show and re.match(r'^SHOW\s+GLOBAL\s+STATUS\s+LIKE\s+\x27[A-Za-z0-9_]+\x27\s*;?$',value,re.I)))


def format_table(rows, columns, limit):
    columns=[str(v) for v in columns]
    rows=list(rows or [])
    shown=rows[:limit]
    def cell(v):
        txt='NULL' if v is None else str(v)
        txt=txt.replace('\r\n',' ').replace('\n',' ').replace('\r',' ').replace('\t',' ')
        return txt[:280]+('...' if len(txt)>280 else '')
    data=[columns]+[[cell(x) for x in r] for r in shown]
    if not columns: return 'N/A'
    widths=[max(len(row[i]) if i<len(row) else 0 for row in data) for i in range(len(columns))]
    lines=[]
    for pos,row in enumerate(data):
        line=' | '.join((row[i] if i<len(row) else '').ljust(widths[i]) for i in range(len(columns)))
        lines.append(line)
        if pos==0:lines.append('-'*len(line))
    if len(rows)>limit:lines.append(f'... showing first {limit} of {len(rows)} rows')
    result='\n'.join(lines)
    return result[:62000]+'\n... output truncated' if len(result)>62000 else result


def extract_value(cursor, datatype='generic_data', result='auto', limit=50):
    desc=cursor.description
    rows=list(cursor.fetchall()) if desc else []
    cols=[d[0] for d in desc] if desc else []
    if result == 'status_value':
        v=rows[0][1] if rows and len(rows[0])>=2 else 0
        return v, datatype
    if result in ('table','full') or (result=='auto' and (len(rows)>1 or (rows and len(rows[0])>1))):
        return format_table(rows,cols,limit), datatype if datatype in STRING_TYPES else 'generic_data_string'
    if not rows or rows[0][0] is None: return ('N/A' if datatype in STRING_TYPES else 0),datatype
    return rows[0][0],datatype


def xml_module(name,value,datatype='generic_data',unit='',group='',description=''):
    datatype=datatype if datatype in ALLOWED_TYPES else 'generic_data_string'
    chunks=['<module>',f'<name>{escape(name_clean(name))}</name>',f'<type>{escape(datatype)}</type>']
    if unit:chunks.append(f'<unit>{escape(unit)}</unit>')
    if group:chunks.append(f'<module_group>{escape(group)}</module_group>')
    if description:chunks.append(f'<description>{escape(description)}</description>')
    if datatype in STRING_TYPES: chunks.append(f'<data><![CDATA[{cdata(value)}]]></data>')
    else: chunks.append(f'<data>{escape(value)}</data>')
    chunks.append('</module>')
    return '\n'.join(chunks)


def xml_agent(engine, cfg, agent, modules, database_version=''):
    """Report the database engine version in the Pandora agent inventory.

    Pandora XML uses `os_version` for the Version inventory column and
    `version` for the software agent version.  Both refer to the monitored
    database, not the Disco collector release.  When the database is down,
    omit both so an existing agent's previous version is not overwritten.
    """
    timestamp=datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    group=cfg.get('group','Databases') or 'Databases'
    address=cfg.get('host','')
    attrs=(f'agent_name="{escape(agent)}" timestamp="{timestamp}" '
           f'group="{escape(group)}" os_name="{escape(engine.OS_NAME)}" '
           f'alias="{escape(agent)}" address="{escape(address)}"')
    version=str(database_version or '').strip()[:128]
    if version:
        attrs+=f' os_version="{escape(version)}" version="{escape(version)}"'
    return '<agent_data '+attrs+'>\n'+'\n'.join(modules)+'\n</agent_data>\n'


def parse_custom(cfg, paths):
    if not as_bool(cfg.get('custom_enabled')):return []
    items=[]
    for i in range(1,11):
        if not as_bool(cfg.get(f'custom{i}_enabled')):continue
        name=cfg.get(f'custom{i}_name','').strip()
        sql_path=Path(paths[i-1]) if len(paths)>=i else None
        sql=sql_path.read_text(encoding='utf-8',errors='replace').strip() if sql_path and sql_path.is_file() else ''
        # Preserve multiline SQL. Never use the key=value config for SQL text.
        if not sql: continue
        items.append(dict(name=name,sql=sql,
                          datatype=cfg.get(f'custom{i}_datatype','generic_data'),
                          result=cfg.get(f'custom{i}_result','auto'),
                          group=cfg.get(f'custom{i}_group','Custom SQL'),
                          unit=cfg.get(f'custom{i}_unit',''),
                          description='User-defined read-only SQL module'))
    return items


def take_lock(path):
    f=open(path,'a+')
    try:fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB);return f
    except BlockingIOError:f.close();return None


def execute(engine_name):
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',required=True)
    ap.add_argument('--sql-files',nargs=10)
    ap.add_argument('--outdir',default='/var/spool/pandora/data_in')
    ap.add_argument('--lock-file',default=f'/tmp/pandorafms-{engine_name}.lock')
    ap.add_argument('--run-log')
    ap.add_argument('--stdout',action='store_true')
    args=ap.parse_args()
    cfg=read_cfg(args.config)
    adapter=importlib.import_module('db_adapter')
    host=cfg.get('host','').strip()
    user=cfg.get('user','').strip()
    target=cfg.get('database','').strip() or cfg.get('service','')
    agent=agent_clean(cfg.get('agent') or f'{engine_name.upper()}-{host}-{target}')
    prefix=cfg.get('module_prefix','')
    runlog=args.run_log or cfg.get('run_log','')
    group='{} Collector'.format(adapter.OS_NAME)
    modules=[]
    started=time.monotonic()
    counts={'success':0,'error':0,'queries':0,'connections':0}
    errors=[]
    database_version=''
    fatal_error=False
    lock=None
    conn=None
    if not host or not user:
        print(json.dumps({'summary':{'Result':'ERROR'},'info':'Host and monitoring username are required'}));return 2
    if as_bool(cfg.get('overlap_protection'),True):
        lock=take_lock(args.lock_file)
        if not lock:
            log(runlog,'WARNING',f'Skip overlapping execution agent={agent}')
            print(json.dumps({'summary':{'Agent':agent,'Result':'SKIPPED'},'info':'Prior run is still active. No new DB session created.'}))
            return 0
    try:
        log(runlog,'INFO',f'Start agent={agent} host={host}; one {adapter.OS_NAME} session max')
        conn=adapter.connect(cfg)
        counts['connections']=1
        cursor=conn.cursor()
        try:
            adapter.init_session(conn,cursor,cfg)
            # Version lookup reuses the SAME DB session and cursor.
            try:
                # Reuse the existing cursor and connection to get a complete
                # product label (engine + version + real edition/distribution).
                database_version=adapter.get_version_info(conn,cursor)
                if adapter.OS_NAME!='Oracle':
                    counts['queries']+=1
            except Exception as version_exc:
                # Version inventory is optional; never break monitoring if
                # metadata lookup is unavailable.
                log(runlog,'WARNING',f'Unable to retrieve DB version: {mask_error(version_exc,cfg)}')

            modules.append(xml_module(prefix+f'{adapter.OS_NAME}:Connection',1,'generic_proc',group=group))
            modules.append(xml_module(prefix+f'{adapter.OS_NAME}:CollectorSessions',1,'generic_data','session',group))
            catalog=json.loads((Path(__file__).parent/'queries_builtin.json').read_text(encoding='utf-8'))
            queries=[]
            for item in catalog:
                if as_bool(cfg.get(item.get('flag','basic_info')),True):queries.append(item)
            queries.extend(parse_custom(cfg,args.sql_files or []))
            max_rows=as_int(cfg.get('max_rows'),50,1,500)
            for item in queries:
                mod_name=item.get('name','').strip()
                sql=item.get('sql','').strip()
                if not mod_name or not sql:
                    counts['error']+=1
                    errors.append('Query definition invalid: module name or SQL is empty')
                    continue
                allow_show=bool(item.get('builtin',False) and adapter.OS_NAME in ('MySQL',) )
                if not sql_is_safe_readonly(sql,allow_show):
                    counts['error']+=1
                    log(runlog,'WARNING',f'Rejected non-read-only or multi-statement SQL for {mod_name}')
                    errors.append(f'[{mod_name}] Rejected: SQL must contain exactly one read-only SELECT/WITH statement.')
                    continue
                try:
                    adapter.before_query(conn,cursor,cfg)
                    cursor.execute(sql)
                    counts['queries']+=1
                    dt=item.get('datatype','generic_data')
                    val,dt=extract_value(cursor,dt,item.get('result','auto'),max_rows)
                    modules.append(xml_module(prefix+mod_name,val,dt,item.get('unit',''),item.get('group',''),item.get('description','')))
                    counts['success']+=1
                except Exception as e:
                    counts['error']+=1
                    detail=mask_error(e,cfg)
                    errors.append(f'[{mod_name}] {detail}')
                    log(runlog,'WARNING',f'Query failed [{mod_name}]: {detail}')
                    try:adapter.on_query_error(conn,cursor,cfg)
                    except Exception:pass
            modules.append(xml_module(prefix+f'{adapter.OS_NAME}:CollectorQueries',counts['queries'],'generic_data','queries',group))
            modules.append(xml_module(prefix+f'{adapter.OS_NAME}:CollectorQueryErrors',counts['error'],'generic_data','errors',group))
            modules.append(xml_module(prefix+f'{adapter.OS_NAME}:CollectionTime',round((time.monotonic()-started)*1000),'generic_data','ms',group))
        finally:
            try:cursor.close()
            except Exception:pass
    except Exception as e:
        counts['error']+=1
        fatal_error=True
        msg=mask_error(e,cfg)
        errors.append(msg)
        log(runlog,'ERROR',f'Connection/collector failed agent={agent}: {msg}')
        # Connection availability is a legitimate metric, not an error-log module.
        modules.append(xml_module(prefix+f'{adapter.OS_NAME}:Connection',0,'generic_proc',group=group))
        modules.append(xml_module(prefix+f'{adapter.OS_NAME}:CollectorSessions',counts['connections'],'generic_data','session',group))
    finally:
        if conn:
            try:conn.close()
            except Exception:pass
        if lock:lock.close()
    body=xml_agent(adapter,cfg,agent,modules,database_version)
    if args.stdout:
        print(body)
        output='stdout'
    else:
        p=Path(args.outdir);p.mkdir(parents=True,exist_ok=True)
        dest=p/f'{agent}_{int(time.time())}_{os.getpid()}.data'
        tmp=dest.with_suffix('.data.tmp')
        tmp.write_text(body,encoding='utf-8')
        os.replace(tmp,dest)
        output=str(dest)
    log(runlog,'INFO',f'Finish agent={agent} OK={counts["success"]} err={counts["error"]} sessions={counts["connections"]} file={output}')
    status='FAILED' if fatal_error else ('PARTIAL' if counts['error'] else 'OK')
    summary={
        'Status':status,
        'Agent':agent,
        'DB':adapter.OS_NAME,
        'Database version':database_version or 'Unavailable',
        'Target':f'{host}:{cfg.get("port") or {"MySQL":"3306","MSSQL":"1433","Oracle":"1521"}.get(adapter.OS_NAME,"")}/{target}',
        'Modules OK':counts['success'],
        'Errors':counts['error'],
        'Queries executed':counts['queries'],
        'Sessions opened':counts['connections'],
        'Output':output,
    }
    # Pandora displays summary entries as key/value rows. Keep error details
    # out of Pandora monitoring modules and put them in the task result here.
    for i, detail in enumerate(errors[:5],start=1):
        summary[f'Error {i}']=detail[:450]
    if len(errors)>5:
        summary['Additional errors']=f'{len(errors)-5} more; inspect server log'
    if fatal_error:
        summary['Suggested action']=error_advice(errors[0],cfg,adapter.OS_NAME)
    elif errors:
        summary['Suggested action']='Review failed SQL modules (SELECT-only and correct privileges); successful modules were still collected.'
    info='Database session closed after task execution. Detailed errors are shown in this summary, not as modules.'
    print(json.dumps({'summary':summary,'info':info},ensure_ascii=False))
    # Official Pandora Discovery convention: nonzero exit status = task failed.
    # A single bad query is partial; other module data remains usable.
    return 1 if fatal_error else 0
