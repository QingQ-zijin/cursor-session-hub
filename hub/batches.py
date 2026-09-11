"""Durable workspace batches feed one ordinary parser/sync job at a time."""
import threading
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from sqlalchemy import select,func,literal,and_,or_
from sqlalchemy.exc import IntegrityError
from . import db

ACTIVE=('planning','active','indexing','syncing')

def queue_index(conn,config,source,enqueue):
    linked=conn.execute(select(db.sessions).where(db.sessions.c.id==source['session_id'],db.sessions.c.revoked.is_(False))).mappings().first() if source['session_id'] else None
    sid=linked['id'] if linked else db.new_id()
    old=conn.execute(select(db.jobs).where(db.jobs.c.session_id==sid,db.jobs.c.kind=='ingest',db.jobs.c.state.in_(('queued','running','paused')))).mappings().first()
    if old:return dict(old)
    if not linked:
        conn.execute(db.sessions.insert().values(id=sid,owner_id='local',title=source['title'] or '未命名会话',original_title=source['title'],project=source['project'],source_kind=source['source_kind'],native_id=source['native_id'],source_id=source['id'],status='queued'))
        conn.execute(db.sources.update().where(db.sources.c.id==source['id']).values(session_id=sid))
    return enqueue(conn,config,'local','ingest',sid,{'source_id':source['id'],'path':source['path'],'source_kind':source['source_kind'],'native_id':source['native_id']})

