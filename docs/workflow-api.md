# Workflow UI / API contract

The browser uses the existing `/api` session and resource specifications. The API validates ownership, freezes source files and SHAs, and controls resource allocation; browser validation is advisory.

`POST /sources/resolve {pr}` accepts a vllm-project/vllm-ascend PR number or URL and returns `{pr,head_sha,vllm_sha,repository?,url?}`. The browser sends the result with workflow submission; the API independently resolves/checks it.

`POST /sources/resolve {commit}` also accepts a complete lowercase 40-character vllm-ascend SHA. It verifies the commit in the official repository, reads its `.github/vllm-main-verified.commit`, and returns `{revision:"commit",commit,head_sha,vllm_sha,...}` without a PR number. PR, commit and branch inputs are mutually exclusive. File previews and workflow submissions independently verify either source form.

`POST /workflows` accepts `{idempotency_key,name,source,preset_id?,resource?,space_id?,retain_minutes,environments,jobs}`. Exactly one of resource and space_id selects a new or reusable space. Source uses a resolved PR, commit or main branch. Resource is the existing ResourceSpec. Reuse sends environments=[]; the API loads the existing environment inputs instead of accepting display/status fields as a mutation.

Environment: `{alias,role:'server'|'client',node_alias:'node0',image,shell,python,workdir,environment:{},packages:[{name,version,source}],bootstrap:Step,install:Step[],verify:Step[]}`. The authorized existing bootstrap is `{type:'shell',path:'/mnt/share/c00814587/start-docker-A3.sh',args:['${image}','${container_name}'],external:true}`. Other executable paths are relative to the frozen PR. Each environment has its own installation inputs. Workdir is absolute (default /home); aliases use lowercase [a-z][a-z0-9_]{0,31}. Service ports are 1024–65535.

Job: `{id,name,environment,kind:'batch'|'service',npu_count,ports:number[],depends_on:[{job_id,condition:'succeeded'|'ready'}],pre:Step[],steps:Step[],post:Step[],post_policy:'success'|'always',ready:Step[],timeout_seconds}`. Zero NPU is valid for request-only clients. Service readiness dependencies differ from successful completion dependencies.

Step: `{type:'shell'|'python'|'yaml',path,args:string[],uploaded_content?,inputs?:[{path,type:'yaml',uploaded_content?}],runner?:{type:'shell'|'python',path,args:string[]},external?:boolean}`. YAML is data and requires an explicit runner; `${input}` identifies its argument position. Edited content is frozen with its original source hash; execution uses the submitted bytes.

Repository-owned task helpers use the separate `hive_presets/<task-directory>/<file>` namespace. Only files explicitly listed in `preset_tasks/<task-directory>/source.json` are accepted, and uploaded bytes may be edited. The submitted task freezes their contents, size, SHA-256 and `origin:"hive_archive"`; materialization uses the same guarded upload mechanism and never overwrites upstream `tools` files. Upstream file uploads retain the original source path and hash, with modified bytes recorded separately.

`GET /presets` includes local task archives, with one public item per nightly YAML (stable UUID), `created_by`, `yaml_path`, `loadable`, and a separate `validation_status`. Local archives supersede duplicate historical publications in the list without modifying old tasks or their evidence. Any authenticated user may load/derive a configuration; resource submission still checks `can_request`. Availability does not imply a performance pass. Personal variants remain private and preserve saved scripts and parameters without replacing them from a newer archive.

`GET /workflows` returns an array. `GET /workflows/{id}` returns `{id,name,status,reason?,spec,space_id,jobs:[{id,name,status,phase?,reason?,logs:[{phase,text}],...}],created_at,owner_name?,started_at?,finished_at?}`. `POST /workflows/{id}/cancel` requests cancellation.

`GET /spaces` returns `[{id,status,owner_name,retain_until?,spec,environments:[{alias,role,node_alias,status,container_name?,reason?,...}]}]`. `POST /spaces/{id}/close` closes a retained space after its owned work exits safely.

`GET /presets` returns `[{id,name,enabled,reason?,tags:{},source?,workflow?}]`. Local archives carry editable helper attachments. A loadable archive does not imply a successful business test; historical database publications retain their enablement rules.

Browser tests exercise rendering, user input, dependencies, uploads and these API payloads through a separately served frontend. All API requests are intercepted; no business resources or remote commands are modified.


Artifact extension: jobs may declare `artifacts:[{path:absolute_path,label,kind:'file'|'metrics'}]`. Workflow creation may include `preset_id` after applying an enabled preset. GET job detail adds cards, endpoint, attempts, logs with display_truncated, downloadable artifacts, and generic metrics/verdict. Space environments add host/node_id/logical_ids/boot_id/logs/requested_packages/identity. Requested dependency versions are not presented as observed installed versions; external verification logs remain the evidence.

