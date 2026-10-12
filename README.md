# OPPO PLG110（Find X9 Pro）TWRP 移植工程

为 **OPPO Find X9 Pro（入网型号 PLG110，MediaTek MT6993 / 天玑 9500，Android 16 / ColorOS 16）**
移植 TWRP 16.0。

## 这个仓库是什么

本机是 macOS，AOSP **无法在 macOS 上编译**（Android 10 起官方不再支持）。因此本仓库的定位是
**移植套件**：设备树 + 从官方固件自动提取板级参数的工具 + 构建/合并/刷写流程，实际编译跑在
Linux 容器、Linux 服务器或 GitHub Actions 上。

```
device/oppo/PLG110/      TWRP 设备树（可直接 rsync 进 TWRP 16.0 源码树）
  BoardConfig.mk         板级配置；固件相关数值全部从 local-inputs/ 读取，不写死
  twrp_PLG110.mk         lunch 目标：twrp_PLG110-bp2a-eng
  recovery.fstab         分区表（已按官方 fstab 重建，与官方一致）
  rootdir/               recovery init 片段
  local-inputs/          由官方固件生成（不入库）：DTB、板级参数、ramdisk 片段
ota_full/                固件解包产物
  images_16/             Android 16 整包镜像  ← 当前基线
  images/                更新版固件（OTA payload, android_version=17）—— 不要与上面混用
  meta/                  payload 元数据
backup/                  官方 vendor_boot/boot/vbmeta 原件 + sha256sums.txt（回滚用）
tools/
  bootimg.py             boot/vendor_boot v0-v4 解析与重打包（纯标准库）
  extract_stock.py       从官方固件提取 DTB / 板级参数 / ramdisk，并生成 recovery.fstab
  repack_vendor_boot.py  把 TWRP 的 recovery ramdisk 合并回官方 vendor_boot
  build_twrp.sh          Linux 上的完整构建流程
  selftest.py            自检：合成的 v4 镜像 + （有固件时）官方镜像逐字节回归
.github/workflows/       云端 Linux 构建
docs/                    调研结论、前置条件、构建刷入、待办适配
```

> **基线要与实机同代**：`images/` 与 `images_16/` 是两代固件，DTB 与 vendor ramdisk
> 内容不同，但解析出的板级参数（地址 / page size / cmdline / 分区大小）完全一致。
> 刷进手机的 vendor_boot 必须由**实机当前运行的那一代**固件合并而来。

## 关键技术判断

- **没有 recovery 分区**。MT6993 是 GKI + A/B + 动态分区，recovery 只能作为
  vendor_boot 里 `type=2 name=recovery` 的 vendor ramdisk 片段存在
  （`BOARD_MOVE_RECOVERY_RESOURCES_TO_VENDOR_BOOT`）。
- **不能整体替换 vendor_boot**。官方 vendor_boot 里还有 MTK 的内核模块和 DTB，
  直接刷 TWRP 产出的镜像会丢模块。所以流程是：编译 → `repack_vendor_boot.py`
  把 recovery 片段插回官方镜像。
- **所有魔数都来自官方固件**。load address、page size、cmdline、header version、
  分区大小由 `extract_stock.py` 解析官方 `vendor_boot.img` 后生成
  `local-inputs/board_config.local.mk`，避免抄错别人的机型参数。
- **先能开机，再谈功能**。首版目标是进 TWRP 主界面 + 挂载 super/逻辑分区；
  FBE 解密、触摸、亮度等按 `docs/04-设备适配待办.md` 逐项补。

## 快速开始

```bash
# 0) 自检工具链（有官方固件时会顺带做逐字节回归）
python3 tools/selftest.py

# 1) 用官方固件生成板级输入（当前基线 = Android 16 整包 images_16）
python3 tools/extract_stock.py \
    --firmware-dir ota_full/images_16 \
    --device-tree device/oppo/PLG110
cp ota_full/images_16/vendor_boot.img \
   device/oppo/PLG110/local-inputs/stock_vendor_boot.img

# 2) 在 Linux 上构建（或推送到 GitHub Actions 跑 .github/workflows/build-twrp.yml）
./tools/build_twrp.sh /path/to/twrp-16.0-src

# 3) 刷入（BL 已解锁；镜像已补齐到分区大小，可整分区覆盖）
fastboot flash vendor_boot PLG110_twrp_vendor_boot.img
fastboot --disable-verity --disable-verification flash vbmeta backup/vbmeta.img
```

详细步骤见 `docs/03-构建与刷入.md`。

## 现在的状态

| 项目 | 状态 |
|---|---|
| 平台/机型确认（PLG110 = Find X9 Pro / MT6993） | 已完成 |
| boot/vendor_boot 解析与重打包工具 | 已完成；官方镜像**逐字节回归**通过（A16 与更新版各一份） |
| 设备树骨架 | 已完成 |
| 板级参数（DTB/地址/cmdline/分区大小） | 已生成（`images_16` 基线；两代固件解析结果一致） |
| recovery.fstab | 已按官方 fstab 重建（与官方逐行一致） |
| 官方镜像备份 | 已完成：`backup/` + `sha256sums.txt` |
| 完整编译出镜像 | 待 Linux 构建环境 |
| 触摸 / FBE 解密 / 亮度 | 未开始，见待办文档 |
| BL 解锁 | **前置条件，需你确认机型状态** |

## 需要你确认

1. **实机当前跑的是哪一代固件**（ColorOS 16 / Android 16，还是更新版）——
   决定 repack 用 `images_16/` 还是 `images/` 的 `vendor_boot.img`。
   不确定就 `adb shell getprop ro.build.version.release` / `ro.build.display.id`。
2. **BL 解锁状态**：`fastboot oem device-info` 或进 fastboot 看是否已解锁。
3. **Linux 构建环境**：本机 Docker/UTM/远程服务器，或用仓库里的 GitHub Actions。

## 风险提示

改 vendor_boot 属于刷机操作：会清数据、可能变砖、影响保修、可能导致支付类 App 与
OTA 异常。所有操作请自行评估，并先备份官方 `vendor_boot.img` / `boot.img` / `vbmeta.img`。
