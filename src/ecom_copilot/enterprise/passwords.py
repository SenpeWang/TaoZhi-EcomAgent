import hashlib,hmac,secrets,threading
from .policy import Denied
_slots=threading.BoundedSemaphore(4)
def hash_password(value,mode):
    if not isinstance(value,str) or not (6 if mode in ('demo','test') else 12)<=len(value)<=128:
        raise Denied("演示密码须至少 6 位；正式密码须至少 12 位","INVALID_PASSWORD",400)
    if mode=='production' and value in ('123456','123456789012','password123456','admin12345678'):
        raise Denied("正式环境不能使用演示或常见密码","INVALID_PASSWORD",400)
    salt=secrets.token_bytes(16)
    with _slots:
        hash_token=hashlib.scrypt(value.encode(),salt=salt,n=16384,r=8,p=5,dklen=32,maxmem=64*1024*1024).hex()
    return 'scrypt$16384$8$5$'+salt.hex()+'$'+hash_token
def verify_password(value,stored):
    try:
        parts=stored.split('$')
        if len(parts)==3:scheme,salt,expected=parts;p=1
        elif len(parts)==6:
            scheme,n,r,p,salt,expected=parts
            if (n,r,p)!=('16384','8','5'):return False
            p=5
        else:return False
        if scheme!='scrypt' or len(salt)!=32 or len(expected)!=64 or len(value)>128:return False
        with _slots:
            actual=hashlib.scrypt(value.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=p,dklen=32,maxmem=64*1024*1024).hex()
        return hmac.compare_digest(actual,expected)
    except (ValueError,TypeError):return False
