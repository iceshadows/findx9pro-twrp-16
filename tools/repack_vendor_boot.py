#!/usr/bin/env python3
"""把 TWRP 编译出的 recovery ramdisk 合并回官方 vendor_boot.img。

TWRP 编译产物 (out/target/product/PLG110/vendor_boot.img) 里的 vendor ramdisk
只含 recovery；MTK 平台还需要官方 ramdisk 里的 kernel module、以及 header 里的
load address / DTB 等。本工具在官方 vendor_boot 基础上，替换/追加
"recovery" 类型的 vendor ramdisk 片段，其余原样保留。

用法:
  python3 tools/repack_vendor_boot.py \
      --stock  ~/fw/vendor_boot.img \
      --twrp   out/target/product/PLG110/vendor_boot.img \
      --out    PLG110_vendor_boot_twrp.img
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bootimg  # noqa: E402


def load(path):
    with open(path, "rb") as f:
        return bootimg.BootImage(f.read())


def pick_recovery_fragment(twrp):
    """从 TWRP 产物中取出 recovery 片段。"""
    for entry, frag in twrp.split_ramdisk():
        if isinstance(entry, str):
            continue
        if entry.type == bootimg.RAMDISK_TYPE_RECOVERY or entry.name == "recovery":
            return bootimg.RamdiskEntry(entry.size, entry.offset,
                                        bootimg.RAMDISK_TYPE_RECOVERY,
                                        entry.name or "recovery", entry.board_id), frag
    # 没有 table（或没有 recovery 项）时，整块 ramdisk 视为 recovery
    if twrp.ramdisk:
        return bootimg.RamdiskEntry(len(twrp.ramdisk), 0,
                                    bootimg.RAMDISK_TYPE_RECOVERY,
                                    "recovery", ""), twrp.ramdisk
    raise SystemExit("TWRP 产物中没有可用的 ramdisk")


def verify(stock, out):
    """加载合成镜像并逐项比对，任一项不符直接退出（避免刷入砖头）。"""
    img = bootimg.BootImage(out)
    h0, h1 = stock.header, img.header
    for key in ("header_version", "page_size", "cmdline", "name",
                "kernel_addr", "ramdisk_addr", "tags_addr", "dtb_size",
                "dtb_addr", "header_size"):
        if h0[key] != h1[key]:
            sys.exit(f"校验失败：header.{key} {h0[key]!r} -> {h1[key]!r}")
    if img.dtb != stock.dtb:
        sys.exit("校验失败：DTB 与官方不一致")
    if img.bootconfig != stock.bootconfig:
        sys.exit("校验失败：bootconfig 与官方不一致")
    if h1["ramdisk_size"] != sum(e.size for e in img.entries):
        sys.exit("校验失败：vendor_ramdisk_size 与片段大小之和不符")
    offs = [e.offset for e in img.entries]
    if offs != [sum(e.size for e in img.entries[:i]) for i in range(len(offs))]:
        sys.exit(f"校验失败：vendor ramdisk 片段偏移不是紧邻拼接 {offs}")
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--twrp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--avbtool", help="可选：avbtool 路径，用于重新签名")
    ap.add_argument("--avb-key", help="签名密钥（配合 --avbtool）")
    ap.add_argument("--partition-size", type=int, default=0,
                    help="vendor_boot 分区大小；默认由官方镜像的 AVB footer 推导")
    ap.add_argument("--no-pad", action="store_true",
                    help="不把镜像补齐到分区大小（默认会补齐，避免残留旧数据）")
    args = ap.parse_args()

    stock = load(args.stock)
    twrp = load(args.twrp)
    if not stock.is_vendor_boot or stock.header["header_version"] < 4:
        sys.exit("官方 vendor_boot 必须是 header v4（含 vendor ramdisk table），当前为 v%d"
                 % stock.header["header_version"])

    rec_entry, rec_frag = pick_recovery_fragment(twrp)
    algo = bootimg.detect_algo(rec_frag)
    print(f"[repack] recovery 片段 {len(rec_frag)} B，压缩={algo}，"
          f"头部={rec_frag[:4].hex()}")
    if algo == "raw":
        print("[repack] 警告：片段看起来是未压缩的 cpio，会显著变大。")
        print("         请在 TWRP 侧确认 BOARD_RAMDISK_USE_LZ4 := true 已生效。")

    entries, fragments = [], []
    replaced = False
    for entry, frag in stock.split_ramdisk():
        if not isinstance(entry, str) and (
                entry.type == bootimg.RAMDISK_TYPE_RECOVERY or entry.name == "recovery"):
            entries.append(rec_entry)
            fragments.append(rec_frag)
            replaced = True
            print(f"[repack] 替换官方 recovery 片段 (原 {entry.size} B)")
        else:
            entries.append(entry)
            fragments.append(frag)
            label = getattr(entry, "name", "?") or f"type{getattr(entry, 'type', 0)}"
            print(f"[repack] 保留片段 {label} type={getattr(entry, 'type', 0)} "
                  f"({len(frag)} B)")
    if not replaced:
        entries.append(rec_entry)
        fragments.append(rec_frag)
        print("[repack] 官方镜像无 recovery 片段，追加")

    out = stock.rebuild_vendor_boot(entries, fragments)

    # 分区大小：官方镜像被 avbtool 补齐到整个分区并在末尾附 footer，
    # 因此“官方文件大小”就等于 vendor_boot 分区大小；footer 里另存的
    # original_image_size 是有效内容长度，可用来交叉核对合成结果。
    part_size = args.partition_size
    footer = bootimg.avb_footer(stock.raw)
    if not part_size and footer:
        part_size = len(stock.raw)
        print(f"[repack] 由官方 AVB footer 推得分区大小 {part_size} B"
              f"（官方有效内容 {footer[0]} B，本产物 {len(out)} B）")
    if part_size:
        if len(out) > part_size:
            sys.exit(
                f"错误：合成镜像 {len(out)} B 超过分区大小 {part_size} B "
                f"(超出 {len(out) - part_size} B)。\n"
                f"       请裁剪 recovery（在 BoardConfig.mk 里加 TW_EXCLUDE_* / "
                f"减小 TWRP 体积）或换用更大的 vendor_boot 分区。")
        if args.no_pad:
            print(f"[repack] 未补齐（{len(out)} B），分区剩余部分保留旧数据。")
        else:
            out += b"\x00" * (part_size - len(out))
            print(f"[repack] 已补齐到分区大小 {part_size} B")

    with open(args.out, "wb") as f:
        f.write(out)
    print(f"[repack] 写出 {args.out} ({len(out)} B)")

    check = verify(stock, out)
    print("[repack] 校验通过：header 全字段/DTB/bootconfig 与官方一致，"
          "片段紧邻拼接")
    for e in check.entries:
        print(f"          type={e.type} name={e.name!r} size={e.size} off={e.offset}")

    if args.avbtool:
        size = part_size or len(out)
        cmd = [args.avbtool, "add_hash_footer", "--image", args.out,
               "--partition_name", "vendor_boot", "--partition_size", str(size),
               "--algorithm", "SHA256_RSA2048"]
        if args.avb_key:
            cmd += ["--key", args.avb_key]
        print("[repack] " + " ".join(cmd))
        sys.exit(os.system(" ".join(cmd)))
    print("[repack] 提示：镜像已不再匹配官方 AVB 校验值。")
    print("         设备已解锁时直接刷即可；未解锁需先解锁 BL。")
    print("         若遇到 verify 失败，刷机时加：")
    print("           fastboot --disable-verity --disable-verification flash vbmeta "
          "vbmeta.img")
    print("         或加 --avbtool/--avb-key 用自有密钥重新签名。")


if __name__ == "__main__":
    main()
