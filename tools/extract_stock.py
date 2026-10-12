#!/usr/bin/env python3
"""从 OPPO 官方固件提取 TWRP(PLG110) 所需的板级输入。

产物（全部落在 device/oppo/PLG110/local-inputs/）：
  board_config.local.mk   由 stock vendor_boot 头部推导出的板级参数
  dtb.img / dtb           stock DTB（BOARD_PREBUILT_DTBIMAGE_DIR 需要）
  vendor_boot_info.json   解析出的头部信息，便于人工核对
  vendor_ramdisk/         拆分出的 vendor ramdisk 片段（含模块）
  stock-fstab/            从 ramdisk 中捞到的 fstab 原文
并按 stock fstab 生成/校对 device/oppo/PLG110/recovery.fstab。

用法:
  python3 tools/extract_stock.py --firmware-dir ota_full/images_16 --device-tree device/oppo/PLG110
  python3 tools/extract_stock.py --vendor-boot ~/fw/vendor_boot.img \
      --vendor-boot-partition-size 100663296 --device-tree device/oppo/PLG110

未指定 --vendor-boot-partition-size 时按 vendor_boot.img 的文件大小取值：官方镜像被
avbtool 补齐到整个分区并附有 footer，所以文件长度通常就等于分区大小。
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootimg  # noqa: E402

VENDOR_BOOT_NAMES = ["vendor_boot.img", "vendor_boot_a.img", "vendor_boot_b.img"]
BOOT_NAMES = ["boot.img", "boot_a.img"]

# fstab 中要剔除的 Android 专有 flag（recovery 不需要，且会干扰 TWRP）
DROP_FLAGS = {"avb", "defaults", "first_stage_mount", "seclabel", "nodiratime"}
DROP_PREFIX = ("avb=", "avb_keys=", "checkpoint=", "context=", "fsverity",
               "sysfs_path=", "alloc_mode=")
SKIP_FSTYPES = {"tmpfs", "devpts", "proc", "sysfs", "cgroup", "cgroup2",
                "none", "overlay"}
SKIP_MOUNTS = ("/proc/", "/sys/", "/dev/", "/tmp/")
# MTK 把 persist 挂到 /mnt/vendor/persist，TWRP 惯例挂到 /persist
REMAP_MOUNTS = {"/mnt/vendor/persist": "/persist"}
# 同一挂载点同时给出多种文件系统时，按此优先级取一个（Android 16/17 实际用 erofs）
FSTYPE_PRIORITY = {"erofs": 0, "f2fs": 1, "ext4": 2, "auto": 3, "vfat": 4,
                   "emmc": 5}
# TWRP 必需但官方 fstab 里不会出现的分区（super 由 liblp 管理，不写进 fstab）
EXTRA_PARTITIONS = [
    ("/dev/block/by-name/super", "/super", "emmc", "defaults", ""),
]
# 输出顺序：逻辑分区 -> 数据分区 -> 刷写分区，其余按字母序排在后面
LOGICAL_ORDER = ["/system", "/system_ext", "/vendor", "/product", "/odm",
                 "/vendor_dlkm", "/odm_dlkm", "/system_dlkm"]
DATA_ORDER = ["/metadata", "/persist", "/data"]
FLASH_ORDER = ["/boot", "/init_boot", "/vendor_boot", "/dtbo", "/vbmeta",
               "/vbmeta_system", "/vbmeta_vendor", "/misc", "/super",
               "/persistent"]


def log(msg):
    print(f"[extract] {msg}")


def find_image(directory, names):
    for name in names:
        p = os.path.join(directory, name)
        if os.path.isfile(p):
            return p
    return None


def read_mk(path):
    with open(path, "rb") as f:
        return f.read()


def decode_os_version(v):
    if not v:
        return None
    return "%d.%d.%d" % ((v >> 14) + 2000, (v >> 7) & 0x7F, v & 0x7F)


def collect_fstabs(img, outdir):
    """从 vendor ramdisk（及 boot ramdisk）里捞出所有 fstab 原文。"""
    found = []
    blobs = []
    for entry, frag in img.split_ramdisk():
        if isinstance(entry, str):
            label = "vendor_ramdisk"
        else:
            label = f"vendor_ramdisk[{entry.name or entry.type}]"
        blobs.append((label, frag))

    os.makedirs(outdir, exist_ok=True)
    for label, frag in blobs:
        try:
            raw, algo = bootimg.decompress(frag)
        except Exception as exc:  # noqa: BLE001
            log(f"  跳过 {label}: 解压失败 ({exc})")
            continue
        if algo != "raw":
            log(f"  {label}: {algo} 解压后 {len(raw)} 字节")
        for name, text in bootimg.find_fstab(raw):
            safe = name.strip("/").replace("/", "_") or "fstab"
            with open(os.path.join(outdir, safe), "w") as f:
                f.write(text)
            found.append((safe, text))
            log(f"  发现 {name} -> stock-fstab/{safe}")
    return found


def _clean_tokens(text):
    """按逗号拆开并剔除 recovery 不需要的 flag，返回保序去重后的列表。"""
    kept = []
    for tok in text.split(","):
        tok = tok.strip()
        if not tok or tok in DROP_FLAGS or tok.startswith(DROP_PREFIX):
            continue
        if tok not in kept:
            kept.append(tok)
    return kept


def parse_fstab_line(line):
    """解析一行 fstab，返回 (src, mnt, fstype, mount_opts, flags) 或 None。

    Android fstab 是 5 列：device  mount_point  fstype  mount_options  fs_mgr_flags
    TWRP 的 recovery.fstab 沿用同样格式，因此这里保持列结构，只做清洗。
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    src, mnt, fstype = parts[0], parts[1], parts[2]
    mount_opts = parts[3]
    flags = ",".join(parts[4:]) if len(parts) > 4 else ""

    if fstype in SKIP_FSTYPES:
        return None
    if src.startswith("/devices/"):
        # 可移动存储（sdcard / USB OTG），挂载点为 auto，整体保留
        return src, mnt, fstype, ",".join(_clean_tokens(mount_opts)) or "defaults", \
            ",".join(_clean_tokens(flags))

    mnt = REMAP_MOUNTS.get(mnt, mnt)
    if mnt == "/" or not mnt.startswith("/"):
        return None
    mnt_key = mnt if mnt.endswith("/") else mnt + "/"
    if mnt_key.startswith(SKIP_MOUNTS) or mnt_key.startswith("/mnt/"):
        return None

    raw_flags = flags.split(",")
    if "logical" in raw_flags or not src.startswith("/"):
        # 逻辑分区第一列写分区名而非设备路径
        src = os.path.basename(mnt)
    else:
        src = re.sub(r"^/dev/block/(platform/[^/]+/)?", "/dev/block/", src)
        if not src.startswith("/dev/block/"):
            return None

    opts = ",".join(_clean_tokens(mount_opts)) or "defaults"
    return src, mnt, fstype, opts, ",".join(_clean_tokens(flags))


