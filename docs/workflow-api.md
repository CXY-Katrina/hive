# Workflow UI / API contract

The browser uses the existing `/api` session and resource specifications. The API validates ownership, freezes source files and SHAs, and controls resource allocation; browser validation is advisory.

`POST /sources/resolve {pr}` accepts a vllm-project/vllm-ascend PR number or URL and returns `{pr,head_sha,vllm_sha,repository?,url?}`. The browser sends the result with workflow submission; the API independently resolves/checks it.

`POST /workflows` accepts `{idempotency_key,name,source,resource?,space_id?,retain_minutes,environments,jobs}`. Exactly one of resource and space_id selects a new or reusable space. Source contains pr/head_sha/vllm_sha. Resource is the existing ResourceSpec. Reuse sends environments=[]; the API loads the existing environment inputs instead of accepting display/status fields as a mutation.

Environment: `{alias,role:'server'|'client',node_alias:'node0',image,shell,python,workdir,environment:{},packages:[{name,version,source}],bootstrap:Step,install:Step[],verify:Step[]}`. The authorized existing bootstrap is `{type:'shell',path:'/mnt/share/c00814587/start-docker-A3.sh',args:['${image}','${container_name}'],external:true}`. Other executable paths are relative to the frozen PR. Each environment has its own installation inputs. Workdir is absolute (default /home); aliases use lowercase [a-z][a-z0-9_]{0,31}. Service ports are 1024–65535.

Job: `{id,name,environment,kind:'batch'|'service',npu_count,ports:number[],depends_on:[{job_id,condition:'succeeded'|'ready'}],pre:Step[],steps:Step[],post:Step[],post_policy:'success'|'always',ready:Step[],timeout_seconds}`. Zero NPU is valid for request-only clients. Service readiness dependencies differ from successful completion dependencies.

Step: `{type:'shell'|'python'|'yaml',path,args:string[],uploaded_content?,inputs?:[{path,type:'yaml',uploaded_content?}],runner?:{type:'shell'|'python',path,args:string[]},external?:boolean}`. YAML is data and requires an explicit runner; `${input}` identifies its argument position. Uploading executable content verifies the same content in the frozen PR rather than bypassing PR provenance.

`GET /workflows` returns an array. `GET /workflows/{id}` returns `{id,name,status,reason?,spec,space_id,jobs:[{id,name,status,phase?,reason?,logs:[{phase,text}],...}],created_at,owner_name?,started_at?,finished_at?}`. `POST /workflows/{id}/cancel` requests cancellation.

`GET /spaces` returns `[{id,status,owner_name,retain_until?,spec,environments:[{alias,role,node_alias,status,container_name?,reason?,...}]}]`. `POST /spaces/{id}/close` closes a retained space after its owned work exits safely.

`GET /presets` returns `[{id,name,enabled,reason?,tags:{},source?,workflow?}]`. Presets contain external references, never Hive-owned business scripts. Unverified entries remain visible but cannot be applied for execution.

Browser tests exercise rendering, user input, dependencies, uploads and these API payloads through a separately served frontend. All API requests are intercepted; no business resources or remote commands are modified.


Artifact extension: jobs may declare `artifacts:[{path:absolute_path,label,kind:'file'|'metrics'}]`. Workflow creation may include `preset_id` after applying an enabled preset. GET job detail adds cards, endpoint, attempts, logs with display_truncated, downloadable artifacts, and generic metrics/verdict. Space environments add host/node_id/logical_ids/boot_id/logs/requested_packages/identity. Requested dependency versions are not presented as observed installed versions; external verification logs remain the evidence.

Downloads use `/api/workflows/{workflow}/jobs/{job}/artifacts/{artifact}` and `/api/workflows/{workflow}/jobs/{job}/logs/{phase}`. Successful process execution is labeled separately from external business verdicts; absent metrics/verdict remains “业务判定未提供”.
