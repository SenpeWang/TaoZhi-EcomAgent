"""私有 PostgreSQL 备份与隔离库恢复演练；密码只经环境传入客户端。"""
import os,sys,subprocess,json,datetime,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"src"))
from ecom_copilot.enterprise.config import load_config
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
STATE=Path.home()/".local/share/ecom-v3"
BIN=STATE/"runtime/postgres/bin"
def client_env(database=None):
    parts=conninfo_to_dict(load_config().dsn)
    return {**os.environ,"PGHOST":parts["host"],"PGPORT":parts["port"],"PGUSER":parts["user"],"PGPASSWORD":parts["password"],"PGDATABASE":database or parts["dbname"]}
def create_database_backup():
    dest=Path(os.environ.get("ECOM_BACKUP_DIR",ROOT/"data/backups"))/(load_config().mode+"_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    dest.mkdir(parents=True,mode=0o700)
    path=dest/(load_config().mode+".dump")
    subprocess.run([str(BIN/"pg_dump"),"-Fc","--no-owner","--no-privileges","-f",str(path)],env=client_env(),check=True)
    path.chmod(0o600)
    # Original attachments need an independent archive; database references are preserved.
    import tarfile
    with tarfile.open(dest/"private-files.tar.gz","w:gz") as archive:archive.add(load_config().private_dir,arcname="private")
    (dest/"private-files.tar.gz").chmod(0o600)
    manifest={"database_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"attachment_sha256":hashlib.sha256((dest/"private-files.tar.gz").read_bytes()).hexdigest(),"mode":load_config().mode}
    (dest/"manifest.json").write_text(json.dumps(manifest));(dest/"manifest.json").chmod(0o600)
    return path
def signatures(dsn):
    result={}
    with psycopg.connect(dsn) as c:
        for table in ("tenants","users","org_nodes","memberships","documents","document_versions","chunks","document_grants","approvals","jobs","legacy_archive"):
            rows=c.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(sql.Identifier(table))).fetchall()
            result[table]=hashlib.sha256("\n".join(row[0] for row in rows).encode()).hexdigest()
    return result
def verify_restore():
    path=create_database_backup();parts=conninfo_to_dict(load_config().dsn)
    target="ecom_v3_restore_"+datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    with psycopg.connect(host=str(STATE/"socket"),port=15432,dbname="postgres",autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(target),sql.Identifier(parts["user"])))
        admin.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(sql.Identifier(target)))
        try:
            subprocess.run([str(BIN/"pg_restore"),"--no-owner","--no-privileges","--exit-on-error","--dbname",target,str(path)],env=client_env(target),check=True)
            restored=load_config().dsn.rsplit("/",1)[0]+"/"+target
            assert signatures(load_config().dsn)==signatures(restored),"恢复数据校验不一致"
            # 在独立目录恢复附件，并逐个验证字节；不覆盖正在使用的私有目录。
            import tempfile,tarfile
            with tempfile.TemporaryDirectory(prefix="restore-files-",dir=STATE) as temporary:
                folder=Path(temporary);count=0
                with tarfile.open(path.parent/"private-files.tar.gz","r:gz") as archive:
                    for item in archive:
                        relative=Path(item.name)
                        if relative.is_absolute() or ".." in relative.parts or item.issym() or item.islnk():
                            raise RuntimeError("备份附件路径校验失败")
                        target_file=folder/relative
                        if item.isdir():target_file.mkdir(parents=True,exist_ok=True,mode=0o700)
                        elif item.isfile():
                            target_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                            original=archive.extractfile(item).read()
                            target_file.write_bytes(original);target_file.chmod(0o600)
                            assert hashlib.sha256(target_file.read_bytes()).digest()==hashlib.sha256(original).digest()
                            count+=1
                print("私有附件隔离恢复校验通过，文件数",count)
            print("隔离恢复演练通过；账号、文档版本、权限、任务及档案校验一致")
            print("备份目录",path.parent)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(target)))
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="显式创建数据库备份或运行隔离恢复验收")
    parser.add_argument("command", choices=("create", "verify"), help="create 创建备份；verify 创建备份并隔离恢复验证")
    args = parser.parse_args()
    if args.command == "verify":
        verify_restore()
    else:
        print("备份目录", create_database_backup().parent)
