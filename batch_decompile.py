#!/usr/bin/env python3
"""Mini World lua 反编译: 三阶段降级 + 空文件兜底

阶段: D_opmap → A_swap → B_patch → 空文件
"""
import os, sys, struct, subprocess, tempfile, shutil, threading, re
from concurrent.futures import ThreadPoolExecutor

UNLUAC   = "unluac_2025_12_23.jar"
OPMAP    = "opmap.txt"
SRC_DIR  = sys.argv[1] if len(sys.argv) > 1 else "test"
DST_DIR  = sys.argv[2] if len(sys.argv) > 2 else "test_lua_src"
WORKERS  = 8

MANIFEST_PATH = os.path.join(DST_DIR, "_manifest.txt")
MANIFEST_LOCK = threading.Lock()

GAME_TO_STD = list(range(64))
GAME_TO_STD[10] = 12; GAME_TO_STD[11] = 13; GAME_TO_STD[12] = 14
GAME_TO_STD[13] = 15; GAME_TO_STD[14] = 16; GAME_TO_STD[15] = 17
GAME_TO_STD[16] = 18; GAME_TO_STD[17] = 19; GAME_TO_STD[18] = 20
GAME_TO_STD[19] = 10; GAME_TO_STD[20] = 11


def _is_disasm(out):
    head = out[:80].lstrip()
    return (head.startswith(b".version ") or
            head.startswith(b".format ") or
            head.startswith(b".endianness "))


_SKELETON_LINE = re.compile(
    rb'^\s*(?:'
    rb'local\s+L\d+_\d+(?:\s*,\s*L\d+_\d+)*'
    rb'|return\s*$'
    rb'|--[^\n]*'
    rb')\s*$'
)


def is_skeleton(out):
    if not out:
        return True
    text = out.strip()
    if not text:
        return True
    if len(text) > 500:
        return False
    for line in text.split(b'\n'):
        if not line.strip():
            continue
        if not _SKELETON_LINE.match(line):
            return False
    return True


# ---------------- swap only ----------------
def swap_only(data, p):
    slen = struct.unpack_from('<I', data, p)[0]; p += 4 + slen
    p += 8 + 4
    ncode = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(ncode):
        ins = struct.unpack_from('<I', data, p)[0]
        struct.pack_into('<I', data, p, (ins & ~0x3F) | GAME_TO_STD[ins & 0x3F])
        p += 4
    nconst = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nconst):
        t = data[p]; p += 1
        if t == 0: pass
        elif t == 1: p += 1
        elif t == 3: p += 8
        elif t == 4:
            slen = struct.unpack_from('<I', data, p)[0]; p += 4 + slen
        else: raise ValueError(f"const type {t}")
    nproto = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nproto):
        p = swap_only(data, p)
    nli = struct.unpack_from('<I', data, p)[0]; p += 4 + 4 * nli
    nlv = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nlv):
        nl = struct.unpack_from('<I', data, p)[0]; p += 4 + nl + 8
    nup = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nup):
        nl = struct.unpack_from('<I', data, p)[0]; p += 4 + nl
    return p


# ---------------- swap + patch maxstack ----------------
def swap_and_patch(data, p):
    slen = struct.unpack_from('<I', data, p)[0]; p += 4 + slen
    p += 8 + 3
    off_ms = p; old_ms = data[p]; p += 1
    ncode = struct.unpack_from('<I', data, p)[0]; p += 4
    max_reg = 0
    for _ in range(ncode):
        ins = struct.unpack_from('<I', data, p)[0]
        std_op = GAME_TO_STD[ins & 0x3F]
        A = (ins >> 6) & 0xFF
        if std_op in (11, 28, 29):
            if A + 1 > max_reg: max_reg = A + 1
        elif A > max_reg:
            max_reg = A
        struct.pack_into('<I', data, p, (ins & ~0x3F) | std_op)
        p += 4
    data[off_ms] = min(255, max(old_ms, max_reg + 1))
    nconst = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nconst):
        t = data[p]; p += 1
        if t == 0: pass
        elif t == 1: p += 1
        elif t == 3: p += 8
        elif t == 4:
            slen = struct.unpack_from('<I', data, p)[0]; p += 4 + slen
        else: raise ValueError(f"const type {t}")
    nproto = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nproto):
        p = swap_and_patch(data, p)
    nli = struct.unpack_from('<I', data, p)[0]; p += 4 + 4 * nli
    nlv = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nlv):
        nl = struct.unpack_from('<I', data, p)[0]; p += 4 + nl + 8
    nup = struct.unpack_from('<I', data, p)[0]; p += 4
    for _ in range(nup):
        nl = struct.unpack_from('<I', data, p)[0]; p += 4 + nl
    return p


