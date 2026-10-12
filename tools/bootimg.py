"""Android boot / vendor_boot 镜像解析与重打包（纯标准库）。

支持 boot header v0..v4 与 vendor_boot v3/v4，包含 vendor ramdisk table
(v4) 的解析与重建。用于 TWRP 移植时从官方固件取 kernel/dtb，
以及把 TWRP 的 recovery ramdisk 片段合并回官方 vendor_boot。
"""

import json
import struct

BOOT_MAGIC = b"ANDROID!"
VENDOR_BOOT_MAGIC = b"VNDRBOOT"

RAMDISK_TYPE_PLATFORM = 1
RAMDISK_TYPE_RECOVERY = 2
RAMDISK_TYPE_DLKM = 3

VENDOR_BOOT_ARGS_SIZE = 2048
VENDOR_BOOT_NAME_SIZE = 16
VENDOR_RAMDISK_NAME_SIZE = 32
VENDOR_RAMDISK_TABLE_ENTRY_V4_SIZE = 108


def _align(value, page):
    return (value + page - 1) // page * page


def _cstr(raw):
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace")


def avb_footer(raw):
    """解析镜像尾部的 AVB footer。

    返回 (original_image_size, vbmeta_offset, vbmeta_size)，没有 footer 时返回 None。

    厂商镜像通常被 `avbtool add_hash_footer --partition_size N` 补齐到整个分区并
    在末尾附上 footer，所以：
      * footer 里的 `original_image_size` = 有效内容长度（页对齐）；
      * 文件长度 = 分区大小（本机 vendor_boot 为 100663296 = 96 MiB）。
    """
    if len(raw) < 64 or raw[-64:-60] != b"AVBf":
        return None
    orig, vbmeta_off, vbmeta_size = struct.unpack_from(">3Q", raw, len(raw) - 52)
    return orig, vbmeta_off, vbmeta_size


class RamdiskEntry:
    def __init__(self, size, offset, rtype, name, board_id):
        self.size = size
        self.offset = offset
        self.type = rtype
        self.name = name
        self.board_id = board_id

    def to_dict(self):
        return {
            "size": self.size,
            "offset": self.offset,
            "type": self.type,
            "name": self.name,
            "board_id": self.board_id,
        }


