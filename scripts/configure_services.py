from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
state=Path.home()/".local/share/ecom-v3"
state.mkdir(parents=True,exist_ok=True,mode=0o700)
python=ROOT/".venv/bin/python"
def find_sp_llm_python():
    for candidate in (Path.home()/"huguoqiang/anaconda3/envs/sp_llm/bin/python",
                      Path.home()/"anaconda3/envs/sp_llm/bin/python",
                      Path.home()/"miniconda3/envs/sp_llm/bin/python"):
        if candidate.is_file():return candidate
    return None
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
environment=PYTHONPATH="{ROOT}/src",ECOM_ENV_FILE="{ROOT}/.env.{mode}"
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
sp_llm=find_sp_llm_python()
if sp_llm:
  parts.append(f"""[program:embedding]
command={sp_llm} {ROOT}/scripts/embedding_server.py
directory={ROOT}
environment=EMBED_MODEL_PATH="{ROOT.parent}/models/bge-small-zh-v1.5",EMBED_PORT="18555",EMBED_DEVICE="cpu"
priority=15
autostart=true
autorestart=true
startsecs=20
stopwaitsecs=30
stopasgroup=true
killasgroup=true
stdout_logfile={state}/embedding.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
redirect_stderr=true
""")
else:
  print("未找到 sp_llm 环境，跳过语义嵌入服务；检索将自动降级为词法两路")
path=state/"supervisor.conf";path.write_text("\n".join(parts));path.chmod(0o600)
print("私有进程监督配置已生成")