def apply_inplace(data, fn):
    d = bytearray(data)
    fn(d, 12)
    return bytes(d)


# ---------------- unluac ----------------
def run_unluac(path, extra=()):
    try:
        r = subprocess.run(
            ["java", "-jar", UNLUAC] + list(extra) + [path],
            capture_output=True, timeout=180,
        )
        if r.returncode == 0 and r.stdout and not _is_disasm(r.stdout):
            return r.stdout
        return None
    except Exception:
        return None


def record(stage, src, out_path, note=""):
    with MANIFEST_LOCK:
        with open(MANIFEST_PATH, "a", encoding="utf-8") as f:
            f.write(f"{stage}\t{src}\t{out_path}\t{note}\n")


# ---------------- process ----------------
def process(item):
    src, rel = item
    dst = os.path.join(DST_DIR, rel)

    try:
        data = open(src, 'rb').read()
    except Exception as e:
        record("read-error", src, "", str(e)[:80])
        return ("read-error", src, str(e)[:80])

    # 非字节码: 原样复制
    if data[:4] != b"\x1bLua":
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        record("copy", src, dst)
        return ("copy", src, "")

    # 预生成 A / B 版本
    v_swap = v_patch = None
    try: v_swap  = apply_inplace(data, swap_only)
    except Exception: pass
    try: v_patch = apply_inplace(data, swap_and_patch)
    except Exception: pass

    # 三阶段: D_opmap → A_swap → B_patch
    attempts = []
    if os.path.exists(OPMAP):
        attempts.append(("D_opmap", None,    ("--opmap", OPMAP)))
    if v_swap is not None:
        attempts.append(("A_swap",  v_swap,  ()))
    if v_patch is not None:
        attempts.append(("B_patch", v_patch, ()))

    for label, blob, extra in attempts:
        if blob is None:
            try_path = src
            cleanup = False
        else:
            with tempfile.NamedTemporaryFile(suffix='.luac', delete=False) as tmp:
                tmp.write(blob); try_path = tmp.name
            cleanup = True
        try:
            out = run_unluac(try_path, extra)
            if out:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if is_skeleton(out):
                    open(dst, 'wb').write(b"")
                    record("empty", src, dst)
                    return ("empty", src, "")
                open(dst, 'wb').write(out)
                record(label, src, dst)
                return (label, src, "")
        finally:
            if cleanup:
                try: os.unlink(try_path)
                except: pass

    # 三阶段全失败: 空文件
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, 'wb').write(b"")
    record("empty", src, dst)
    return ("empty", src, "")


# ---------------- main ----------------
def main():
    os.makedirs(DST_DIR, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        f.write("stage\tsource\toutput\tnote\n")

    if not os.path.exists(OPMAP):
        print(f"[WARN] {OPMAP} 不存在, D_opmap 阶段会被跳过")

    targets = []
    for root, _, files in os.walk(SRC_DIR):
        for name in files:
            if name.lower().endswith(".lua"):
                s = os.path.join(root, name)
                targets.append((s, os.path.relpath(s, SRC_DIR)))

    print(f"输入 {SRC_DIR}  →  输出 {DST_DIR}    共 {len(targets)} 个文件")
    print(f"阶段: D_opmap → A_swap → B_patch → 空文件")
    print("-" * 60)

    stats = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (st, src, msg) in enumerate(ex.map(process, targets), 1):
            stats[st] = stats.get(st, 0) + 1
            if i % 500 == 0:
                print(f"  {i}/{len(targets)}  {stats}")

    print("-" * 60)
    print(f"完成: {stats}")

    d  = stats.get("D_opmap", 0)
    a  = stats.get("A_swap", 0)
    b  = stats.get("B_patch", 0)
    em = stats.get("empty", 0)
    cp = stats.get("copy", 0)
    print(f"\n有内容: {d + a + b}  空文件: {em}  明文复制: {cp}")
    print(f"manifest: {MANIFEST_PATH}")


if __name__ == "__main__":
    main()