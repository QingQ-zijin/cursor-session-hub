"""CLI entry point for servers, workers, and the packaged desktop sidecar."""
import argparse
import getpass
import json
import os
import secrets
import socket
import sys
from .config import Config

def main():
    if len(sys.argv)>1 and sys.argv[1] in ('--worker','--worker-job'):
        config=Config()
        if sys.argv[1]=='--worker':
            from .worker import run_forever
            run_forever(config)
        else:
            if len(sys.argv)!=3: raise SystemExit('--worker-job requires a job ID')
            from .worker import execute_job
            execute_job(config,sys.argv[2])
        return
    # Frozen desktop sidecar passes serve flags directly.
    if len(sys.argv)>1 and sys.argv[1].startswith('--') and sys.argv[1] not in ('--help','--worker','--worker-job'):
        sys.argv.insert(1,'serve')
    parser=argparse.ArgumentParser(prog='cursor-session-hub')
    sub=parser.add_subparsers(dest='command')
    serve=sub.add_parser('serve')
    serve.add_argument('--host',default=None)
    serve.add_argument('--port',type=int,default=0)
    serve.add_argument('--mode',choices=['local','cloud'])
    serve.add_argument('--data-dir')
    sub.add_parser('worker')
    init=sub.add_parser('bootstrap-admin')
    init.add_argument('--username',default='admin')
    init.add_argument('--display-name',default='管理员')
    init.add_argument('--password-env')
    sub.add_parser('migrate')
    args=parser.parse_args()
    config=Config()
    if getattr(args,'data_dir',None):
        from pathlib import Path
        config.home=Path(args.data_dir).expanduser().resolve()
        if not os.getenv('CSH_DATABASE_URL'): config.database_url='sqlite:///'+(config.home/'hub.db').as_posix()
    if args.command=='worker':
        from .worker import run_forever
        run_forever(config);return
    if args.command in ('bootstrap-admin','migrate'):
        from . import db,auth
        engine=db.get_engine(config);db.init_db(engine)
        if args.command=='bootstrap-admin':
            password=os.getenv(args.password_env,'') if args.password_env else getpass.getpass('管理员密码（至少 12 位）：')
            try: auth.bootstrap_admin(engine,args.username,args.display_name,password)
            except ValueError as exc: parser.error(str(exc))
            print('管理员初始化完成。')
        else: print('数据库结构已更新。')
        engine.dispose();return
    if args.command not in (None,'serve'): parser.error('未知命令')
    if getattr(args,'mode',None): config.mode=args.mode
    if config.mode=='local' and not config.local_token: config.local_token=secrets.token_urlsafe(48)
    host=getattr(args,'host',None) or ('127.0.0.1' if config.mode=='local' else '0.0.0.0')
    if config.mode=='local' and host not in ('127.0.0.1','localhost','::1'): parser.error('本地模式只能监听回环地址')
    port=getattr(args,'port',0)
    sock=socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET,socket.SOCK_STREAM)
    if os.name!='nt': sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind((host,port));sock.listen(128)
    from .api import create_app
    app=create_app(config)
    print('CSH_READY '+json.dumps({'port':sock.getsockname()[1]}),flush=True)
    if config.mode=='local' and not os.getenv('CSH_LOCAL_TOKEN'):
        print('浏览器地址：http://127.0.0.1:'+str(sock.getsockname()[1])+'/?token='+config.local_token,file=sys.stderr)
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(app,host=host,port=port,log_level='info'))
    import threading
    stopped=threading.Event()
    parent_pid=os.getenv('CSH_NATIVE_PARENT_PID')
    if config.mode=='local' and parent_pid:
        import psutil
        try:
            parent=psutil.Process(int(parent_pid))
            born=parent.create_time()
        except (ValueError,psutil.Error):
            sock.close()
            raise SystemExit('Native parent process is unavailable')
        def watch_parent():
            while not stopped.wait(2):
                try:
                    alive=parent.is_running() and parent.create_time()==born and parent.status()!=psutil.STATUS_ZOMBIE
                except psutil.Error:
                    alive=False
                if not alive:
                    server.should_exit=True
                    return
        threading.Thread(target=watch_parent,daemon=True,name='csh-parent-watchdog').start()
    try:
        server.run(sockets=[sock])
    finally:
        stopped.set()
        sock.close()

if __name__=='__main__': main()
