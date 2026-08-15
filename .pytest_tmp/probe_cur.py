"""检查 8005 当前服务的 index.html 与 JS bundle。"""
import re
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8005/chat/master-agent", timeout=10) as r:
    html = r.read().decode("utf-8")
print("JS refs:", re.findall(r'src="([^"]+\.js)"', html))
js = re.findall(r'src="([^"]+\.js)"', html)
if js:
    body = urllib.request.urlopen("http://127.0.0.1:8005" + js[0], timeout=15).read()
    print("bundle bytes:", len(body))
    for key in [b"borderLeft", b"write_todos", b"pending_approval", b"clearPrevApproval"]:
        print(key.decode(), "in bundle:", key in body)
