# local-inputs

本目录由 `tools/extract_stock.py` 依据**官方固件**生成，不要手工编辑，也不要提交到
公共仓库（固件内容受版权保护）。

## 当前基线

| 项 | 值 |
|---|---|
| 机型 | OPPO PLG110（Find X9 Pro）/ MediaTek MT6993 |
| Android | **16**（TWRP 16.0 对应 `twrp-16.0` 分支，lunch 目标 `bp2a`） |
| 固件目录 | `ota_full/images_16/`（整包镜像，含 `vendor_boot.img`/`boot.img`/`vbmeta*.img`） |

`ota_full/images/` 是从 OTA `payload.bin` 解出来的**更新版**固件
（`meta/payload_properties.txt`: `android_version=17`、`oplus_rom_version=V17.0.0`），
它的 DTB 与 vendor ramdisk 都与 A16 不同，**不要**混用：刷入的 vendor_boot 必须与
手机上实际运行的固件同代，否则内核模块与 DTB 会对不上。

两代固件解析出的板级参数（`board_config.local.mk`）与 `recovery.fstab` **完全相同**，
差异只在 `dtb.img` 与 `vendor_ramdisk/` 的内容，所以换基线只需重新跑一次
`extract_stock.py`。

## 在 Linux 构建机上必须有

- `board_config.local.mk` —— `BoardConfig.mk` 会 include；缺失时编译会直接报错退出。
- `dtb.img` —— `BOARD_PREBUILT_DTBIMAGE_DIR` 使用；缺失时编译会直接报错退出。
- `stock_vendor_boot.img` —— 官方 vendor_boot 原件，`tools/build_twrp.sh` 用它做合并；
  没有它就只能刷 TWRP 直接产出的 vendor_boot（会缺官方 vendor ramdisk 里的内核模块）。

其余是给人核对用的中间产物：`vendor_boot_info.json`、`stock-fstab/`、`vendor_ramdisk/`。

## 生成方式

```bash
python3 tools/extract_stock.py \
    --firmware-dir ota_full/images_16 \
    --device-tree device/oppo/PLG110
cp ota_full/images_16/vendor_boot.img \
   device/oppo/PLG110/local-inputs/stock_vendor_boot.img
```

## 官方镜像备份

`backup/`（仓库根目录）存有本基线用到的官方镜像原件与 `sha256sums.txt`，
用于刷坏后回滚。刷 vendor_boot 前后都可以用
`shasum -a 256 -c backup/sha256sums.txt` 核对。