Downloads use `/api/workflows/{workflow}/jobs/{job}/artifacts/{artifact}` and `/api/workflows/{workflow}/jobs/{job}/logs/{phase}`. Successful process execution is labeled separately from external business verdicts; absent metrics/verdict remains “业务判定未提供”.


## Compact composer revision (2026-09-12)

New UI steps submit `{launch:string,files:[{name,content}]}`. Both fields are optional individually, but an empty step is rejected. Uploaded files are sh/py/yaml/yml (256 KiB text per file); the fixed PR tree resolves unique picked filenames to source paths. Executable contents may be edited; their original and submitted hashes are retained. YAML overrides are hashed separately and require a launch command. Upload aliases and original source paths are retained; conflicting names fail before allocation. A launch command runs in the task's fixed source workspace, in the bound environment shell. ${input} denotes the first uploaded file and requires an upload; Bash variables remain script-owned; known `${node0.ip}` style platform references are substituted. Legacy path/type/args/runner steps remain accepted; do not mix both shapes in one step.

Job timeout defaults to 600 seconds; the UI edits minutes. Ports remain a legacy API field and are omitted from new UI configuration. Dependencies come from graph edges. Artifacts accept `{environment,label,path}`; environment names a container in the same space, or defaults to the current job environment. Different environments may expose the same path. Missing kind defaults to `auto`: ordinary files stay raw, JSON declaring `metrics` is checked against the existing generic result protocol; `file` and `metrics` remain compatible API values.

Node resource mappings use `GET /api/nodes/{id}/mappings` and administrator `PUT {version,entries:[{kind,name,target}]}`. Kinds are model/dataset/image/package. NodeCreate optionally accepts mappings. First allocation freezes version and all entries per node; space environments expose resource_mappings, mappings_version and resolved_image. Image aliases resolve before local image inspection. Mapping edits do not mutate retained environments.

New submissions default to `runtime_variables:"minimal"`: only HIVE_SOURCE_DIR, indexed HIVE_NODE{n}_IP / HIVE_CONTAINER{n}_NAME, HIVE_TASK_ID, HIVE_JOB_ID and (with output_layout:"per-job") HIVE_OUTPUT_DIR are platform exports. Already-submitted spaces without this marker keep the legacy variable contract. Bash scripts can call `hive_resource kind name`; Python receives mapping results as explicit arguments. Registered paths must be accessible through the external container startup's mounts.

Upload filename discovery uses GitHub's [Git trees API](https://docs.github.com/en/rest/git/trees#get-a-tree) at the fixed head SHA. Truncated trees are rejected rather than treating partial matches as unique.

## Multi-node templates and personal cases (2026-09-13)

Source resolution accepts `{pr,revision:"head"|"merged"}`; omitted revision defaults to head. Merged requires an actually merged PR and pins its merge_commit_sha. Preserve revision on subsequent submission and preset import.

Environment `node_aliases:["node0","node1"]` supersedes legacy node_alias; role is optional and is not shown in the composer. Job node_aliases optionally selects a subset of its environment nodes. Logical definitions remain in space/workflow spec; the scheduler compiles independent persisted environment/job instances. Dependencies form an all-instances barrier. Limits are 128 environment instances and 256 job instances. Detail responses add logical_environments/logical_jobs with grouped instance status, retaining flat instances for compatibility.

Artifacts accept `targets:[{environment:"env0",node_alias:"node0"},{environment:"env0",node_alias:"node1"}]`, label and absolute path. Each selected environment/node target is archived once, with separate download identity. Each logical job has at most 16 path entries; expansion supports up to 2048 targets per instance, within byte limits. Indexed node/container variables expose the allocation; the UI lists their exact environment/node mapping. Legacy service placeholders remain accepted for old commands.

Artifact absolute paths support `${HIVE_TASK_ID}` and `${HIVE_JOB_ID}` (also bare `$HIVE_TASK_ID`/`$HIVE_JOB_ID`, and legacy `${task_id}`/`${job_id}`), for example `/var/tmp/results/${task_id}/${job_id}/result.json`. The API resolves them to the task UUID and executing job instance ID before persisting runnable jobs; logical definitions retain the template for reuse. Unknown placeholders and duplicate resolved target paths are rejected before resource allocation. Commands must write files to the matching location; Hive does not generate business results.

Resource `target_node_ids:[]` optionally restricts allocation candidates to registered UUIDs. It never falls back to an unlisted node. This is useful for controlled acceptance runs; published baselines remove the constraint.

`POST /presets/from-workflow {workflow_id,item_id,name,tags}` is admin-only. It saves an immutable, initially disabled baseline with source_workflow_id. Queued/preparing/running sources yield validation_status=pending_execution; successful sources yield execution_passed, retaining the upstream verdict separately. The source run's actual state is checked when reading and enabling the baseline; only SUCCEEDED can be enabled. Repeating the same request returns the original ID. Existing single-sample enable rules apply.

