DEVICE_PATH := device/oppo/PLG110

$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit_only.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/base.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/ramdisk_stub.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/virtual_ab_ota.mk)
$(call inherit-product, vendor/twrp/config/common.mk)

PRODUCT_SHIPPING_API_LEVEL := 36
PRODUCT_TARGET_VNDK_VERSION := 36

AB_OTA_UPDATER := true
ENABLE_VIRTUAL_AB := true
PRODUCT_USE_DYNAMIC_PARTITIONS := true

PRODUCT_PROPERTY_OVERRIDES += \
    ro.twrp.vendor_boot=true

PRODUCT_PACKAGES += \
    init.recovery.mt6993.rc \
    init.recovery.PLG110.rc

# 首版按体积裁掉非必需组件
PRODUCT_PACKAGES -= \
    bash \
    nano \
    vim \
    htop \
    lsof \
    openvpn \
    powertop \
    fsck.exfat \
    mount.exfat \
    mkfs.exfat \
    fsck.ntfs \
    mount.ntfs \
    mkfs.ntfs

PRODUCT_SOONG_NAMESPACES += $(DEVICE_PATH)
