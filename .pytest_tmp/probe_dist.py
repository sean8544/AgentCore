"""检查 8005 服务的静态 JS 是否包含新组件特征字符串。"""
import re
import urllib.request

BASE = "http://127.0.0.1:8005"


def get(path: str) -> str:
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return r.read().decode("utf-8", errors="replace")


def main() -> None:
    html = get("/chat/master-agent")
    js_files = re.findall(r'src="([^"]+\.js)"', html)
    print("js files:", js_files)
    for jf in js_files[:6]:
        if not jf.startswith("http"):
            jf = jf if jf.startswith("/") else "/" + jf
        try:
            body = get(jf)
        except Exception as e:  # noqa: BLE001
            print(f"  {jf}: ERR {e}")
            continue
        markers = {
            "ToolCallCard(highlightJson)": "border-left",
            "todoStatusBadge": "STATUS_META",
            "delegationTimeline": "DelegationRecordPanel",
        }
        hits = [name for name, key in markers.items() if key in body]
        print(f"  {jf}: {len(body)} bytes, markers={hits or 'NONE'}")


if __name__ == "__main__":
    main()
