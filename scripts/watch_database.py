import os,subprocess,time,signal
from pathlib import Path
state=Path.home()/".local/share/ecom-agent"
ctl=state/"runtime/postgres/bin/pg_ctl"
stop=False
def quit(*_):
    global stop
    stop=True
signal.signal(signal.SIGTERM,quit);signal.signal(signal.SIGINT,quit)
while not stop:
    env={**os.environ,"LD_LIBRARY_PATH":str(state/"runtime/postgres/lib")}
    status=subprocess.run([str(ctl),"-D",str(state/"postgres"),"status"],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if status.returncode:
        subprocess.run([str(ctl),"-D",str(state/"postgres"),"-l",str(state/"postgres.log"),"start","-w"],stdout=subprocess.DEVNULL)
    time.sleep(5)
