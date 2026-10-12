#!/usr/bin/env bash
# PLG110 TWRP 16.0 构建脚本（必须在 Linux x86_64 上运行）
#
#   ./tools/build_twrp.sh /path/to/twrp-16.0-src
#
# 前置：
#   - 已跑过 tools/extract_stock.py，且 device/oppo/PLG110/local-inputs/ 已就绪
#   - 依赖: repo git curl openjdk-17 python3 rsync (AOSP 官方要求的包)
set -euo pipefail

SRC_ROOT="${1:?用法: build_twrp.sh <twrp-16.0 源码目录>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEVICE_DIR="device/oppo/PLG110"
MANIFEST_URL="${TWRP_MANIFEST:-https://github.com/TWRP-Test/platform_manifest_twrp_aosp.git}"
MANIFEST_BRANCH="${TWRP_BRANCH:-twrp-16.0}"
JOBS="${JOBS:-$(nproc)}"

[ "$(uname -s)" = "Linux" ] || {
    echo "错误: AOSP 无法在 $(uname -s) 上编译，请用 Linux/容器/CI" >&2; exit 1; }

# ---------------------------------------------------------------- 1. 同步源码
mkdir -p "$SRC_ROOT" && cd "$SRC_ROOT"
if [ ! -d .repo ]; then
    echo "==> repo init ($MANIFEST_URL / $MANIFEST_BRANCH)"
    repo init --depth=1 -u "$MANIFEST_URL" -b "$MANIFEST_BRANCH"
fi
# 设备树默认由下面的 rsync 注入；只有把 local_manifest.xml 里的 remote
# 指向了真实仓库时才需要用 repo 管理，此时设 USE_LOCAL_MANIFEST=1。
if [ "${USE_LOCAL_MANIFEST:-0}" = "1" ]; then
    mkdir -p .repo/local_manifests
    cp "$REPO_ROOT/local_manifest.xml" .repo/local_manifests/PLG110.xml
fi
echo "==> repo sync -j$JOBS"
repo sync -c -j"$JOBS" --force-sync --no-clone-bundle --no-tags

# ------------------------------------------------------- 2. 注入设备树与输入
echo "==> 注入设备树 $DEVICE_DIR"
mkdir -p "$DEVICE_DIR"
rsync -a --delete \
    --exclude 'local-inputs' --exclude '.git' \
    "$REPO_ROOT/$DEVICE_DIR/" "$SRC_ROOT/$DEVICE_DIR/"

if [ ! -f "$REPO_ROOT/$DEVICE_DIR/local-inputs/board_config.local.mk" ]; then
    echo "错误: 缺少 $DEVICE_DIR/local-inputs/board_config.local.mk" >&2
    echo "      请先执行: python3 tools/extract_stock.py --firmware-dir <固件目录>" >&2
    exit 1
fi
mkdir -p "$SRC_ROOT/$DEVICE_DIR/local-inputs"
cp "$REPO_ROOT/$DEVICE_DIR/local-inputs/"* "$SRC_ROOT/$DEVICE_DIR/local-inputs/"

# ------------------------------------------------------------------ 3. 编译
echo "==> 编译 recovery"
set +u
source build/envsetup.sh
lunch twrp_PLG110-bp2a-eng
set -u

mka recovery vendorbootimage -j"$JOBS" 2>&1 | tail -40

OUT="out/target/product/PLG110"
[ -f "$OUT/vendor_boot.img" ] || { echo "错误: 未生成 vendor_boot.img" >&2; exit 1; }

# ------------------------------------------------- 4. 与官方 vendor_boot 合并
STOCK="${STOCK_VENDOR_BOOT:-$REPO_ROOT/$DEVICE_DIR/local-inputs/stock_vendor_boot.img}"
if [ -f "$STOCK" ]; then
    echo "==> 合并到官方 vendor_boot"
    python3 "$REPO_ROOT/tools/repack_vendor_boot.py" \
        --stock "$STOCK" --twrp "$OUT/vendor_boot.img" \
        --out "$OUT/PLG110_twrp_vendor_boot.img"
    FINAL="$OUT/PLG110_twrp_vendor_boot.img"
else
    echo "!! 未找到官方 vendor_boot（$STOCK），跳过合并；"
    echo "   直接用 TWRP 产物可能缺少官方 vendor ramdisk 内的内核模块。"
    FINAL="$OUT/vendor_boot.img"
fi

echo
echo "==== 产物 ===="
ls -lh "$FINAL"
echo
echo "刷入（BL 已解锁）:"
echo "  fastboot flash vendor_boot $FINAL"
echo "  fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img"
echo
echo "回滚: fastboot flash vendor_boot <官方 vendor_boot.img>"
