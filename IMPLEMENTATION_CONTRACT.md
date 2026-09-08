# Cursor Session Hub implementation contract

Build on all current source/working-copy changes; keep legacy viewer runnable. Never put real transcripts/screenshots/credentials in Git. Preserve MIT license. Product locale Chinese, product title Cursor Session Hub.

## Ownership
- Backend agent: hub/config.py, hub/db.py (SQLAlchemy Core schema), hub/auth.py, hub/api.py, hub/__main__.py, backend auth/API tests. Publish db.py and interfaces first.
- Ingest agent: hub/ingest.py, hub/sources.py, hub/worker.py, hub/bundles.py, parsing/index/resource tests. Coordinate schema directly with backend agent.
- Frontend agent: frontend/** only (React TypeScript Vite, all dependencies local, no CDN). Use APIs below. Coordinate additions with backend agent.
- Root: desktop/** (Tauri), hub/remote.py, packaging, CI, Docker/deployment, migration, integration/performance/browser tests, dependencies and integration fixes.

## Shared Python
Python 3.12. FastAPI, uvicorn, SQLAlchemy Core 2, psycopg[binary], argon2-cffi, python-multipart, psutil, jieba, keyring, httpx, Pillow. hub is a package. Root provides requirements.txt.
Config from env CSH_MODE=local|cloud, CSH_HOME, CSH_DATABASE_URL (SQLite default; PostgreSQL cloud), CSH_LOCAL_TOKEN, CSH_WORKER_EXTERNAL=1. Config must expose mode, home:Path, database_url, local_token, worker_external. API create_app(config=None) -> FastAPI.
DB tables use opaque string UUID IDs and epoch float timestamps. Shared get_engine(config), init_db(engine), new_id(), now(). Every owner is user_id; local offline user is id='local'. Content and raw files outside DB; metadata_json/payload_json as JSON object columns or clearly specified encoding.
Ingest public function ingest_path(config, job_id) executes one persisted job and publishes atomic revision. Sources discover_sources(config) indexes light session metadata only. Worker run_forever(config), start_worker(config) with cancel/lease/single parser resource controls. Export and bundle work are queued jobs. Backend must never call legacy full parse in GET requests.

## API (prefix /api/v1)
JSON errors {detail:string}; HTTP 401 unauthorized,403 forbidden,409 conflict,413 limits,429 queue full. Catalog responses {items:[],next_cursor:string|null}. Timestamps epoch seconds. Login user {id,username,display_name,role,active}. Common session {id,title,original_title,owner_id,owner_name,project,source_kind,status,current_revision,event_count,round_count,updated_at,sync_status,comment_count,favorite}. Job {id,kind,state,progress,total,error,session_id,created_at,updated_at}; states queued/running/paused/succeeded/failed/cancelled.

- GET /capabilities -> {mode,local_import,local_sources,server_sync,comments,favorites,user?:User}
- GET /auth/me -> User (local returns local user); POST /auth/login {username,password} -> {user}; browser HttpOnly cookie. POST /auth/device-login -> {user,token} opaque revocable bearer. POST /auth/logout. POST /auth/register {token,username,display_name,password} -> {user}. No open registration.
- GET /members -> catalog; PATCH /members/{id} {active?,role?,display_name?} admin. POST /invites {} -> {id,token,expires_at}; one-time seven-day links. GET /invites; DELETE /invites/{id} admin.
- GET /sessions?cursor=&limit=50&owner_id=&project=&q=&favorite=&date_from=&date_to= -> catalog. GET /sessions/{id} -> session. PATCH {title}; DELETE (owner/admin revokes cloud copy). GET /sessions/{id}/revisions -> catalog.
- GET /sessions/{id}/rounds?revision=&cursor=&limit=100&recent=3 -> catalog of {number,start_seq,end_seq,preview,count}; recent sorts selected rounds chronologically. GET /sessions/{id}/events?revision=&round=&cursor=&limit=40 -> {items:[{id,seq,round_number,event}],next_cursor}; max1MiB. event is existing event_schema shape with text/tool payload capped and content_id/has_more references. Large text/tool payload retrieval GET /contents/{id}?cursor=&limit=262144 -> {text,next_cursor,kind}; attachment GET /assets/{id} authenticated stream. No raw disk paths in cloud API.
- GET /sources (local) -> {items:[{id,title,source_kind,project,path,status}]}; POST /sources/scan -> {job}; POST /sources/{id}/index -> {job,session_id}. Path/source_id references must be locally discovered/selected, not cloud.
- POST /imports (local multipart file) -> {job,session_id}; file <=512MiB streamed to storage, never JSON text request. Optional POST /imports/path {path} local authenticated native picker only.
- GET /jobs -> catalog; POST /jobs/{id}/cancel; POST /jobs/{id}/retry; POST /jobs/{id}/pause; POST /jobs/{id}/resume. GET /activity SSE authorized single connection; event data minimal job/session/comment update, no transcript text. Client reconnect backoff, no global 1s polling.
- GET /sessions/{id}/comments?revision=&event_id= -> catalog; POST {revision_id,event_id?,text}; PATCH /comments/{id} {text}; DELETE. Read all team; edit own/admin only. Anchors immutable revision/event IDs.
- GET /favorites -> catalog; POST /favorites {session_id,revision_id?,event_id?}; DELETE /favorites/{id}. Private per user.
- POST /sessions/{id}/exports {format:'html'|'markdown',revision_id?} -> {job}; GET /exports/{job_id}/download authorization rechecked, streaming.

## Sync interfaces (desktop -> cloud)
Root hub/remote.py registers local router with server configuration/login, preview and manual synchronization. Canonical package format csh-bundle-v1: manifest.json ({format,source_kind,native_id,title,project,device_id,source_hash,events_file:'events.jsonl',assets:[{id,path,sha256,bytes,mime}]}), events.jsonl existing normalized event records, assets/<hash>. No arbitrary extraction paths, absolute paths or symlinks. Native images identified only by explicitly referenced image records; preview may exclude selected IDs. Ingest agent implements build_bundle(config,session_id,revision_id,output_path,excluded_asset_ids=[]) and import_bundle(...), streaming. Preserve source/raw blocks as content references when needed.
- POST /uploads {filename,total_bytes,sha256,session_key,device_id} -> {id,chunk_size:4194304,received_chunks:[]}; content server chooses owner.
- GET /uploads/{id} -> status/received_chunks.
- PUT /uploads/{id}/chunks/{number} raw bytes, X-Chunk-SHA256, Content-Type application/octet-stream (max4MiB).
- POST /uploads/{id}/complete {} -> {job,session_id}; verify whole hash, queue atomic revision, idempotent. DELETE cancels own upload. max2 active global,1 per owner; server queue50,10/user. Disk low threshold5GiB or10% stops admission.
- GET /syncs?owner_id=&cursor= -> catalog owner/device/state/session/revision.

## Resource and UX invariants
One parser worker per installation, subprocess memory512MiB/Linux1CPU; batch commit200 events or1MiB. Oversize JSONL lines >8MiB stored as raw diagnostic with original bytes preserved; corrupt/unknown records visible, not silently dropped. Catalog/round/contents reads query disk index. Current source snapshot immutable; publish only complete revision; failed job leaves previous revision readable. Parser checkpoints/resume and file rewrite detection mandatory. Search indexed pretokenized Chinese text (jieba, SQLite FTS5/PostgreSQL tsvector or indexed token table), not in-memory full-session scans. Export/bundle also bounded background work.
Reader initially last3 rounds, max3 expanded, round directory paged, event pages40+byte cap, lazy details, older snapshots remain readable. Local refresh and remote sync manual. Image sync default on with preview exclusion. Default offline, cloud/team space requires login. Comments + favorites yes; no tasks/AI inference. UI nav local/team/favorites/jobs/admin members, blue accent neutral palette/dark mode. Incoming update banner, no disruptive rerender.