def _pick_fstab_rows(stock_fstabs):
    """选定主 fstab 并做挂载点去重。

    stock 里通常是 fstab.mt6993（主表，覆盖最全）+ fstab.emmc / fstab.raid
    （变体或裁剪版），三者重叠且分区命名不一致（dpm1 vs dpm_1），直接拼接会
    产出重复项。策略：只取覆盖挂载点最多的那份主表；主表缺失的“必需”挂载点
    再用其它表补齐。同一挂载点同时给出多种文件系统时按 FSTYPE_PRIORITY 取优
    （my_* 在 stock 里 ext4 写在 erofs 前面，需要纠正为 erofs）。
    """
    tables = []
    for name, text in stock_fstabs:
        rows = [r for r in (parse_fstab_line(l) for l in text.splitlines()) if r]
        mnts = {r[1] for r in rows if not r[0].startswith("/devices/")}
        tables.append((len(mnts), name, rows))
    tables.sort(key=lambda t: -t[0])
    if not tables:
        return []
    primary_name, primary_rows = tables[0][1], tables[0][2]

    chosen = {}
    for row in primary_rows:
        key = ("removable", row[0]) if row[0].startswith("/devices/") else ("mnt", row[1])
        prio = FSTYPE_PRIORITY.get(row[2], 9)
        cur = chosen.get(key)
        if cur is None or prio < cur[0]:
            chosen[key] = (prio, row)

    needed = set(LOGICAL_ORDER) | set(DATA_ORDER) | set(FLASH_ORDER)
    missing = needed - {k[1] for k in chosen if k[0] == "mnt"}
    if missing:
        for _score, name, rows in tables[1:]:
            for row in rows:
                if row[0].startswith("/devices/") or row[1] not in missing:
                    continue
                chosen[("mnt", row[1])] = (FSTYPE_PRIORITY.get(row[2], 9), row)
                missing.discard(row[1])
                print(f"[extract]  补齐 {row[1]} <- {name}")
    log(f"  采用主 fstab: {primary_name}")
    return [v[1] for v in chosen.values()]


