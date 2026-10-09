from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
state=Path.home()/".local/share/ecom-agent"
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

def env_file_values(name):
  """读取部署环境文件为字符串字典；用于取中间件凭据，避免在配置里写死。"""
  values={};path=ROOT/name
  if not path.is_file():return values
  for line in path.read_text().splitlines():
    line=line.strip()
    if not line or line.startswith("#") or "=" not in line:continue
    key,_,val=line.partition("=")
    values[key.strip()]=val.strip().strip('"')
  return values

def middleware_programs(state):
  """按已安装的中间件版本生成 Supervisor 程序段；未安装的部件自动跳过。"""
  mw=state/"middleware";result=[]
  def newest(pattern):
    """按版本号选最新的中间件目录，避免多版本共存时误选旧版。"""
    def key(path):
      try:return [int(part) for part in path.name.split("-",1)[1].split(".")]
      except ValueError:return [0]
    dirs=sorted(mw.glob(pattern),key=key)
    return dirs[-1] if dirs else None
  es_dir=newest("elasticsearch-*");kafka_dir=newest("kafka_*")
  java_home=(es_dir/"jdk") if es_dir else None
  if (mw/"minio").is_file():
    creds=env_file_values(".env.demo")
    result.append(f"""[program:minio]
command={mw}/minio server {state}/minio-data --address 127.0.0.1:19000 --console-address 127.0.0.1:19001
directory={mw}
environment=MINIO_ROOT_USER="{creds.get('ECOM_MINIO_ACCESS_KEY','')}",MINIO_ROOT_PASSWORD="{creds.get('ECOM_MINIO_SECRET_KEY','')}"
priority=4
autostart=true
autorestart=true
startsecs=3
stopwaitsecs=20
stopasgroup=true
killasgroup=true
stdout_logfile={state}/minio.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
redirect_stderr=true
""")
  if kafka_dir and java_home:
    result.append(f"""[program:kafka]
command={kafka_dir}/bin/kafka-server-start.sh {state}/kafka/broker.properties
directory={kafka_dir}
environment=JAVA_HOME="{java_home}",PATH="{java_home}/bin:%(ENV_PATH)s"
priority=5
autostart=true
autorestart=true
startsecs=10
stopwaitsecs=40
stopasgroup=true
killasgroup=true
stdout_logfile={state}/kafka.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
redirect_stderr=true
""")
  if es_dir:
    result.append(f"""[program:elasticsearch]
command={es_dir}/bin/elasticsearch
directory={es_dir}
environment=ES_JAVA_OPTS="-Xms1g -Xmx1g"
priority=6
autostart=true
autorestart=true
startsecs=30
stopwaitsecs=40
stopasgroup=true
killasgroup=true
stdout_logfile={state}/elasticsearch.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=3
redirect_stderr=true
""")
  if not result:print("未发现中间件，跳过 MinIO/Kafka/Elasticsearch 程序段")
  return result

parts.extend(middleware_programs(state))
path=state/"supervisor.conf";path.write_text("\n".join(parts));path.chmod(0o600)
print("私有进程监督配置已生成")
