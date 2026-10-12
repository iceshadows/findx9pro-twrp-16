#!/usr/bin/env python3
"""自检：按 AOSP bootimg.h 规范手工构造 vendor_boot v4，验证解析与重打包。

不依赖任何固件，可在 macOS 直接运行：
  python3 tools/selftest.py
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootimg  # noqa: E402

PAGE = 4096
HEADER_SIZE = 2128
ARGS_SIZE = 2048
NAME_SIZE = 16
ENTRY_SIZE = 108


def pad(data, page=PAGE):
    return data + b"\x00" * ((page - len(data) % page) % page)


def make_vendor_boot(fragments, dtb, bootconfig, page=PAGE):
    """fragments: [(name, type, bytes)]，按 AOSP mkbootimg 布局手工拼装。

    注意：片段之间**紧邻**拼接（不补齐），只有整块 ramdisk 之后补到页边界，
    与 mkbootimg 及 MTK 官方 vendor_boot 一致。
    """
    blob = bytearray()
    table = bytearray()
    for name, rtype, data in fragments:
        offset = len(blob)
        blob += data
        table += struct.pack("<3I", len(data), offset, rtype)
        table += name.encode().ljust(32, b"\x00")
        table += b"".ljust(ENTRY_SIZE - 12 - 32, b"\x00")

    hdr = bytearray(HEADER_SIZE)
    hdr[0:8] = b"VNDRBOOT"
    struct.pack_into("<5I", hdr, 8, 4, page, 0x80000000, 0xa6F00000, len(blob))
    hdr[28:28 + len(b"bootopt=64S3,32N2,64N2")] = b"bootopt=64S3,32N2,64N2"
    struct.pack_into("<I", hdr, 2076, 0x87C80000)      # tags_addr
    hdr[2080:2080 + 6] = b"mt6993"                      # name
    struct.pack_into("<I", hdr, 2096, HEADER_SIZE)
    struct.pack_into("<I", hdr, 2100, len(dtb))
    struct.pack_into("<Q", hdr, 2104, 0x87C80000)
    struct.pack_into("<4I", hdr, 2112, len(table), len(fragments), ENTRY_SIZE,
                     len(bootconfig))

    img = bytearray()
    img += pad(bytes(hdr), page)
    img += pad(bytes(blob), page)
    img += pad(dtb, page)
    img += pad(bytes(table), page)
    img += pad(bootconfig, page)   # bootconfig 之后同样补到页边界（与官方一致）
    return bytes(img)


def main():
    frags = [("platform", 1, bytes(range(256)) * 4), ("dlkm", 3, b"D" * 500)]
    dtb = b"\xd0\x0d\xfe\xed" + b"x" * 2996
    bootconfig = b"androidboot.hardware=mt6993\n"

    raw = make_vendor_boot(frags, dtb, bootconfig)
    print(f"[selftest] 合成 vendor_boot v4: {len(raw)} B")

    img = bootimg.BootImage(raw)
    h = img.header
    assert img.is_vendor_boot
    assert h["header_version"] == 4, h["header_version"]
    assert h["page_size"] == PAGE
    assert h["kernel_addr"] == 0x80000000, hex(h["kernel_addr"])
    assert h["ramdisk_addr"] == 0xA6F00000
    assert h["tags_addr"] == 0x87C80000, hex(h["tags_addr"])
    assert h["dtb_addr"] == 0x87C80000, hex(h["dtb_addr"])
    assert h["cmdline"] == "bootopt=64S3,32N2,64N2", h["cmdline"]
    assert h["name"] == "mt6993", h["name"]
    assert img.dtb == dtb, (len(img.dtb), len(dtb))
    assert img.bootconfig == bootconfig
    assert len(img.entries) == 2

    blob = img.ramdisk
    assert blob[0:len(frags[0][2])] == frags[0][2]
    off1 = img.entries[1].offset
    assert off1 == len(frags[0][2]), off1   # 片段紧邻拼接
    assert blob[off1:off1 + len(frags[1][2])] == frags[1][2]
    assert h["ramdisk_size"] == sum(len(f) for _n, _t, f in frags)
    print("[selftest] 解析 OK：地址/cmdline/DTB/片段偏移全部正确")

    # 重打包：追加一个 recovery 片段
    entries = list(img.entries)
    frags_out = [f for _n, _t, f in frags]
    rec = bootimg.RamdiskEntry(0, 0, bootimg.RAMDISK_TYPE_RECOVERY, "recovery", "")
    entries.append(rec)
    frags_out.append(b"TWRP" * 300)
    out = img.rebuild_vendor_boot(entries, frags_out)

    img2 = bootimg.BootImage(out)
    assert len(img2.entries) == 3, len(img2.entries)
    assert img2.entries[2].type == bootimg.RAMDISK_TYPE_RECOVERY
    assert img2.entries[2].name == "recovery"
    assert img2.dtb == dtb
    assert img2.bootconfig == bootconfig
    assert img2.header["kernel_addr"] == h["kernel_addr"]
    assert img2.header["ramdisk_addr"] == h["ramdisk_addr"]
    assert img2.header["cmdline"] == h["cmdline"]
    assert img2.header["ramdisk_size"] == sum(len(f) for f in frags_out)
    b2 = img2.ramdisk
    for e, frag in zip(img2.entries, frags_out):
        assert b2[e.offset:e.offset + e.size] == frag, e.name
    # 片段必须紧邻（AOSP 语义），否则 bootloader 会有额外要求
    assert [e.offset for e in img2.entries] != [0, 0, 0]
    print(f"[selftest] 重打包 OK：{len(out)} B, 3 个片段")

    # LZ4 解码自检（用系统 lz4 CLI 生成样本，没有就跳过）
    if os.system("command -v lz4 >/dev/null 2>&1") == 0:
        import subprocess
        payload = b"hello cpio " * 1000
        frame = subprocess.run(["lz4", "-c"], input=payload,
                               stdout=subprocess.PIPE, check=True).stdout
        assert bootimg.decompress(frame)[0] == payload
        print("[selftest] LZ4 frame 解码 OK")

    check_real_images()
    print("[selftest] 全部通过")


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 官方固件可能有多个基线（Android 16 的整包与更新版 OTA payload 未必同代），
# 全部纳入回归，避免只在某一份固件上“碰巧通过”。
REAL_VENDOR_BOOTS = [
    os.path.join(ROOT, "ota_full", "images_16", "vendor_boot.img"),
    os.path.join(ROOT, "ota_full", "images", "vendor_boot.img"),
    os.path.join(ROOT, "device", "oppo", "PLG110", "local-inputs",
                 "stock_vendor_boot.img"),
]


def check_real_images():
    found = [p for p in REAL_VENDOR_BOOTS if os.path.exists(p)]
    if not found:
        print(f"[selftest] 跳过真实镜像检查（无 {REAL_VENDOR_BOOTS[0]} 等）")
        return
    for path in found:
        check_real_image(path)


def check_real_image(path):
    """官方 vendor_boot.img 回归。

    核心断言：用解析出来的片段原样重打包，结果必须与官方镜像的有效内容
    **逐字节相同**。一次性验证 header 偏移、片段紧邻拼接、页对齐、
    table/bootconfig 位置、以及 dtb_addr 等字段的保留策略。
    """
    if not os.path.exists(path):
        print(f"[selftest] 跳过真实镜像检查（无 {path}）")
        return
    with open(path, "rb") as f:
        raw = f.read()
    img = bootimg.BootImage(raw)
    assert img.is_vendor_boot and img.header["header_version"] == 4
    entries = img.entries
    print(f"[selftest] 真实镜像 {os.path.basename(os.path.dirname(path))}/"
          f"{os.path.basename(path)}: {len(raw)} B, "
          f"{len(entries)} 片段, dtb={len(img.dtb)} B, bootconfig="
          f"{len(img.bootconfig)} B")

    offs = [e.offset for e in entries]
    assert offs[0] == 0, offs
    assert offs == [sum(e.size for e in entries[:i]) for i in range(len(entries))], offs
    assert img.header["ramdisk_size"] == sum(e.size for e in entries)

    # DTB 段是按“页对齐后的 vendor ramdisk 之后”这一结构约定取出的。
    # MTK 的 dtb.img 是 Android dt_table（magic 0xd7b7ab1e，offset 4 是整包大小），
    # 其 total_size 必须等于取出的长度，否则说明分段布局判断错了。
    dtb = img.dtb
    if dtb[:4] == b"\xd7\xb7\xab\x1e":
        (total_size,) = struct.unpack_from(">I", dtb, 4)
        assert total_size == len(dtb), (total_size, len(dtb))
        (cnt,) = struct.unpack_from(">I", dtb, 16)
        print(f"[selftest] DTB 为 dt_table: {total_size} B, {cnt} 个条目 (自洽)")
    elif dtb[:4] == b"\xd0\x0d\xfe\xed":
        (total_size,) = struct.unpack_from(">I", dtb, 4)
        assert total_size == len(dtb), (total_size, len(dtb))
        print(f"[selftest] DTB 为 FDT: {total_size} B (自洽)")
    else:
        print(f"[selftest] DTB magic={dtb[:4].hex()}（未校验自洽性）")

    frags = [f for _e, f in img.split_ramdisk()]
    out = img.rebuild_vendor_boot(list(entries), list(frags))
    assert len(out) == img.content_end, (
        f"重打包大小 {len(out)} != 官方有效内容 {img.content_end}")
    assert out == raw[:len(out)], f"{path} 重打包结果与官方有效内容不一致"
    label = os.path.basename(os.path.dirname(path))
    print(f"[selftest] {label} 重打包逐字节一致 OK ({len(out)} B)")

    # 再用 avbtool 自己记录的长度做交叉验证：footer 里的 original_image_size
    # 是厂商签名时的有效内容长度，应该与我们的合成长度完全相同。
    footer = bootimg.avb_footer(raw)
    if footer:
        assert len(out) == footer[0], (
            f"合成长度 {len(out)} != avbtool 记录的 original_image_size {footer[0]}")
        print(f"[selftest] {label} 长度与 avbtool 记录一致：有效内容 {footer[0]} B，"
              f"文件/分区 {len(raw)} B")


if __name__ == "__main__":
    main()
