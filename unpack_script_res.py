#!/usr/bin/env python3
"""Unpack Mini World (迷你世界) script_res.pkg.

索引布局 (ver=0x8b):
  u32 N
  N * [16B MD5][u32 X][u32 Y][u32 Z]   # 28 字节/记录
  N * [u32 A][u32 L][L 字节路径]         # 无对齐填充

数据区: 未压缩存储，每个文件块 = [u32 uncompressed_size][LZ4 block]
        块偏移 = X - 16

配对: paths 按路径字节序排序后，sorted_paths[k] ↔ recs[k]
      （关键：records 不排序，paths 排序后与原序 records 一一对应）
"""
import struct, sys, os, json, hashlib
import lz4.block

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PKG = sys.argv[1] if len(sys.argv) > 1 else "script_res.pkg"
OUT = sys.argv[2] if len(sys.argv) > 2 else "script_res_unpacked"
HDR_SIZE = 16


def parse_index(idx):
    S = len(idx)
    N = struct.unpack_from("<I", idx, 0)[0]
    if not (1 <= N <= 500000) or 4 + N * 28 > S:
        return None, None
    recs = []
    for k in range(N):
        off = 4 + k * 28
        md5 = bytes(idx[off:off + 16])
        X, Y, Z = struct.unpack_from("<III", idx, off + 16)
        recs.append({"md5": md5, "X": X, "Y": Y, "Z": Z})
    pos = 4 + N * 28
    paths = []
    for _ in range(N):
        if pos + 8 > S:
            return None, None
        A, L = struct.unpack_from("<II", idx, pos)
        if L > 2000 or pos + 8 + L > S:
            return None, None
        p = bytes(idx[pos + 8:pos + 8 + L]).rstrip(b"\x00\x20")
        paths.append({"A": A, "p": p})
        pos += 8 + L
    return recs, paths


def decompress_block(block):
    if len(block) < 8:
        return block, "raw-short"
    usize = struct.unpack("<I", block[:4])[0]
    if 0 < usize < 500_000_000:
        try:
            return lz4.block.decompress(block[4:], uncompressed_size=usize), "lz4-prefix"
        except Exception:
            pass
    try:
        return lz4.block.decompress(block), "lz4-raw"
    except Exception:
        pass
    return block, "raw"


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(PKG, "rb") as f:
        ver, unk, idx_off, idx_size = struct.unpack("<4I", f.read(HDR_SIZE))
        print(f"header: ver={ver:#x}  idx@{idx_off}+{idx_size}")

        f.seek(idx_off)
        iblob = f.read(idx_size)
        iusize = struct.unpack("<I", iblob[:4])[0]
        idx = lz4.block.decompress(iblob[4:], uncompressed_size=iusize)

        recs, paths = parse_index(idx)
        if recs is None:
            sys.exit("无法解析索引")
        N = len(recs)
        print(f"N={N}")

        f.seek(HDR_SIZE)
        data = f.read(idx_off - HDR_SIZE)
        print(f"data region: {len(data)} bytes")

        # ---- 关键: 按路径字节序排序, 与 records 顺序一一配对 ----
        order = sorted(range(N), key=lambda i: paths[i]["p"])

        md5_ok = md5_bad = 0
        decomp_ok = decomp_fail = 0
        extracted = 0
        errors = []

        for k in range(N):
            pi = order[k]
            p = paths[pi]["p"]
            r = recs[k]                       # 注意: 用 k 索引 records

            rel = p.decode("utf-8", "replace")
            while rel.startswith("../"):
                rel = rel[3:]

            off = r["X"] - HDR_SIZE
            if off < 0 or off + r["Y"] > len(data):
                errors.append((rel, f"OOB off={off} Y={r['Y']}"))
                continue
            block = data[off:off + r["Y"]]

            # MD5 校验的是压缩块
            if hashlib.md5(block).digest() == r["md5"]:
                md5_ok += 1
            else:
                md5_bad += 1

            # 解压
            content, method = decompress_block(block)
            if method.startswith("lz4"):
                decomp_ok += 1
            else:
                decomp_fail += 1

            dest = os.path.join(OUT, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as g:
                g.write(content)
            extracted += 1
            if extracted % 1000 == 0:
                print(f"  extracted {extracted} ...")

        print(f"\nextracted: {extracted}")
        print(f"MD5: ok {md5_ok}  bad {md5_bad}")
        print(f"decompress: lz4 {decomp_ok}  raw {decomp_fail}")
        if errors:
            print(f"errors: {len(errors)}")
            for e in errors[:5]:
                print("  ", e)

        with open(os.path.join(OUT, "_unpack_report.json"), "w", encoding="utf-8") as g:
            json.dump({"pkg": PKG, "mapping": "P_name_asc + R_identity",
                       "files": extracted, "md5_ok": md5_ok, "md5_bad": md5_bad,
                       "decomp_ok": decomp_ok, "decomp_raw": decomp_fail,
                       "errors": errors[:50]},
                      g, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()