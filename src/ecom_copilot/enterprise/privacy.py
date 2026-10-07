"""已授权内容的个人信息最小化；外部模型始终接收脱敏文本。"""
import re
def redact(text):
    text=str(text)
    text=re.sub(r"sk-[A-Za-z0-9_-]{20,}","已隐藏的密钥",text)
    text=re.sub(r"(?<!\d)(1[3-9]\d{9})(?!\d)",lambda m:m[1][:3]+"****"+m[1][-4:],text)
    text=re.sub(r"(?<!\d)(\d{17}[0-9Xx])(?!\w)",lambda m:m[1][:4]+"**********"+m[1][-4:],text)
    text=re.sub(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})",lambda m:m[1][:1]+"***@"+m[2],text)
    text=re.sub(r"((?:收货地址|客户地址|详细地址|地址)\s*[：:])\s*[^。\n；;]{3,100}",r"\1已隐藏",text)
    return text
