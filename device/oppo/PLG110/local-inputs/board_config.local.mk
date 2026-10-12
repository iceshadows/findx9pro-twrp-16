# 由 tools/extract_stock.py 生成 —— 请勿手工编辑
# 来源: vendor_boot.img
BOARD_BOOT_HEADER_VERSION := 4
BOARD_KERNEL_PAGESIZE := 4096
BOARD_KERNEL_BASE := 0x00000000
BOARD_KERNEL_CMDLINE := bootopt=64S3,32N2,64N2 bootconfig buildvariant=user
BOARD_USES_GENERIC_KERNEL_IMAGE := true
BOARD_RAMDISK_USE_LZ4 := true
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := 100663296
BOARD_VENDOR_BOOT_CMDLINE := bootopt=64S3,32N2,64N2 bootconfig buildvariant=user
BOARD_MKBOOTIMG_ARGS := --header_version 4 --pagesize 4096 --base 0x00000000 --kernel_offset 0x80000000 --ramdisk_offset 0xa3800000 --tags_offset 0x87c80000 --dtb_offset 0x87c80000 --board ""
