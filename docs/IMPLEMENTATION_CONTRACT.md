# Shared implementation contract

All API timestamps are UTC ISO strings; all IDs are UUID strings except stable seed logical keys. All data shapes below are JSON dictionaries. Root owns persistence, API, ingestion, worker, schemas, project configuration and integration tests. Agent engine owns engine.py, provider.py and their tests. Frontend agent owns frontend/. Artifact agent owns demo.py, exports.py, export_support/, data/, evaluation.py and their tests. Do not overwrite another owner's files.

## API

- GET /api/health; GET /api/budget -> {limit_usd,spent_usd,reserved_usd,buckets}
- GET /api/projects -> Project[]; POST /api/projects -> Project; POST /api/demo -> seeded Project (idempotent only with same Idempotency-Key)
- GET /api/projects/{id} -> Project; PATCH same edits brief, only outside active run.
- GET /api/projects/{id}/sources -> Source[]; POST same JSON {title,text,competitor?,kind?,logical_key?,published_at?,url?}; POST /api/projects/{id}/upload multipart file, competitor optional.
- POST /api/projects/{id}/outline -> {dimensions,questions,estimated_cap_usd}; no model bill; edit brief dimensions before start.
- POST /api/projects/{id}/runs JSON {mode:replay|live,model_profile?:qwen-default|gemini-budget,architecture:multi|single|pipeline,budget_usd?,instructions?,bucket?:development|evaluation|web|reserve} -> Run; header Idempotency-Key supported. Only one active run per project. Replay only allowed for synthetic workspaces and must be labelled. New runs default to gemini-budget; historical runs preserve their original profile and no automatic model switch occurs.
- GET /api/projects/{id}/runs -> Run[]; GET /api/runs/{id}; POST /api/runs/{id}/cancel; POST /api/runs/{id}/resume; GET /api/runs/{id}/tasks -> Task[]
- GET /api/runs/{id}/events?after=0 -> SSE with id sequence, event message, JSON Event; GET /api/runs/{id}/event-log?after=0 -> Event[]
- GET /api/runs/{id}/collaboration -> report-bound decisions, evidence links, actual task intervals, follow-ups and reuse metrics. Missing historical instrumentation is null, never an invented zero or quality gain.
- GET /api/projects/{id}/reports -> Report[]; GET /api/reports/{id}; GET /api/projects/{id}/claims -> Claim[]
- POST /api/reports/{id}/revise JSON {instructions} -> Run (same provider mode and model_profile as original, new work revision)
- POST /api/reports/{id}/exports JSON {format:docx|pptx} -> ExportJob {id,status,format,report_id,error?,download_url?}; GET /api/exports/{id}; GET /api/exports/{id}/download.

## Objects

Project: id,title,question,audience,time_range:string(default 最近12个月),web_enabled:boolean,competitors:string[],dimensions:string[],mode:synthetic|public,status,created_at,updated_at,latest_report_id?,dirty:boolean. Synthetic projects never search the web; public projects may disable search for uploaded-only evidence. Public workspaces without this field retain the original enabled default. Brief edits are blocked during an active run.

Source: id,project_id,logical_key,title,competitor:string (empty for industry),kind,text,published_at:string|null,retrieved_at,url:string|null,synthetic:boolean,version:int,sha256,active:boolean. Old versions stay stored. Sources must never contain evaluation gold.

Evidence: source_id,quote,locator:string. Quotes must be exact substrings of the frozen source text. locator is descriptive (paragraph/page/row) and optional empty string. Numeric computations retain operands and formula as claim computation.

Claim: id,project_id,run_id,subject,dimension,statement,status:supported|uncertain|contradicted,evidence:Evidence[],value:string|null,conditions:string[],computation:dict|null,source_ids:string[],dirty:boolean.

Task: id,run_id,role,target,title,status:pending|running|completed|failed|cancelled,round:int,depends_on:string[],output:dict,error:string|null,created_at,updated_at.

Run: id,project_id,brief:frozenProject,mode:replay|live,model_profile:qwen-default|gemini-budget,architecture:multi|single|pipeline,status:queued|running|completed|failed|cancelled|budget_exceeded,budget_usd:number,bucket,spent_usd,reserved_usd,request_count,source_ids:string[],instructions,created_at,updated_at,report_id?,error?,phase?.

Event: id:int,run_id,type,message,payload:dict,created_at.

Report: id,project_id,run_id,version:int,title,executive_summary:string,sections:[{id,heading,body,claim_ids:string[]}],comparison:[{competitor,positioning,price,sso,conditions:string[],claim_ids:string[]}],claims:Claim[],sources:Source[],unresolved:string[],changes:string[],synthetic:boolean,mode:replay|live,model_profile:qwen-default|gemini-budget,created_at,content_hash. Frozen immutable report snapshot contains exact claims and sources. Exports consume this snapshot alone, no further model calls.

## Store surface (root implements; use dictionaries)

Store(database_url?, data_dir?) defaults environment. get_project(id), create_project(data), update_project(id,patch), list_projects(). add_source(project_id,data)->Source, list_sources(project_id,active_only=True), get_source(id). create_run(project_id,data)->Run, get_run(id), update_run(id,patch), list_runs(project_id). create_task(run_id,data)->Task, update_task(id,patch), list_tasks(run_id). add_event(run_id,type,message,payload=None)->Event. list_events(run_id,after=0)->list.

save_claims(project_id,run_id,claims)->list (upsert by subject+dimension; incoming evidence determines source_ids; keep unrelated existing claims); list_claims(project_id). save_report(project_id,run_id,report_data)->Report (sets id/version/hash and project latest); get_report(id); list_reports(project_id).

reserve_cost(run_id,amount_usd,request_key)->reservation dict (atomically check global 10, bucket 2/4/2/2, per-run and max40 requests; idempotent key; raise BudgetExceeded). settle_cost(reservation_id,actual_usd:float|None,metadata=None), budget_summary(). request_count counts every paid HTTP model attempt incl retries. Unknown costs keep reserve. assert_run_active(run_id) raises RunCancelled when cancelled, lease ownership enforced by worker integration.

Engine entry: async run_research(store,run_id)->Report; handles replay/live and architecture, persists steps/tasks/events, raises meaningful errors for worker. resume uses completed task outputs and durable graph checkpoint. Provider only receives key from env, never logs it. All tool/model/schema outputs validate.

Demo entry: seed_demo(store,scenario='default')->Project (30 sources); fixture_scenarios()-> list dictionaries for dev/test; gold remains evaluation side. Exports entry: export_report(report:dict,format:str,output_dir:Path)->Path; dependencies declared to root. evaluation CLI can call engine+Store.

## Product design

Chinese interface. Editorial SaaS workspace: warm off-white background, dark ink, forest-green accent, refined readable typography, source sidebar, document-like report canvas. 4 nav views: 研究概览 / 资料与证据 / 协作进度 / 报告与导出. Clearly show 虚构测试资料 and 固定响应回放 vs 真实模型. Public projects require live. UI never displays raw secrets. Support responsive desktop and narrow widths, empty/loading/failure states. No template picker needed for app's fixed export template; accepted plan explicitly picks a stable Word/PPT template generated with python-docx and PptxGenJS.