class BootImage:
    """boot.img (v0-v2) 与 vendor_boot.img (v3/v4) 的统一解析结果。"""

    def __init__(self, raw):
        self.raw = raw
        self.is_vendor_boot = raw[:8] == VENDOR_BOOT_MAGIC
        self.header = {}
        self.kernel = b""
        self.ramdisk = b""
        self.second = b""
        self.dtb = b""
        self.recovery_dtbo = b""
        self.bootconfig = b""
        self.entries = []
        # 镜像“真实内容”长度：官方镜像会被填充到分区大小并附 AVB footer，
        # 这里记录有效数据的结束位置，便于比对/校验重打包结果。
        self.content_end = len(raw)
        if self.is_vendor_boot:
            self._parse_vendor_boot()
        elif raw[:8] == BOOT_MAGIC:
            self._parse_boot()
        else:
            raise ValueError("不是有效的 boot/vendor_boot 镜像（magic 不匹配）")

    # ------------------------------------------------------------- 解析
    def _parse_boot(self):
        # v3/v4 的 header_version 在 offset 40，v0-v2 在 offset 44
        probe = struct.unpack_from("<I", self.raw, 40)[0]
        modern = probe in (3, 4)
        if modern:
            (kernel_size,) = struct.unpack_from("<I", self.raw, 8)
            (ramdisk_size,) = struct.unpack_from("<I", self.raw, 12)
            (os_version,) = struct.unpack_from("<I", self.raw, 16)
            (header_size,) = struct.unpack_from("<I", self.raw, 20)
            header_version = probe
            page_size = 4096
            kernel_addr = ramdisk_addr = second_addr = tags_addr = 0
            second_size = 0
            name = ""
            cmdline = _cstr(self.raw[44:44 + 1536])
            recovery_dtbo_size = 0
        else:
            (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size,
             second_addr, tags_addr, page_size, header_version,
             os_version) = struct.unpack_from("<10I", self.raw, 8)
            header_size = 0
            name = _cstr(self.raw[48:64])
            cmdline = _cstr(self.raw[64:64 + 512])
        dtb_size = dtb_addr = 0
        if header_version < 3:
            if header_version >= 1:
                (_extra_size, recovery_dtbo_size, _recovery_dtbo_offset,
                 header_size) = struct.unpack_from("<4I", self.raw, 1632)
            else:
                recovery_dtbo_size = 0
                header_size = 0
            if header_version >= 2:
                (dtb_size, dtb_addr) = struct.unpack_from("<2I", self.raw, 1648)

        self.header = {
            "image": "boot",
            "header_version": header_version,
            "page_size": page_size,
            "os_version": os_version,
            "name": name,
            "cmdline": cmdline,
            "kernel_addr": kernel_addr,
            "ramdisk_addr": ramdisk_addr,
            "second_addr": second_addr,
            "tags_addr": tags_addr,
            "kernel_size": kernel_size,
            "ramdisk_size": ramdisk_size,
            "second_size": second_size,
            "dtb_size": dtb_size,
            "dtb_addr": dtb_addr,
            "header_size": header_size,
        }

        pos = page_size  # 头部占 1 页
        self.kernel = self.raw[pos:pos + kernel_size]
        pos = _align(pos + kernel_size, page_size)
        self.ramdisk = self.raw[pos:pos + ramdisk_size]
        pos = _align(pos + ramdisk_size, page_size)
        self.second = self.raw[pos:pos + second_size]
        pos = _align(pos + second_size, page_size)
        if recovery_dtbo_size:
            self.recovery_dtbo = self.raw[pos:pos + recovery_dtbo_size]
            pos = _align(pos + recovery_dtbo_size, page_size)
        if header_version >= 2 and dtb_size:
            self.dtb = self.raw[pos:pos + dtb_size]

    def _parse_vendor_boot(self):
        # 头部字段偏移（见 AOSP system/tools/mkbootimg/include/bootimg/bootimg.h）
        (header_version, page_size, kernel_addr, ramdisk_addr,
         ramdisk_size) = struct.unpack_from("<5I", self.raw, 8)
        cmdline = _cstr(self.raw[28:28 + VENDOR_BOOT_ARGS_SIZE])
        (tags_addr,) = struct.unpack_from("<I", self.raw, 2076)
        name = _cstr(self.raw[2080:2096])
        (header_size,) = struct.unpack_from("<I", self.raw, 2096)
        (dtb_size,) = struct.unpack_from("<I", self.raw, 2100)
        (dtb_addr,) = struct.unpack_from("<Q", self.raw, 2104)
        table_size = table_num = table_entry_size = bootconfig_size = 0
        if header_version >= 4:
            (table_size, table_num, table_entry_size,
             bootconfig_size) = struct.unpack_from("<4I", self.raw, 2112)

        self.header = {
            "image": "vendor_boot",
            "header_version": header_version,
            "page_size": page_size,
            "ramdisk_size": ramdisk_size,
            "name": name,
            "cmdline": cmdline,
            "kernel_addr": kernel_addr,
            "ramdisk_addr": ramdisk_addr,
            "tags_addr": tags_addr,
            "dtb_size": dtb_size,
            "dtb_addr": dtb_addr,
            "header_size": header_size,
            "table_size": table_size,
            "table_num": table_num,
            "table_entry_size": table_entry_size,
            "bootconfig_size": bootconfig_size,
        }

        # vendor_boot 无独立 kernel 段字段，header 之后即 vendor ramdisk
        pos = page_size
        self.ramdisk = self.raw[pos:pos + ramdisk_size]
        pos = _align(pos + ramdisk_size, page_size)
        if dtb_size:
            self.dtb = self.raw[pos:pos + dtb_size]
            pos = _align(pos + dtb_size, page_size)
        if header_version >= 4:
            table_blob = self.raw[pos:pos + table_size]
            pos = _align(pos + table_size, page_size)
            self.bootconfig = self.raw[pos:pos + bootconfig_size]
            # 有效内容结尾：bootconfig 之后同样补到页边界。依据是官方镜像尾部
            # AVB footer 里 avbtool 记录的 original_image_size 恰好是页对齐值
            # （A16: 59322368，而 bootconfig 结束在 59318366），且这段填充实测为 0。
            self.content_end = _align(pos + bootconfig_size, page_size)
            self.entries = self._parse_table(table_blob, table_num,
                                             table_entry_size)

    @staticmethod
    def _parse_table(blob, num, entry_size):
        entries = []
        for i in range(num):
            base = i * entry_size
            size, offset, rtype = struct.unpack_from("<3I", blob, base)
            name = _cstr(blob[base + 12:base + 12 + VENDOR_RAMDISK_NAME_SIZE])
            board_id = _cstr(blob[base + 12 + VENDOR_RAMDISK_NAME_SIZE:
                                  base + entry_size])
            entries.append(RamdiskEntry(size, offset, rtype, name, board_id))
        return entries

    # ------------------------------------------------------------- 输出
    def info(self):
        d = dict(self.header)
        d["entries"] = [e.to_dict() for e in self.entries]
        return d

    def split_ramdisk(self):
        """按 table 把 vendor ramdisk 拆成片段列表 [(entry, bytes)]。"""
        if not self.entries:
            return [("blob", self.ramdisk)]
        out = []
        for e in self.entries:
            chunk = self.ramdisk[e.offset:e.offset + e.size]
            out.append((e, chunk))
        return out

    # ------------------------------------------------- 重打包 vendor_boot
    def rebuild_vendor_boot(self, entries, fragments, bootconfig=None):
        """用新的片段列表重建 vendor_boot 镜像。

        entries: RamdiskEntry 列表（size/offset 会被重算）
        fragments: 与 entries 等长的 bytes 列表
        """
        if not self.is_vendor_boot:
            raise ValueError("仅支持重建 vendor_boot 镜像")
        page = self.header["page_size"] or 4096
        hdr = bytearray(self.raw[:page])

        if len(entries) != len(fragments):
            raise ValueError("entries 与 fragments 数量不一致")
        # AOSP mkbootimg 把各 vendor ramdisk 片段**紧邻**拼接（片段之间不补齐），
        # 只在整块 ramdisk 之后补到页边界；MTK 官方镜像同样如此
        # （例：片段 offset 0/45738691，后者不是 4096 的整数倍）。
        blob = bytearray()
        for e, frag in zip(entries, fragments):
            e.offset = len(blob)
            e.size = len(frag)
            blob += frag

        table = bytearray()
        for e in entries:
            ent = struct.pack("<3I", e.size, e.offset, e.type)
            ent += e.name.encode()[:31].ljust(VENDOR_RAMDISK_NAME_SIZE, b"\x00")
            ent += e.board_id.encode().ljust(VENDOR_RAMDISK_TABLE_ENTRY_V4_SIZE
                                             - 12 - VENDOR_RAMDISK_NAME_SIZE,
                                             b"\x00")
            table += ent
        table_size = len(table)
        table_padded = bytes(table) + b"\x00" * (_align(table_size, page) - table_size)

        if bootconfig is None:
            bootconfig = self.bootconfig

        out = bytearray(hdr)
        out += bytes(blob) + b"\x00" * (_align(len(blob), page) - len(blob))
        dtb = self.dtb
        out += dtb + b"\x00" * (_align(len(dtb), page) - len(dtb))
        out += table_padded
        # bootconfig 是最后一段，长度由 header.bootconfig_size 精确给出，但仍会
        # 补到页边界——官方镜像尾部 AVB footer 的 original_image_size 就是页对齐的
        # （见 BootImage.content_end 处的说明）。
        out += bootconfig + b"\x00" * (_align(len(bootconfig), page) - len(bootconfig))

        # 回填 header 字段
        struct.pack_into("<I", out, 24, len(blob))              # vendor_ramdisk_size
        struct.pack_into("<I", out, 2100, len(dtb))             # dtb_size
        # dtb_addr **不能**写文件偏移：MTK/OPPO 官方镜像里这个 v4 字段存的是
        # mkbootimg 的 --dtb_offset 装载地址（本机为 0x87c80000，远大于 96MB
        # 分区，bootloader 不可能当偏移用），DTB 的真实位置由“紧跟在页对齐后的
        # vendor ramdisk 之后”这一结构约定给出。因此保留官方原值。
        if self.header["header_version"] >= 4:
            struct.pack_into("<4I", out, 2112,
                             table_size, len(entries),
                             VENDOR_RAMDISK_TABLE_ENTRY_V4_SIZE, len(bootconfig))
        return bytes(out)


