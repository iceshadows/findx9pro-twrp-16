#
# OPPO Find X9 Pro (PLG110) / MediaTek MT6993 — TWRP 16.0 (Android 16, bp2a)
# A/B + 动态分区 + GKI；无独立 recovery 分区，recovery 走 vendor_boot。
#
DEVICE_PATH := device/oppo/PLG110

TARGET_ARCH := arm64
TARGET_ARCH_VARIANT := armv8-a
TARGET_CPU_ABI := arm64-v8a
TARGET_CPU_VARIANT := generic
TARGET_CPU_VARIANT_RUNTIME := generic
TARGET_SUPPORTS_32_BIT_APPS := false
TARGET_SUPPORTS_64_BIT_APPS := true

TARGET_BOOTLOADER_BOARD_NAME := mt6993
TARGET_BOARD_PLATFORM := mt6993
TARGET_NO_BOOTLOADER := true
TARGET_NO_KERNEL := true
TARGET_COPY_OUT_VENDOR := vendor
TARGET_COPY_OUT_ODM := odm

ALLOW_MISSING_DEPENDENCIES := true

# 固件派生参数（load addr / pagesize / cmdline / header ver / 分区大小）
# 由 tools/extract_stock.py 生成，不写死。
PLG110_LOCAL_INPUTS := $(DEVICE_PATH)/local-inputs
ifneq ($(wildcard $(PLG110_LOCAL_INPUTS)/board_config.local.mk),)
include $(PLG110_LOCAL_INPUTS)/board_config.local.mk
else
$(error 缺少 $(PLG110_LOCAL_INPUTS)/board_config.local.mk; 先执行 python3 tools/extract_stock.py --firmware-dir <固件目录> --device-tree $(DEVICE_PATH))
endif

BOARD_INCLUDE_DTB_IN_BOOTIMG := true
BOARD_PREBUILT_DTBIMAGE_DIR := $(PLG110_LOCAL_INPUTS)
ifeq ($(wildcard $(PLG110_LOCAL_INPUTS)/dtb.img),)
$(error 缺少 $(PLG110_LOCAL_INPUTS)/dtb.img; 需从官方 vendor_boot.img 提取)
endif

# recovery 归入 vendor_boot
BOARD_MOVE_RECOVERY_RESOURCES_TO_VENDOR_BOOT := true
BOARD_INCLUDE_RECOVERY_RAMDISK_IN_VENDOR_BOOT := true
TARGET_NO_RECOVERY := true
TARGET_RECOVERY_FSTAB := $(DEVICE_PATH)/recovery.fstab
TARGET_RECOVERY_PIXEL_FORMAT := RGBX_8888

BOARD_FLASH_BLOCK_SIZE := 262144
BOARD_HAS_LARGE_FILESYSTEM := true
BOARD_HAS_NO_SELECT_BUTTON := true
BOARD_SUPPRESS_SECURE_ERASE := true

TW_THEME := portrait_hdpi
# twrp-16.0 已把 -DTW_USE_NEW_MINADBD 写死在 Android.bp 的 recovery cflags 里，
# 设备树再设 TW_USE_NEW_MINADBD 是无效配置，故移除。
TW_INCLUDE_FASTBOOTD := true
TW_EXCLUDE_DEFAULT_USB_INIT := true

RECOVERY_SDCARD_ON_DATA := true
TW_INTERNAL_STORAGE_MOUNT_POINT := "/data/media/0"

# FBE：TWRP 16.0 的 prebuilt/Android.mk 里只有 TW_INCLUDE_CRYPTO 与
# TW_INCLUDE_CRYPTO_FBE 两个开关，后者才会把 libtwrpfscrypt、keystore2、
# keymint/gatekeeper、fscryptpolicyget 等 A16 组件链进来。
# 注意 TW_INCLUDE_FBE / TW_INCLUDE_FBE_METADATA_DECRYPT 在 twrp-16.0 中已不存在
# （写了也不会被消费），真正的 meta 解密由 TW_INCLUDE_CRYPTO_FBE 覆盖。
TW_INCLUDE_CRYPTO := true
TW_INCLUDE_CRYPTO_FBE := true

# 以下两个是 TWRP 12.1+ 常见做法，但在 twrp-16.0 的源码里没找到消费点，
# 先保留（未知的 TW_ 变量会被忽略，不影响编译），首版跑通后按需清理。
TW_KEEP_VENDOR_MOUNTED := true
TW_KEEP_ODM_MOUNTED := true

TW_EXCLUDE_APEX := true
TW_EXCLUDE_TZDATA := true
TW_EXCLUDE_NANO := true
TW_EXCLUDE_BASH := true
