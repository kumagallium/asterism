#!/bin/sh
# Phase 1+ Option B entrypoint for self-built togomcp container.
#
# 1. Oxigraph (compose service "oxigraph", port 7878) が ready になるまで待つ
# 2. **overlay** を作る: togomcp package の data/ デフォルトを /var/togomcp-overlay
#    にコピーし、その上に bind mount /data/togomcp_overlay の差分を被せる
#    (我々の mie/starrydata.yaml と resources/endpoints.csv だけを差し替え)
# 3. TOGOMCP_DIR=/var/togomcp-overlay にして togomcp 本体を起動
#
# なぜ overlay 方式か:
#   togomcp の server.py は TOGOMCP_DIR 配下に mie / resources / docs / kw_search
#   などを **全部**期待する。我々がリポジトリで持ちたいのは starrydata MIE と
#   endpoints.csv の差分だけ。その他の defaults (docs/togomcp-intro.html,
#   resources/MIE_prompt.md など) は package に同梱されているので、起動時に
#   そこからコピーして overlay を作るのが一番ストレスが少ない。

set -e

OXIGRAPH_HOST="${OXIGRAPH_HOST:-oxigraph}"
OXIGRAPH_PORT="${OXIGRAPH_PORT:-7878}"
TIMEOUT="${OXIGRAPH_WAIT_TIMEOUT:-60}"

OVERLAY_DIR="${OVERLAY_DIR:-${HOME}/togomcp-overlay}"
USER_OVERRIDES_DIR="${USER_OVERRIDES_DIR:-/data/togomcp}"

PACKAGE_DATA_DIR=$(python -c "import togo_mcp, pathlib; print(pathlib.Path(togo_mcp.__file__).parent / 'data')")

echo "[entrypoint] package data dir: ${PACKAGE_DATA_DIR}"
echo "[entrypoint] user overrides:   ${USER_OVERRIDES_DIR}"
echo "[entrypoint] overlay target:   ${OVERLAY_DIR}"

# (1) Wait for Oxigraph
echo "[entrypoint] waiting for ${OXIGRAPH_HOST}:${OXIGRAPH_PORT}..."
python -c "
import socket, sys, time
host, port, deadline = '${OXIGRAPH_HOST}', ${OXIGRAPH_PORT}, time.time() + ${TIMEOUT}
while time.time() < deadline:
    try:
        with socket.create_connection((host, port), timeout=2):
            print('[entrypoint] ' + host + ':' + str(port) + ' is open')
            sys.exit(0)
    except OSError:
        time.sleep(1)
print('[entrypoint] timeout waiting for ' + host + ':' + str(port), file=sys.stderr)
sys.exit(1)
"

# (2) Build overlay: package defaults + user overrides on top
rm -rf "${OVERLAY_DIR}"
cp -r "${PACKAGE_DATA_DIR}" "${OVERLAY_DIR}"
if [ -d "${USER_OVERRIDES_DIR}" ]; then
    # cp -RT で /data/togomcp の中身を overlay にマージ (同名ファイルは上書き)
    cp -rT "${USER_OVERRIDES_DIR}" "${OVERLAY_DIR}"
    echo "[entrypoint] overlay built; user files merged on top of package defaults"
else
    echo "[entrypoint] no user overrides at ${USER_OVERRIDES_DIR}; using package defaults"
fi

# (2.5) Merge endpoints.csv: package デフォルトには multi-DB の登録があるが、
# 我々は **starrydata 用の 1 行を末尾に追加する**運用なので、上で丸ごと上書きされる
# のは困る。USER_OVERRIDES_DIR/resources/endpoints.csv があれば、それを package
# 版に append する (header 行はスキップ)。
if [ -f "${USER_OVERRIDES_DIR}/resources/endpoints.csv" ]; then
    cp "${PACKAGE_DATA_DIR}/resources/endpoints.csv" "${OVERLAY_DIR}/resources/endpoints.csv"
    awk 'NR>1' "${USER_OVERRIDES_DIR}/resources/endpoints.csv" \
        >> "${OVERLAY_DIR}/resources/endpoints.csv"
    echo "[entrypoint] endpoints.csv merged (package defaults + user rows appended)"
fi

export TOGOMCP_DIR="${OVERLAY_DIR}"
echo "[entrypoint] TOGOMCP_DIR=${TOGOMCP_DIR}"
echo "[entrypoint] starting: $@"
exec "$@"