class Controller:
    def __init__(self,config,engine,lock,enqueue):
        self.config=config;self.engine=engine;self.lock=lock;self.enqueue=enqueue;self.stop=threading.Event();self.thread=None
    def start(self):
        def run():
            while not self.stop.wait(.8):
                try:self.tick()
                except Exception: self.stop.wait(2)
        self.thread=threading.Thread(target=run,daemon=True,name='workspace-batches');self.thread.start()
    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(5)
    def tick(self):
        with self.lock,self.engine.begin() as conn:
            if self.engine.dialect.name=='sqlite':conn.exec_driver_sql('BEGIN IMMEDIATE')
            batch=conn.execute(select(db.source_batches).where(db.source_batches.c.state.in_(ACTIVE)).order_by(db.source_batches.c.created_at).limit(1)).mappings().first()
            if not batch:return
            def change(**values):
                if values.get('state') not in (None,*ACTIVE,'paused'):values['active_slot']=None
                conn.execute(db.source_batches.update().where(db.source_batches.c.id==batch['id']).values(updated_at=db.now(),**values))
            child=conn.execute(select(db.jobs).where(db.jobs.c.id==batch['child_job_id'])).mappings().first() if batch['child_job_id'] else None
            if child and child['state'] in ('queued','running','paused'):return
            if batch['state']=='planning':
                if not child or child['state']!='succeeded':change(state='failed',error=child['error'] if child else '扫描任务丢失');return
                if (child['result_json'] or {}).get('sidebar_available') is False:change(state='failed',error='未识别到 Cursor 左栏目录；可先打开 Cursor 保存工作区，或手动选择来源');return
                scan_id=(child['result_json'] or {}).get('scan_id')
                primary=db.sources.alias('primary_source');registry=db.source_catalog.alias('primary_catalog')
                has_ide=select(primary.c.id).join(registry,registry.c.source_id==primary.c.id).where(primary.c.native_id==db.sources.c.native_id,primary.c.source_kind=='cursor_ide',registry.c.scan_id==scan_id).exists()
                earlier_jsonl=select(primary.c.id).join(registry,registry.c.source_id==primary.c.id).where(primary.c.native_id==db.sources.c.native_id,primary.c.source_kind=='cursor_jsonl',registry.c.scan_id==scan_id,primary.c.id<db.sources.c.id).exists()
                query=select(literal(batch['id'])+':'+db.sources.c.id,literal(batch['id']),db.sources.c.id,literal('pending')).join(db.source_catalog,db.source_catalog.c.source_id==db.sources.c.id).where(db.sources.c.owner_id=='local',db.source_catalog.c.scan_id==scan_id,db.source_catalog.c.in_sidebar.is_(True),db.source_catalog.c.is_subagent.is_(False),db.sources.c.project!='',or_(db.sources.c.source_kind=='cursor_ide',and_(db.sources.c.source_kind=='cursor_jsonl',~has_ide,~earlier_jsonl)))
                if batch['project'] is not None:query=query.where(db.sources.c.project==batch['project'])
                else:query=query.where(db.source_catalog.c.named.is_(True))
                conn.execute(db.batch_items.insert().from_select(['id','batch_id','source_id','state'],query))
                total=conn.execute(select(func.count()).select_from(db.batch_items).where(db.batch_items.c.batch_id==batch['id'])).scalar_one()
                change(state='active' if total else 'succeeded',total=total,child_job_id=None,error=None);return
            if child:
                if child['state']=='succeeded' and batch['state']=='indexing' and batch['destination']=='server':
                    saved=batch['remote_json'];sid=child['session_id'];sync_id=db.new_id();source=conn.execute(select(db.sessions).where(db.sessions.c.id==sid)).mappings().one()
                    job=self.enqueue(conn,self.config,'local','sync',sid,{'session_id':sid,'revision_id':source['current_revision'],'server_url':saved['url'],'device_id':saved['device_id'],'remote_user_id':saved['user_id'],'sync_id':sync_id,'excluded_asset_ids':[]})
                    conn.execute(db.syncs.insert().values(id=sync_id,owner_id='local',device_id=saved['device_id'],session_id=sid,job_id=job['id'],state='queued'))
                    conn.execute(db.sessions.update().where(db.sessions.c.id==sid).values(sync_status='pending'))
                    conn.execute(db.batch_items.update().where(db.batch_items.c.id==batch['item_id']).values(job_id=job['id']))
                    change(state='syncing',child_job_id=job['id']);return
                failed=child['state']!='succeeded'
                conn.execute(db.batch_items.update().where(db.batch_items.c.id==batch['item_id']).values(state='failed' if failed else 'succeeded',error=child['error'] if failed else None))
                change(state='active',child_job_id=None,item_id=None,completed=batch['completed']+1,failed=batch['failed']+int(failed));return
            item=conn.execute(select(db.batch_items).where(db.batch_items.c.batch_id==batch['id'],db.batch_items.c.state=='pending').order_by(db.batch_items.c.id).limit(1)).mappings().first()
            if not item:change(state='completed_with_errors' if batch['failed'] else 'succeeded');return
            if conn.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state.in_(('queued','running','paused')))).scalar_one()>=self.config.max_user_queue:return
            previous=conn.execute(select(db.jobs).where(db.jobs.c.id==item['job_id'])).mappings().first() if item['job_id'] else None
            if previous and previous['state']=='failed':
                conn.execute(db.jobs.update().where(db.jobs.c.id==previous['id']).values(state='queued',cancel_requested=False,lease_until=None,error=None))
                conn.execute(db.batch_items.update().where(db.batch_items.c.id==item['id']).values(state='processing',error=None))
                change(state='syncing' if previous['kind']=='sync' else 'indexing',item_id=item['id'],child_job_id=previous['id']);return
            source=conn.execute(select(db.sources).where(db.sources.c.id==item['source_id'])).mappings().first()
            if not source:
                conn.execute(db.batch_items.update().where(db.batch_items.c.id==item['id']).values(state='failed',error='来源不存在'));change(completed=batch['completed']+1,failed=batch['failed']+1);return
            job=queue_index(conn,self.config,source,self.enqueue)
            conn.execute(db.batch_items.update().where(db.batch_items.c.id==item['id']).values(state='processing',job_id=job['id']))
            change(state='indexing',item_id=item['id'],child_job_id=job['id'])

class Start(BaseModel):
    project:str|None=Field(default=None,max_length=32768)
    destination:str='local'
class Action(BaseModel):
    action:str

