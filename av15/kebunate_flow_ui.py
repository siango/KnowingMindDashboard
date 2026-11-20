"""AV15 KEBUNATE Flow Studio, attached to existing MCP V5 authenticated origin.

Routes /flow, /flow/api. Outer existing Access JWT gate is mandatory.
Read-only observation; no fake process, queue, receipt or execution claim.
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = Path(__file__).resolve().parent / "kebunate_flow.html"


def _pg_snapshot():
    output = {"state":"UNAVAILABLE", "database":"km_microops", "tables":{}, "events":[], "memory_cases":[]}
    service=os.environ.get("KMCO_PG_SERVICE", "")
    if not service:
        output["reason"]="KMCO_PG_SERVICE_NOT_CONFIGURED"
        return output
    try:
        import psycopg
        with psycopg.connect("service="+service,connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '3000ms'")
                cur.execute("SELECT current_database(), current_user")
                db, role=cur.fetchone()
                if db!="km_microops" or role=="postgres":
                    raise ValueError("DB_IDENTITY_SCOPE_DENIED")
                for table in ("registry","bindings","relations","routes","usage_events","core_memory"):
                    cur.execute("SELECT COUNT(*) FROM circuit."+table)
                    output["tables"][table]=cur.fetchone()[0]
                cur.execute("""
                    SELECT id,recorded_at,operation,operation_ids,success,error_class,
                           mission_id,task_id,result_sha256
                    FROM circuit.usage_events ORDER BY id DESC LIMIT 30
                """)
                output["events"]=[{"id":r[0],"at":r[1].isoformat(),"tool":r[2],
                    "operations":r[3],"ok":r[4],"error_class":r[5],
                    "mission_id":r[6],"task_id":r[7],"result_sha256":r[8]}
                    for r in cur.fetchall()]
                cur.execute("""
                    SELECT operation,result_state,attempt_count,updated_at,
                           (nirodha->>'independent_qc') AS qc,
                           (samudaya->>'verification') AS cause_verification
                    FROM circuit.core_memory ORDER BY updated_at DESC LIMIT 30
                """)
                output["memory_cases"]=[{"operation":r[0],"state":r[1],
                    "attempts":r[2],"updated_at":r[3].isoformat(),
                    "independent_qc":r[4],"cause_verification":r[5]}
                    for r in cur.fetchall()]
                output["state"]="DB_READBACK"
                output["role"]="NON_SUPERUSER"
    except Exception:
        output["state"]="UNAVAILABLE"
        output["reason"]="EXACT_DB_READBACK_FAILED"
        output["tables"]={}
        output["events"]=[]
        output["memory_cases"]=[]
    return output


def _safe_process_state():
    # A process match is discovery only, never an execution/lease proof.
    try:
        p=subprocess.run(["pgrep","-af","[k]mnw|[k]nowingmind-network-worker"],
                         capture_output=True,text=True,timeout=2)
        return "CANDIDATE_FOUND_NOT_VERIFIED" if p.returncode==0 else "PROCESS_NOT_FOUND"
    except Exception:
        return "NOT_MEASURED"


def _origin():
    # The origin is serving this very request; not evidence of MCP tools/call.
    return "ORIGIN_HTTP_RESPONDING_NOT_MCP_EXECUTION_VERIFIED"


def snapshot():
    now=datetime.now(timezone.utc).isoformat()
    db=_pg_snapshot()
    return {
        "schema":"KEBUNATE_FLOW_STUDIO_OBSERVATION_V1",
        "observed_at":now,
        "source":"AV15_EXISTING_MCPV5_ORIGIN",
        "mode":"READ_ONLY",
        "live_data":db["state"]=="DB_READBACK",
        "db":db,
        "nodes":[
            {"id":"KMCW","name":"KMCW V3","state":"SOURCE_PRESENT_EXECUTION_NOT_MEASURED","evidence":"local source; not a daemon claim"},
            {"id":"KMCO","name":"KMCO 132","state":"DESIGN_ONLY_EXCEPT_VERIFIED_P01","evidence":"132 design records from prior checkpoint; database rows measured below"},
            {"id":"CORE","name":"รู้–จำ–ทำ / อริยสัจสี่","state":"DB_READBACK" if db["state"]=="DB_READBACK" else "UNAVAILABLE","evidence":"core_memory readback; no independent success claim"},
            {"id":"PG","name":"PostgreSQL km_microops","state":db["state"],"evidence":"same-request SQL readback"},
            {"id":"VECTOR","name":"pgvector search","state":"NOT_VERIFIED","evidence":"extension availability is not semantic retrieval"},
            {"id":"KMNW","name":"KMNW Worker","state":_safe_process_state(),"evidence":"process discovery only; ACK and lease not tested"},
            {"id":"MCP","name":"MCP V5","state":_origin(),"evidence":"signed Access HTTP route; tools/call not proven"},
            {"id":"QC","name":"Independent QC","state":"NOT_VERIFIED","evidence":"no independent receipt"}
        ],
        "edges":[
            {"from":"KMCW","to":"KMCO","state":"PREVIOUS_LOCAL_P01_PASS"},
            {"from":"KMCO","to":"PG","state":"CURRENT_DB_EVENTS" if db["state"]=="DB_READBACK" else "UNAVAILABLE"},
            {"from":"PG","to":"CORE","state":"CURRENT_DB_MEMORY" if db["state"]=="DB_READBACK" else "UNAVAILABLE"},
            {"from":"PG","to":"VECTOR","state":"NOT_VERIFIED"},
            {"from":"KMNW","to":"KMCW","state":"NOT_VERIFIED"},
            {"from":"MCP","to":"KMCW","state":"NOT_VERIFIED"},
            {"from":"CORE","to":"QC","state":"NOT_VERIFIED"}
        ],
        "acceptance":{"local_p01":"PREVIOUS_EVIDENCE_PASS",
                      "whole_six_systems":"NOT_VERIFIED",
                      "production_authority":False,
                      "independent_receipt":False}
    }


async def handle(scope, receive, send):
    path=scope.get("path","")
    method=scope.get("method","GET")
    if method not in ("GET","HEAD"):
        return await _respond(send,405,b'{"error":"METHOD_NOT_ALLOWED"}',"application/json")
    if path=="/flow":
        try:
            if UI.is_symlink() or not UI.is_file() or UI.stat().st_size>150000:
                raise OSError()
            body=UI.read_bytes()
        except OSError:
            return await _respond(send,503,b'{"error":"FLOW_UI_UNAVAILABLE"}',"application/json")
        return await _respond(send,200,b"" if method=="HEAD" else body,"text/html; charset=utf-8",len(body))
    if path=="/flow/api":
        data=json.dumps(snapshot(),ensure_ascii=False,separators=(",",":")).encode("utf-8")
        return await _respond(send,200,b"" if method=="HEAD" else data,"application/json; charset=utf-8",len(data))
    return await _respond(send,404,b'{"error":"NOT_FOUND"}',"application/json")


async def _respond(send,status,body,content_type,total=None):
    headers=[(b"content-type",content_type.encode("ascii")),
             (b"cache-control",b"no-store, max-age=0"),
             (b"x-content-type-options",b"nosniff"),
             (b"x-frame-options",b"DENY"),
             (b"content-security-policy",b"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
             (b"content-length",str(total if total is not None else len(body)).encode("ascii"))]
    await send({"type":"http.response.start","status":status,"headers":headers})
    await send({"type":"http.response.body","body":body})