`POST /presets/{parent_id}/derive {name,tags,workflow}` saves a new personal case from an enabled parent. Workflow must contain resource/environments, not an allocated space_id; The currently selected source is saved; submission independently resolves it before execution. Response includes scope=personal, parent_id, root_id and validation_status=unverified. It is editable/loadable but does not assert successful execution. GET presets includes public baselines plus the caller's variants (administrators see all). Workflow submission checks ownership and the root baseline's enablement.

Browser drafts use per-username IndexedDB and include uploaded text, source, resource/environment/job settings and selected tabs. They restore across navigation/reload, are not server-side records, and are cleared only after successful submission or explicit reset.

## Current composer contract (2026-09-14)

`POST /sources/resolve {branch:"main"}` returns `{revision:"branch",branch:"main",head_sha,vllm_sha,...}`. Public archive loading resolves main then previews its original nightly YAML via `POST /sources/file {source,path}`; all matching attachments are refreshed. Either failure blocks submission. Private variants preserve their edited YAML and scripts. Submission rechecks the head, then freezes exact SHAs and bytes; a moved branch returns a conflict.

`GET /images/vllm-ascend/tags?page=1` requires login and returns `{tags:[{name,image,last_modified,manifest_digest}],page,has_more,repository}`. The API reads the official Quay repository, 50 tags/page, caches successful pages for five minutes, and returns 502 on upstream failure. Selecting a tag is read-only; any image pull occurs only during environment preparation.

The composer always submits a new resource request with `retain_minutes:0` and no `space_id`. Reuse/retention fields remain only for compatibility with historical API clients and already-submitted spaces; no such choice is exposed in the UI.

Every attachment can be edited. Identical names must have identical content throughout one submission. Archive helpers record `base_sha256`, `modified`, `origin` and the actual `sha256`; a personal variant preserves the edited bytes on reload. Each step supports `launch` plus its files; execution resolves from a task-specific checkout, not the mutable UI draft.

New commands use `HIVE_` environment variables in Shell/Python. See [workflow variables](workflow-variables.md). Job IDs whose normalized variable names collide are rejected. Old platform placeholders remain supported.

Omitted artifact targets resolve to every node in the job environment. Explicit targets select environment/node pairs; the UI labels them with server/container details. Directory artifacts are streamed tar.gz downloads, preserving relative paths. File limit4MiB; each directory is limited to256MiB unpacked/compressed and10000 entries; each job instance's aggregate cap512MiB. Symlinks and special files are rejected. Download metadata includes `format`, `download_name`, `media_type`, `unpacked_size` and `entry_count`. Completed archive conflicts fail the job without overwriting saved evidence; unknown remote identity/transport remains retryable.

## Simplified composer (2026-09-14, supersedes lifecycle options above)

New tasks always allocate environments and release them after all jobs and cleanup finish. Tabs and graph nodes use job0/job1/job2, independent of business purpose. Historical draft IDs remain internally stable so their old command references are not silently rewritten.

The six public variable groups are documented in [workflow variables](workflow-variables.md). Container numbering follows environment order and numeric node order, globally consistent across all jobs. No endpoint/host/port/image/source-SHA JSON variables are introduced for new tasks; business scripts own those settings.

Task files are displayed under their owning steps and are editable, with same-name edits synchronized; there is no global file summary. The Qwen3 preset shares one task.sh across stages instead of many wrapper scripts. Old drafts without script attachments display an explicit reload notice; restoration recovers missing job environments but does not overwrite edited commands.

`post` is labeled “执行后检查”: a script that runs after the main commands, with nonzero exit failing the job. It is not a manual approval gate. Service `ready` checks are separate and unblock downstream jobs only after service readiness succeeds.

## Per-job output directories and public preset management

New submissions default to `output_layout:"per-job"`. Job execution exports `HIVE_OUTPUT_DIR=/var/tmp/hive/outputs/<task_id>/<job_instance_id>` and creates the directory before steps. Artifact paths accept `$HIVE_OUTPUT_DIR` or `${HIVE_OUTPUT_DIR}`; runtime jobs persist resolved absolute paths. This directory is local to the selected container unless shared mounts are configured. Historical spaces without the marker keep their existing execution contract.

- `POST /api/presets/public` (admin): `{name,tags,remarks,yaml_path,workflow,base_preset_id?}`. Creates an unverified public preset; does not allocate resources or execute jobs. Source resolution freezes the supported upstream commit.
- `PATCH /api/presets/{id}/remarks` (admin): `{remarks}` (up to 4000 characters).
- Preset list/get responses include `remarks`; null uses tags outside the five primary table fields as default remarks.

Apply schema migration 009 before starting the updated services. Public definitions and remark overlays are stored in MySQL, without modifying repository preset archives.