def router(config,engine,require_user,local_only,controller):
    api=APIRouter(prefix='/source-batches')
    def public(conn,row):
        item={k:v for k,v in row.items() if k!='remote_json'}
        source=conn.execute(select(db.sources.c.title).join(db.batch_items,db.batch_items.c.source_id==db.sources.c.id).where(db.batch_items.c.id==row['item_id'])).scalar() if row['item_id'] else None
        item['current_title']=source;return item
    @api.get('')
    def listing(user=Depends(require_user)):
        local_only()
        with engine.connect() as conn:return {'items':[public(conn,r) for r in conn.execute(select(db.source_batches).order_by(db.source_batches.c.created_at.desc()).limit(20)).mappings()]}
    @api.post('')
    def start(body:Start,user=Depends(require_user)):
        local_only()
        if body.destination not in ('local','server'):raise HTTPException(422,'无效同步目标')
        remote={}
        if body.destination=='server':
            from .remote import _credentials,_read_config,_checked
            import httpx
            try:
                url,headers=_credentials(config)
                with httpx.Client(timeout=10) as client:me=_checked(client.get(url+'/api/v1/auth/me',headers=headers))
                remote={'url':url,'device_id':_read_config(config).get('device_id') or db.new_id(),'user_id':(me.get('user') or me)['id']}
            except (ValueError,RuntimeError):raise HTTPException(401,'请先设置并登录团队服务器')
            except httpx.HTTPError:raise HTTPException(503,'无法连接团队服务器，请检查连接后重试')
        with controller.lock,engine.begin() as conn:
            if conn.execute(select(db.source_batches.c.id).where(db.source_batches.c.state.in_((*ACTIVE,'paused')))).first():raise HTTPException(409,'已有工作区批次，请在批次进度中完成或取消后重试')
            scan=conn.execute(select(db.jobs).where(db.jobs.c.kind=='scan',db.jobs.c.state.in_(('queued','running')))).mappings().first() or controller.enqueue(conn,config,'local','scan')
            ident=db.new_id()
            try:conn.execute(db.source_batches.insert().values(id=ident,active_slot=1,project=body.project,destination=body.destination,state='planning',scan_job_id=scan['id'],child_job_id=scan['id'],remote_json=remote))
            except IntegrityError:raise HTTPException(409,'已有工作区批次正在处理')
            return {'id':ident,'state':'planning'}
    @api.get('/{ident}/items')
    def items(ident:str,cursor:str='',user=Depends(require_user)):
        local_only()
        with engine.connect() as conn:
            rows=conn.execute(select(db.batch_items,db.sources.c.title,db.sources.c.project).outerjoin(db.sources,db.sources.c.id==db.batch_items.c.source_id).where(db.batch_items.c.batch_id==ident,db.batch_items.c.id>cursor).order_by(db.batch_items.c.id).limit(51)).mappings().all()
            return {'items':[dict(r) for r in rows[:50]],'next_cursor':rows[49]['id'] if len(rows)>50 else None}
    @api.patch('/{ident}')
    def control(ident:str,body:Action,user=Depends(require_user)):
        local_only()
        if body.action not in ('pause','resume','cancel','retry'):raise HTTPException(422,'无效操作')
        with controller.lock,engine.begin() as conn:
            batch=conn.execute(select(db.source_batches).where(db.source_batches.c.id==ident)).mappings().first()
            if not batch:raise HTTPException(404,'批次不存在')
            child=conn.execute(select(db.jobs).where(db.jobs.c.id==batch['child_job_id'])).mappings().first() if batch['child_job_id'] else None
            values={}
            if body.action in ('pause','cancel'):
                if batch['state'] not in (*ACTIVE,'paused'):raise HTTPException(409,'批次已经结束')
                values['state']='paused' if body.action=='pause' else 'cancelled'
                if child and child['state'] in ('queued','running','paused'):conn.execute(db.jobs.update().where(db.jobs.c.id==child['id']).values(state=values['state'],cancel_requested=body.action=='cancel'))
            else:
                if conn.execute(select(db.source_batches.c.id).where(db.source_batches.c.id!=ident,db.source_batches.c.state.in_((*ACTIVE,'paused')))).first():raise HTTPException(409,'请先完成当前批次')
                if body.action=='resume' and batch['state']=='paused':
                    values['state']={'scan':'planning','ingest':'indexing','sync':'syncing'}.get(child['kind'] if child else '', 'active')
                    if child and child['state']=='paused':conn.execute(db.jobs.update().where(db.jobs.c.id==child['id']).values(state='queued',cancel_requested=False,lease_until=None))
                elif body.action=='retry' and batch['state']=='completed_with_errors':
                    conn.execute(db.batch_items.update().where(db.batch_items.c.batch_id==ident,db.batch_items.c.state=='failed').values(state='pending',error=None));values={'state':'active','failed':0,'completed':batch['completed']-batch['failed']}
                elif body.action=='retry' and batch['state']=='failed' and child:
                    conn.execute(db.jobs.update().where(db.jobs.c.id==child['id']).values(state='queued',cancel_requested=False,lease_until=None,error=None));values={'state':'planning','error':None}
                else:raise HTTPException(409,'当前批次不能执行此操作')
            values['active_slot']=1 if values['state'] in (*ACTIVE,'paused') else None
            try:conn.execute(db.source_batches.update().where(db.source_batches.c.id==ident).values(updated_at=db.now(),**values))
            except IntegrityError:raise HTTPException(409,'请先完成当前批次')
        return {'ok':True}
    return api