# --------------------------------------------------------------- 解压
def detect_algo(data):
    if data[:2] == b"\x1f\x8b":
        return "gzip"
    if data[:4] == b"\x04\x22\x4d\x18":
        return "lz4"
    if data[:4] == b"\x02\x21\x4c\x18":
        return "lz4-legacy"
    if data[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if data[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    return "raw"


def lz4_decompress_block(src):
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while i < n:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            raise ValueError("LZ4 非法 offset=0")
        mlen = token & 0x0F
        if mlen == 15:
            while i < n:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        if start < 0:
            raise ValueError("LZ4 引用越界")
        for j in range(mlen):
            out.append(out[start + j])
    return bytes(out)


def _lz4_frame(data):
    p = 4
    flg = data[p]
    p += 2  # FLG + BD
    content_size = None
    if flg & 0x08:
        content_size = struct.unpack_from("<Q", data, p)[0]
        p += 8
    if flg & 0x01:
        p += 4
    p += 1  # HC
    block_checksum = bool(flg & 0x10)
    out = bytearray()
    while True:
        bsize = struct.unpack_from("<I", data, p)[0]
        p += 4
        if bsize == 0:
            break
        uncompressed = bool(bsize & 0x80000000)
        bsize &= 0x7FFFFFFF
        raw = data[p:p + bsize]
        p += bsize
        out += raw if uncompressed else lz4_decompress_block(raw)
        if block_checksum:
            p += 4
    if content_size is not None and len(out) != content_size:
        raise ValueError("LZ4 frame 内容长度不符")
    return bytes(out)


def _lz4_legacy(data):
    p = 4
    out = bytearray()
    while p + 4 <= len(data):
        bsize = struct.unpack_from("<I", data, p)[0]
        p += 4
        if bsize == 0:
            break
        out += lz4_decompress_block(data[p:p + bsize])
        p += bsize
    return bytes(out)


def decompress(data):
    """返回 (解压后数据, 算法名)。无法识别时原样返回。"""
    algo = detect_algo(data)
    if algo == "gzip":
        import gzip
        return gzip.decompress(data), algo
    if algo == "lz4":
        return _lz4_frame(data), algo
    if algo == "lz4-legacy":
        return _lz4_legacy(data), algo
    if algo == "xz":
        import lzma
        return lzma.decompress(data), algo
    if algo == "zstd":
        try:
            import zstandard  # type: ignore
            return zstandard.ZstdDecompressor().decompress(data), algo
        except ImportError:
            raise RuntimeError(
                "ramdisk 为 zstd，需要 pip install zstandard 才能解包")
    return data, algo


# --------------------------------------------------------------- cpio
def cpio_entries(data):
    """解析 newc cpio，返回 [(name, content)]。"""
    out = []
    p = 0
    n = len(data)
    while p + 110 <= n:
        if data[p:p + 6] != b"070701":
            break
        try:
            fields = [int(data[p + 6 + i * 8:p + 14 + i * 8], 16)
                      for i in range(13)]
        except ValueError:
            break  # 头部损坏，放弃剩余部分
        mode, filesize, namesize = fields[1], fields[6], fields[11]
        if namesize == 0:
            break
        name_start = p + 110
        name = _cstr(data[name_start:name_start + namesize - 1])
        if name == "TRAILER!!!":
            break
        data_start = _align(name_start + namesize, 4)
        content = data[data_start:data_start + filesize]
        if mode & 0o170000 != 0o040000:
            out.append((name, content))
        p = _align(data_start + filesize, 4)
    return out


def find_fstab(ramdisk):
    """在解压后的 ramdisk 中查找 fstab 文件，返回 [(name, text)]。"""
    hits = []
    for name, content in cpio_entries(ramdisk):
        base = name.rsplit("/", 1)[-1]
        if base.startswith("fstab") and not name.endswith("/"):
            hits.append((name, content.decode("utf-8", "replace")))
    return hits


def dump_info(img, stream):
    json.dump(img.info(), stream, indent=2, ensure_ascii=False)
