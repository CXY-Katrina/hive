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


## Compact composer revision (2026-09-12)

New UI steps submit `{launch:string,files:[{name,content}]}`. Both fields are optional individually, but an empty step is rejected. Uploaded files are sh/py/yaml/yml (256 KiB text per file); the fixed PR tree resolves unique picked filenames to source paths. Executable contents must match the PR. YAML overrides are hashed separately and require a launch command. Upload aliases and original source paths are retained; conflicting names fail before allocation. A launch command runs in the task's fixed source workspace, in the bound environment shell. ${input} denotes the first uploaded file and requires an upload; Bash variables remain script-owned; known `${node0.ip}` style platform references are substituted. Legacy path/type/args/runner steps remain accepted; do not mix both shapes in one step.

Job timeout defaults to 600 seconds; the UI edits minutes. Ports remain a legacy API field and are omitted from new UI configuration. Dependencies come from graph edges. Artifacts accept `{environment,label,path}`; environment names a container in the same space, or defaults to the current job environment. Different environments may expose the same path. Missing kind defaults to `auto`: ordinary files stay raw, JSON declaring `metrics` is checked against the existing generic result protocol; `file` and `metrics` remain compatible API values.

Node resource mappings use `GET /api/nodes/{id}/mappings` and administrator `PUT {version,entries:[{kind,name,target}]}`. Kinds are model/dataset/image/package. NodeCreate optionally accepts mappings. First allocation freezes version and all entries per node; space environments expose resource_mappings, mappings_version and resolved_image. Image aliases resolve before local image inspection. Mapping edits do not mutate retained environments.

Runtime exports HIVE_NODE0_IP (and other allocated nodes), HIVE_HOST_IP, HIVE_CONTAINER_NAME and HIVE_RESOURCE_MAP_JSON. Bash scripts can call `hive_resource kind name`; Python scripts read the JSON environment variable. Registered paths must be accessible through the external container startup's mounts.

Upload filename discovery uses GitHub's [Git trees API](https://docs.github.com/en/rest/git/trees#get-a-tree) at the fixed head SHA. Truncated trees are rejected rather than treating partial matches as unique.

## Multi-node templates and personal cases (2026-09-13)

Source resolution accepts `{pr,revision:"head"|"merged"}`; omitted revision defaults to head. Merged requires an actually merged PR and pins its merge_commit_sha. Preserve revision on subsequent submission and preset import.

Environment `node_aliases:["node0","node1"]` supersedes legacy node_alias; role is optional and is not shown in the composer. Job node_aliases optionally selects a subset of its environment nodes. Logical definitions remain in space/workflow spec; the scheduler compiles independent persisted environment/job instances. Dependencies form an all-instances barrier. Limits are 128 environment instances and 256 job instances. Detail responses add logical_environments/logical_jobs with grouped instance status, retaining flat instances for compatibility.

Artifacts accept `targets:[{environment:"env0",node_alias:"node0"},{environment:"env0",node_alias:"node1"}]`, label and absolute path. Each selected environment/node target is archived once, with separate download identity. Each expanded job instance still has the 16-item limit. Runtime HIVE_NODES_JSON exposes all allocated nodes; explicit multi-node service placeholders include `${job0.node1.endpoint}`.

Artifact absolute paths support only `${task_id}` and `${job_id}` placeholders, for example `/var/tmp/results/${task_id}/${job_id}/result.json`. The API resolves them to the task UUID and executing job instance ID before persisting runnable jobs; logical definitions retain the template for reuse. Unknown placeholders and duplicate resolved target paths are rejected before resource allocation. Commands must write files to the matching location; Hive does not generate business results.

Resource `target_node_ids:[]` optionally restricts allocation candidates to registered UUIDs. It never falls back to an unlisted node. This is useful for controlled acceptance runs; published baselines remove the constraint.

`POST /presets/from-workflow {workflow_id,item_id,name,tags}` is admin-only. It saves an immutable, initially disabled baseline with source_workflow_id. Queued/preparing/running sources yield validation_status=pending_execution; successful sources yield execution_passed, retaining the upstream verdict separately. The source run's actual state is checked when reading and enabling the baseline; only SUCCEEDED can be enabled. Repeating the same request returns the original ID. Existing single-sample enable rules apply.

`POST /presets/{parent_id}/derive {name,tags,workflow}` saves a new personal case from an enabled parent. Workflow must contain resource/environments, not an allocated space_id; PR and fixed source SHAs remain the parent's. Response includes scope=personal, parent_id, root_id and validation_status=unverified. It is editable/loadable but does not assert successful execution. GET presets includes public baselines plus the caller's variants (administrators see all). Workflow submission checks ownership and the root baseline's enablement.

Browser drafts use per-username IndexedDB and include uploaded text, source, resource/environment/job settings and selected tabs. They restore across navigation/reload, are not server-side records, and are cleared only after successful submission or explicit reset.
