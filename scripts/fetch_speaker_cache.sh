#!/bin/bash
# 从运行中的 voicevox_engine 拉取 speaker_info（含头像 base64）并写入 src/voice/speaker_info_bundled.json
# 需先启动: docker run --rm -p 127.0.0.1:50021:50021 voicevox/voicevox_engine:cpu-latest

set -e
cd "$(dirname "$0")/.."
BASE="http://127.0.0.1:50021"

echo "正在连接 voicevox_engine ($BASE)..."
if ! curl -sf --connect-timeout 3 "$BASE/speakers" > /dev/null; then
  echo "错误: 无法连接 voicevox_engine"
  echo "请先启动: docker run --rm -p 127.0.0.1:50021:50021 voicevox/voicevox_engine:cpu-latest"
  exit 1
fi

python3 - "$BASE" << 'PY'
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

base = sys.argv[1]
print("获取 /speakers...")
req = urllib.request.Request(f"{base}/speakers", method="GET")
with urllib.request.urlopen(req, timeout=10) as r:
    speakers = json.loads(r.read().decode())

cache = {}
seen = set()
for sp in speakers:
    uuid_val = sp.get("speaker_uuid") or sp.get("uuid")
    if not uuid_val:
        continue
    uuid_str = str(uuid_val)
    if uuid_str in seen:
        continue
    seen.add(uuid_str)
    name = sp.get("name", "?")
    print(f"  下载 {name}...", end=" ", flush=True)
    url = f"{base}/speaker_info?speaker_uuid={urllib.parse.quote(uuid_str)}&resource_format=base64"
    try:
        req2 = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req2, timeout=15) as r2:
            cache[uuid_str] = json.loads(r2.read().decode())
        print("OK")
    except Exception as e:
        print(f"跳过: {e}")

if not cache:
    print("未获取到任何数据")
    sys.exit(1)

out = __import__("pathlib").Path("src/voice/speaker_info_bundled.json")
out.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已写入 {out} ({len(cache)} 个角色)")
PY

echo "完成"