def _order_key(row):
    src, mnt, fstype = row[0], row[1], row[2]
    if src.startswith("/devices/"):
        return (4, 0, mnt)
    if mnt == "/super":
        return (0, 0, mnt)
    if "logical" in row[4] or mnt in LOGICAL_ORDER:
        return (1, LOGICAL_ORDER.index(mnt) if mnt in LOGICAL_ORDER else 99, mnt)
    if mnt in DATA_ORDER:
        return (2, DATA_ORDER.index(mnt), mnt)
    if fstype == "emmc" and mnt in FLASH_ORDER:
        return (3, FLASH_ORDER.index(mnt), mnt)
    return (4, 1, mnt)


def gen_recovery_fstab(stock_fstabs, device_tree, template_header):
    rows = _pick_fstab_rows(stock_fstabs)
    if not rows:
        return None
    have = {r[1] for r in rows}
    for src, mnt, fstype, opts, flags in EXTRA_PARTITIONS:
        if mnt not in have:
            rows.append((src, mnt, fstype, opts, flags))
    rows.sort(key=_order_key)

    target = os.path.join(device_tree, "recovery.fstab")
    with open(target, "w") as f:
        f.write(template_header)
        f.write("\n")
        section = None
        for src, mnt, fstype, opts, flags in rows:
            bucket = _order_key((src, mnt, fstype, opts, flags))[0]
            title = {0: "# ==== super（动态分区宿主）====",
                     1: "# ==== 逻辑分区（super 内）====",
                     2: "# ==== 数据 / 元数据分区 ====",
                     3: "# ==== 刷写分区 ====",
                     4: "# ==== 其它物理分区 / 可移动存储 ===="}[bucket]
            if bucket != section:
                section = bucket
                f.write("\n" + title + "\n")
            line = "%s %s %s %s" % (src, mnt, fstype, opts)
            if flags:
                line += " " + flags
            f.write(line + "\n")
    return target


