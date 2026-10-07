from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
state=Path.home()/".local/share/ecom-v3"
state.mkdir(parents=True,exist_ok=True,mode=0o700)
python=ROOT/".venv-v3/bin/python"
parts=[f"""[unix_http_server]
file={state}/supervisor.sock
chmod=0600
[supervisord]
logfile={state}/supervisor.log
pidfile={state}/supervisor.pid
childlogdir={state}
umask=0077
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://{state}/supervisor.sock
[program:database-guard]
command={python} {ROOT}/scripts/watch_database.py
directory={ROOT}
priority=10
autostart=true
autorestart=true
stopasgroup=true
killasgroup=true
stdout_logfile={state}/database-guard.log
redirect_stderr=true
"""]
for mode,port in (("demo",18501),("production",18080)):
 for kind in ("worker","api"):
  name=kind+"-"+mode
  command=f"{python} -m ecom_copilot.enterprise.worker" if kind=="worker" else f"{python} -m uvicorn ecom_copilot.enterprise.api:app --host 127.0.0.1 --port {port} --no-access-log"
  parts.append(f"""[program:{name}]
command={command}
directory={ROOT}
environment=PYTHONPATH="{ROOT}/src",ECOM_V3_CONFIG="{ROOT}/.env.v3-{mode}"
priority={20 if kind=="worker" else 30}
autostart=true
autorestart=true
startsecs=3
stopwaitsecs=60
stopasgroup=true
killasgroup=true
stdout_logfile={state}/{name}.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
redirect_stderr=true
""")
path=state/"supervisor.conf";path.write_text("\n".join(parts));path.chmod(0o600)
print("私有进程监督配置已生成")
