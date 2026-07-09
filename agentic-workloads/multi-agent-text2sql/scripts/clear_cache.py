"""캐시 비우기

ElastiCache Valkey의 Semantic Cache 데이터를 모두 삭제합니다.
"""

import os
import valkey

host = os.environ.get("VALKEY_ENDPOINT", "localhost")
port = int(os.environ.get("VALKEY_PORT", "6379"))

r = valkey.Valkey(host=host, port=port)
r.flushdb()
print("✅ 캐시를 비웠습니다")
