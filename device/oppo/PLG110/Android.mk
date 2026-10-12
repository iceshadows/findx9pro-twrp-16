LOCAL_PATH := $(call my-dir)

ifeq ($(TARGET_DEVICE),PLG110)

include $(CLEAR_VARS)
LOCAL_MODULE := init.recovery.mt6993.rc
LOCAL_MODULE_TAGS := optional
LOCAL_MODULE_CLASS := ETC
LOCAL_MODULE_PATH := $(TARGET_ROOT_OUT)
LOCAL_SRC_FILES := rootdir/init.recovery.mt6993.rc
include $(BUILD_PREBUILT)

include $(CLEAR_VARS)
LOCAL_MODULE := init.recovery.PLG110.rc
LOCAL_MODULE_TAGS := optional
LOCAL_MODULE_CLASS := ETC
LOCAL_MODULE_PATH := $(TARGET_ROOT_OUT)
LOCAL_SRC_FILES := rootdir/init.recovery.PLG110.rc
include $(BUILD_PREBUILT)

endif
