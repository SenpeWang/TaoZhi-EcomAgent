import subprocess,time,signal
from pathlib import Path
state=Path.home()/".local/share/ecom-v3"
ctl=state/"runtime/postgres/bin/pg_ctl"
stop=False
def quit(*_):
    global stop
    stop=True
signal.signal(signal.SIGTERM,quit);signal.signal(signal.SIGINT,quit)
while not stop:
    status=subprocess.run([str(ctl),"-D",str(state/"postgres"),"status"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if status.returncode:
        subprocess.run([str(ctl),"-D",str(state/"postgres"),"-l",str(state/"postgres.log"),"start","-w"],stdout=subprocess.DEVNULL)
    time.sleep(5)
