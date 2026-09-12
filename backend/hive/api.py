from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Request, Depends, Query
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.encoders import jsonable_encoder
import pymysql
from .config import Settings
from .domain import DomainError, decode
from .schemas import Login, MemberPermissions, NodeConnection, NodeCreate, NodeUpdate, RequestCreate, TaskCreate


def respond(value, status=200):
    return JSONResponse(jsonable_encoder(value, custom_encoder={
        datetime: lambda d: d.replace(tzinfo=timezone.utc).isoformat().replace("+00:00","Z")
    }), status_code=status)


def create_app(services=None, settings=None):
    settings = settings or Settings.from_env()
    if services is None:
        from .services import build_services
        services = build_services(settings)
    app = FastAPI(title="Hive", version="0.1.0")
    app.state.services = services

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return respond({"detail": str(exc)}, exc.code)

    @app.exception_handler(pymysql.IntegrityError)
    async def integrity_error(request, exc):
        return respond({"detail": "记录已存在或数据引用冲突"}, 409)

    @app.exception_handler(pymysql.OperationalError)
    async def database_error(request, exc):
        return respond({"detail": "MySQL 暂不可用，请检查服务配置"}, 503)

    @app.middleware("http")
    async def same_origin(request, call_next):
        if request.url.path.startswith("/api"):
            if request.method not in {"GET","HEAD","OPTIONS"}:
                origin = request.headers.get("origin")
                if origin and origin.rstrip("/") != settings.origin.rstrip("/") or request.headers.get("sec-fetch-site") == "cross-site":
                    return respond({"detail":"请求来源不匹配"},403)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        return await call_next(request)

    def current(request: Request):
        return services.identity.current(request.cookies.get("hive_session"))


    @app.get("/api/health")
    def health():
        services.db.one("SELECT 1 AS ok")
        return {"status":"ok"}

    @app.post("/api/session")
    def login(body: Login):
        token, actor = services.identity.login(body.username, body.password)
        response = respond(asdict(actor))
        response.set_cookie("hive_session",token,max_age=86400,httponly=True,
                            secure=settings.cookie_secure,samesite="lax",path="/")
        return response

    @app.get("/api/session")
    def session(actor=Depends(current)):
        return asdict(actor)

    @app.get('/api/members')
    def members(actor=Depends(current)):
        return respond(services.identity.members(actor))

    @app.patch('/api/members/{member_id}')
    def member_permissions(member_id: str, body: MemberPermissions, actor=Depends(current)):
        services.identity.permissions(actor, member_id, body.model_dump())
        return {'ok': True}

    @app.delete('/api/members/{member_id}')
    def remove_member(member_id: str, actor=Depends(current)):
        services.identity.remove(actor, member_id)
        return {'ok': True}

    @app.delete("/api/session")
    def logout(request: Request, actor=Depends(current)):
        services.identity.logout(request.cookies["hive_session"],actor)
        response=respond({"ok":True})
        response.delete_cookie("hive_session",path="/",secure=settings.cookie_secure,httponly=True,samesite="lax")
        return response

    @app.get("/api/nodes")
    def nodes(actor=Depends(current)):
        return respond(services.inventory.list_nodes())

    @app.post("/api/nodes")
    def admit(body: NodeCreate, actor=Depends(current)):
        services.inventory.require_admin(actor)
        payload = body.model_dump()
        verified = services.onboarding.check(payload, enroll=True)
        payload.update(model=verified['model'], metadata=verified['metadata'], boot_id=verified['boot_id'])
        return respond(services.inventory.create(actor,payload),201)

    @app.post("/api/nodes/check")
    def check_node(body: NodeConnection, actor=Depends(current)):
        services.inventory.require_admin(actor)
        return respond(services.onboarding.check(body.model_dump()))

    @app.delete("/api/nodes/{node_id}")
    def remove_node(node_id: str, actor=Depends(current)):
        services.inventory.remove(node_id, actor)
        return {"ok": True}

    @app.patch("/api/nodes/{node_id}")
    def update_node(node_id: str, body: NodeUpdate, actor=Depends(current)):
        return respond(services.inventory.update(node_id,actor,body.model_dump(exclude_none=True)))

    @app.post("/api/nodes/{node_id}/probe")
    def probe_node(node_id: str, actor=Depends(current)):
        services.inventory.request_probe(node_id,actor)
        return {"ok":True,"status":"queued"}

    @app.post("/api/nodes/{node_id}/credentials")
    def credentials(node_id: str, actor=Depends(current)):
        return respond(services.inventory.credentials(node_id,actor))

    @app.post("/api/nodes/{node_id}/compute-benchmark")
    def compute_benchmark(node_id: str, actor=Depends(current)):
        return respond(services.compute_benchmark.request(node_id,actor),202)

    @app.post("/api/devices/{device_id}/baseline")
    def baseline(device_id: str, actor=Depends(current)):
        services.inventory.confirm_baseline(device_id,actor)
        return {"ok":True}

    @app.get("/api/devices/{device_id}/history")
    def history(device_id: str, minutes: int=Query(default=10,ge=1,le=1440),actor=Depends(current)):
        return respond(services.telemetry.history(device_id,minutes))

    @app.get("/api/metrics")
    def metrics(actor=Depends(current)):
        return services.catalog.describe()

    @app.get("/api/devices/{device_id}/rollups")
    def rollups(device_id: str,resolution: int=Query(default=60),days: int=Query(default=1,ge=1,le=365),actor=Depends(current)):
        if resolution not in (60,3600) or resolution==60 and days>30:
            raise DomainError("仅支持分钟（30天内）或小时（365天内）聚合",422)
        return respond(services.reporting.history(device_id,resolution,days))

    @app.get("/api/usage")
    def usage(actor=Depends(current)):
        return respond(services.reporting.usage())

    @app.get("/api/nodes/{node_id}/connectivity")
    def connectivity(node_id: str,actor=Depends(current)):
        rows=services.db.all("""SELECT c.*,s.slot AS source_slot,t.slot AS target_slot,n.host AS target_host
                              FROM connectivity_checks c JOIN devices s ON s.id=c.source_device
                              JOIN devices t ON t.id=c.target_device JOIN nodes n ON n.id=t.node_id
                              WHERE s.node_id=%s ORDER BY n.host,s.slot,t.slot""",(node_id,))
        for row in rows:
            row["detail"]=decode(row["detail"])
        return respond(rows)

    @app.get("/api/requests")
    def requests(actor=Depends(current)):
        return respond(services.resources.list())

    @app.post("/api/requests")
    def submit_request(body: RequestCreate, actor=Depends(current)):
        if body.spec.purpose != "debug":
            raise DomainError("自动任务请从任务接口提交",422)
        return respond(services.resources.create(actor,body.spec.model_dump(),body.idempotency_key),201)

    @app.post("/api/requests/{request_id}/release")
    def release_request(request_id: str, actor=Depends(current)):
        return respond(services.resources.release(request_id,actor))

    @app.get("/api/tasks")
    def tasks(actor=Depends(current)):
        return respond(services.execution.list(actor))

    @app.post("/api/tasks")
    def submit_task(body: TaskCreate, actor=Depends(current)):
        return respond(services.execution.submit(actor,body.model_dump()),201)

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, actor=Depends(current)):
        return respond(services.execution.cancel(task_id,actor))

    @app.get("/api/tasks/{task_id}/logs")
    def logs(task_id: str, actor=Depends(current)):
        return respond(services.execution.logs(task_id,actor))

    @app.get("/api/summary")
    def summary(actor=Depends(current)):
        nodes=services.inventory.list_nodes()
        devices=[d for n in nodes for d in n["devices"]]
        states={key:sum(d["status"]==key for d in devices) for key in ("available","allocated","external","unknown","fault","maintenance")}
        return {"nodes":len(nodes),"devices":len(devices),**states,
                "queued":services.db.one("SELECT COUNT(*) AS n FROM resource_requests WHERE status='QUEUED'")["n"],
                "running":services.db.one("SELECT COUNT(*) AS n FROM executions WHERE status IN ('RUNNING','PREPARING','STARTING')")["n"]}

    @app.get("/api/audit")
    def audit(limit: int=Query(default=100,ge=1,le=500),actor=Depends(current)):
        services.inventory.require_admin(actor)
        rows=services.db.all("SELECT * FROM audit_events ORDER BY created_at DESC LIMIT %s",(limit,))
        for row in rows:
            row["detail"]=decode(row["detail"])
        return respond(rows)

    dist=Path(__file__).resolve().parents[2]/"frontend"/"dist"
    if (dist/"assets").is_dir():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")

    @app.get("/{path:path}",include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise DomainError("接口不存在",404)
        if not (dist/"index.html").exists():
            return respond({"detail":"前端尚未构建；开发时运行 frontend 中的 npm run dev"},503)
        if path == "hive.svg" and (dist/path).exists():
            return FileResponse(dist/path)
        return FileResponse(dist/"index.html")
    return app
