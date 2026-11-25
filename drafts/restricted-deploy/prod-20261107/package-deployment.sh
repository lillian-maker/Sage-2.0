#!/bin/sh
# 打包生产部署候选（结构对齐 cfb411f release：candidate.json + sha256-manifest + 镜像 tar + drafts）
# 用法: package-deployment.sh <anchor-sha> <输出目录>
set -eu

if [ "$#" -ne 2 ]; then
    echo "Usage: $0 <anchor-sha> <output-directory>" >&2
    exit 2
fi

SHA="$1"; OUT="$2"; ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
APP_TAG="sage-app:$SHA"; WEB_TAG="sage-web:$SHA"
CANDIDATE="$OUT/$SHA"
FINAL_BUNDLE="$OUT/sage-docker-$SHA-deployment.tar.gz"

git -C "$ROOT" cat-file -e "$SHA^{commit}"
mkdir -p "$OUT"
[ ! -e "$CANDIDATE" ] || { echo "Candidate already exists: $CANDIDATE" >&2; exit 1; }
[ ! -e "$FINAL_BUNDLE" ] || { echo "Deployment bundle already exists: $FINAL_BUNDLE" >&2; exit 1; }
TMP_CANDIDATE=$(mktemp -d "$OUT/.${SHA}.XXXXXX")
RAW_ARCHIVE="$TMP_CANDIDATE/images-linux-amd64.tar"
IMAGE_ARCHIVE="$TMP_CANDIDATE/images-linux-amd64.tar.gz"
TMP_IMAGE_ARCHIVE="$TMP_CANDIDATE/.images-linux-amd64.tar.gz.tmp"
TMP_BUNDLE="$OUT/.sage-docker-$SHA-deployment.tar.gz.tmp"
CID=''

cleanup() {
    [ -z "$CID" ] || docker rm "$CID" >/dev/null 2>&1 || true
    rm -rf "$TMP_CANDIDATE"
}
trap cleanup EXIT HUP INT TERM
mkdir -p "$TMP_CANDIDATE/drafts/restricted-deploy"

echo "== 导出镜像 =="
APP_PLATFORM=$(docker image inspect "$APP_TAG" --format '{{.Os}}/{{.Architecture}}')
WEB_PLATFORM=$(docker image inspect "$WEB_TAG" --format '{{.Os}}/{{.Architecture}}')
[ "$APP_PLATFORM" = linux/amd64 ] || { echo "Unexpected app image platform: $APP_PLATFORM" >&2; exit 1; }
[ "$WEB_PLATFORM" = linux/amd64 ] || { echo "Unexpected web image platform: $WEB_PLATFORM" >&2; exit 1; }
docker save -o "$RAW_ARCHIVE" "$APP_TAG" "$WEB_TAG"
gzip -c "$RAW_ARCHIVE" > "$TMP_IMAGE_ARCHIVE"
mv "$TMP_IMAGE_ARCHIVE" "$IMAGE_ARCHIVE"
gzip -t "$IMAGE_ARCHIVE"
rm "$RAW_ARCHIVE"

echo "== 提取镜像内首页哈希 =="
CID=$(docker create "$WEB_TAG")
docker cp "$CID":/srv/sage/dist/index.html "$TMP_CANDIDATE/index.html" >/dev/null
HOMEPAGE=$(shasum -a 256 "$TMP_CANDIDATE/index.html" | cut -d' ' -f1)
docker rm "$CID" >/dev/null
CID=''
rm "$TMP_CANDIDATE/index.html"

APP_ID=$(docker image inspect "$APP_TAG" --format '{{.Id}}')
WEB_ID=$(docker image inspect "$WEB_TAG" --format '{{.Id}}')
ARCH_SHA=$(shasum -a 256 "$IMAGE_ARCHIVE" | cut -d' ' -f1)
ARCH_BYTES=$(wc -c < "$IMAGE_ARCHIVE" | tr -d ' ')

echo "== 复制部署文件 =="
for f in compose.yaml compose.offline.yaml compose.egress.yaml compose.auth.yaml compose.alipay.yaml compose.sales.yaml docker-init.sql; do
    git -C "$ROOT" show "$SHA:drafts/restricted-deploy/$f" > "$TMP_CANDIDATE/drafts/restricted-deploy/$f"
done
git -C "$ROOT" archive --format=tar "$SHA" drafts/restricted-deploy/prod-20261107 | tar -xf - -C "$TMP_CANDIDATE"

echo "== candidate.json =="
cat > "$TMP_CANDIDATE/candidate.json" <<EOF
{
  "source_commit": "$SHA",
  "source_method": "Git commit archive, deployment inputs listed in sha256-manifest",
  "images": {
    "app":  {"image_id": "$APP_ID", "tag": "$APP_TAG", "platform": "linux/amd64"},
    "web":  {"image_id": "$WEB_ID", "tag": "$WEB_TAG", "platform": "linux/amd64"}
  },
  "homepage_sha256": "$HOMEPAGE",
  "images_archive_sha256": "$ARCH_SHA",
  "images_archive_bytes": $ARCH_BYTES,
  "postgres_image_id_unchanged": "sha256:ec9f656f17c8b4dfebf49e58cf5c46d493d4846d4ea6d8b85d0637f68ee33bfc",
  "offline_override": {"file": "drafts/restricted-deploy/compose.offline.yaml", "pins_db_image_id": true},
  "platform": "linux/amd64"
}
EOF

echo "== sha256-manifest.json =="
(cd "$TMP_CANDIDATE" && find candidate.json images-linux-amd64.tar.gz drafts/restricted-deploy -type f -print | \
    LC_ALL=C sort | xargs shasum -a 256 > sha256-manifest.json)
cat "$TMP_CANDIDATE/sha256-manifest.json"

echo "== 外层包 =="
# macOS tar 默认附带 ._* 扩展属性文件，解包后会混入 release 目录
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$TMP_BUNDLE" -C "$TMP_CANDIDATE" .
mv "$TMP_BUNDLE" "$FINAL_BUNDLE"
mv "$TMP_CANDIDATE" "$CANDIDATE"
TMP_CANDIDATE=''
shasum -a 256 "$FINAL_BUNDLE"
echo "DONE: $FINAL_BUNDLE"