def emit_board_config(info, vb_path, out_path, partition_size, ramdisk_algo):
    addrs = {k: info[k] for k in ("kernel_addr", "ramdisk_addr", "tags_addr")}
    dtb_addr = info.get("dtb_addr", 0)
    args = [
        "--header_version %d" % info["header_version"],
        "--pagesize %d" % info["page_size"],
        "--base 0x00000000",
        "--kernel_offset 0x%08x" % addrs["kernel_addr"],
        "--ramdisk_offset 0x%08x" % addrs["ramdisk_addr"],
        "--tags_offset 0x%08x" % addrs["tags_addr"],
        "--dtb_offset 0x%08x" % dtb_addr,
        '--board ""',
    ]
    body = f"""# 由 tools/extract_stock.py 生成 —— 请勿手工编辑
# 来源: {os.path.basename(vb_path)}
BOARD_BOOT_HEADER_VERSION := {info['header_version']}
BOARD_KERNEL_PAGESIZE := {info['page_size']}
BOARD_KERNEL_BASE := 0x00000000
BOARD_KERNEL_CMDLINE := {info['cmdline']}
BOARD_USES_GENERIC_KERNEL_IMAGE := true
BOARD_RAMDISK_USE_LZ4 := {'true' if ramdisk_algo.startswith('lz4') else 'false'}
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := {partition_size}
BOARD_VENDOR_BOOT_CMDLINE := {info['cmdline']}
BOARD_MKBOOTIMG_ARGS := {' '.join(args)}
"""
    with open(out_path, "w") as f:
        f.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--firmware-dir", help="解包后的官方固件目录（含 vendor_boot.img 等）")
    ap.add_argument("--vendor-boot", help="直接指定 vendor_boot.img 路径")
    ap.add_argument("--boot", help="直接指定 boot.img 路径（可选，用于取 os_version）")
    ap.add_argument("--device-tree", default="device/oppo/PLG110")
    ap.add_argument("--vendor-boot-partition-size", type=int, default=0,
                    help="vendor_boot 分区大小（字节），未知时用 vendor_boot.img 文件大小取整")
    ap.add_argument("--no-fstab", action="store_true", help="不自动生成 recovery.fstab")
    args = ap.parse_args()

    vb_path = args.vendor_boot or (
        find_image(args.firmware_dir, VENDOR_BOOT_NAMES) if args.firmware_dir else None)
    if not vb_path or not os.path.isfile(vb_path):
        sys.exit("找不到 vendor_boot.img，请用 --firmware-dir 或 --vendor-boot 指定")

    boot_path = args.boot or (
        find_image(args.firmware_dir, BOOT_NAMES) if args.firmware_dir else None)

    outdir = os.path.join(args.device_tree, "local-inputs")
    os.makedirs(outdir, exist_ok=True)

    log(f"解析 {vb_path}")
    img = bootimg.BootImage(read_mk(vb_path))
    info = img.info()
    log(f"  vendor_boot header v{info['header_version']}, "
        f"page={info['page_size']}, cmdline={info['cmdline']!r}")
    log(f"  ramdisk={len(img.ramdisk)} B, dtb={len(img.dtb)} B, "
        f"片段数={len(img.entries)}")

    if boot_path:
        boot = bootimg.BootImage(read_mk(boot_path))
        osv = decode_os_version(boot.header.get("os_version", 0))
        info["boot_os_version"] = osv
        log(f"  boot.img header v{boot.header['header_version']}, os_version={osv}")

    with open(os.path.join(outdir, "vendor_boot_info.json"), "w") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

    if img.dtb:
        with open(os.path.join(outdir, "dtb.img"), "wb") as f:
            f.write(img.dtb)
        with open(os.path.join(outdir, "dtb"), "wb") as f:
            f.write(img.dtb)
        log(f"  已写出 dtb.img ({len(img.dtb)} B)")
    else:
        log("  警告: vendor_boot 中未找到 DTB，需确认固件是否完整")

    rd_dir = os.path.join(outdir, "vendor_ramdisk")
    os.makedirs(rd_dir, exist_ok=True)
    for entry, frag in img.split_ramdisk():
        name = getattr(entry, "name", "") or "platform"
        rtype = getattr(entry, "type", 1)
        with open(os.path.join(rd_dir, f"{rtype:02d}_{name}.img"), "wb") as f:
            f.write(frag)
    log(f"  已拆分 vendor ramdisk -> {rd_dir}/")

    vb_size = args.vendor_boot_partition_size
    if not vb_size:
        vb_size = os.path.getsize(vb_path)
        log(f"  未提供 vendor_boot 分区大小，暂用文件大小 {vb_size}（可能偏小，需按实机分区表修正）")
    ramdisk_algo = bootimg.detect_algo(bytes(img.ramdisk[:4]))
    log(f"  vendor ramdisk 压缩格式: {ramdisk_algo}")
    emit_board_config(info, vb_path, os.path.join(outdir, "board_config.local.mk"),
                      vb_size, ramdisk_algo)
    log("  已写出 board_config.local.mk")

    stock_fstabs = collect_fstabs(img, os.path.join(outdir, "stock-fstab"))
    if boot_path:
        try:
            boot = bootimg.BootImage(read_mk(boot_path))
            for name, text in bootimg.find_fstab(bootimg.decompress(boot.ramdisk)[0]):
                stock_fstabs.append((f"boot_{name.strip('/')}", text))
        except Exception as exc:  # noqa: BLE001
            log(f"  boot ramdisk 扫描跳过: {exc}")

    if not args.no_fstab and stock_fstabs:
        header = ("# 本文件由 tools/extract_stock.py 依据官方 fstab 生成\n"
                  "# 若需保留模板注释，请先备份再覆盖\n")
        target = gen_recovery_fstab(stock_fstabs, args.device_tree, header)
        if target:
            log(f"  已生成 {target}（请人工核对后再编译）")
    else:
        log("  未从固件中自动取得 fstab，继续使用设备树中的模板 recovery.fstab")

    log("完成。下一步：")
    log("  1) 核对 local-inputs/vendor_boot_info.json 与 recovery.fstab")
    log("  2) 同步 TWRP 16.0 源码后 m recovery vendorbootimage")
    log("  3) 用 tools/repack_vendor_boot.py 把 recovery ramdisk 合并回官方 vendor_boot")


if __name__ == "__main__":
    main()
