#!/usr/bin/env python3
"""只替换 Lua 字符串字面量内部的 \\ddd 十进制转义序列。

用法:
    python decode_escapes.py <输入目录>                # 原地替换
    python decode_escapes.py <输入目录> <输出目录>     # 输出到新目录 (保持结构)
    python decode_escapes.py <输入目录> <输出目录> --all  # 也处理纯 ASCII

处理规则:
  - 双引号 "..." 和单引号 '...' 内的 \\ddd -> 尝试 UTF-8 解码
  - 长字符串 [[...]] / [=[...]=] 内不处理 (Lua 不解释转义)
  - 注释 -- ... 和 --[[ ... ]] 内不处理
  - 字符串里的 \\" \\' 等转义正确处理
  - 连续 \\ddd 一起 UTF-8 解码 (如 \\229\\133\\165 -> "入")
  - UTF-8 解码失败 / 含 \\0 -> 原样保留
"""
import os, sys, re, argparse

_ONE_ESC = re.compile(rb'\\(\d{1,3})')


def _decode_run(vals, only_non_ascii):
    try:
        s = bytes(vals).decode('utf-8')
    except UnicodeDecodeError:
        return None
    if '\x00' in s:
        return None
    if only_non_ascii and all(ord(c) < 0x80 for c in s):
        return None
    return s


def decode_string_inner(inner, only_non_ascii=True):
    out = bytearray()
    i = 0
    n = len(inner)
    while i < n:
        m = _ONE_ESC.match(inner, i)
        if not m:
            out.append(inner[i])
            i += 1
            continue
        p = i
        vals = []
        while p < n:
            m2 = _ONE_ESC.match(inner, p)
            if not m2:
                break
            v = int(m2.group(1))
            if v > 255:
                break
            vals.append(v)
            p = m2.end()
        if not vals:
            out.append(inner[i])
            i += 1
            continue
        s = _decode_run(vals, only_non_ascii)
        if s is None:
            out += inner[i:p]
        else:
            out += s.encode('utf-8')
        i = p
    return bytes(out), len(inner) != len(out)


def process_lua(content, only_non_ascii=True):
    out = bytearray()
    i = 0
    n = len(content)
    DQ   = ord('"')
    SQ   = ord("'")
    LB   = ord('[')
    EQ   = ord('=')
    MIN  = ord('-')
    NL   = ord('\n')
    BS   = ord('\\')

    changes = 0

    while i < n:
        c = content[i]

        # 注释
        if c == MIN and i + 1 < n and content[i+1] == MIN:
            j = i + 2
            eq = 0
            while j < n and content[j] == EQ:
                eq += 1; j += 1
            if j < n and content[j] == LB:
                close = b']' + b'=' * eq + b']'
                k = content.find(close, j + 1)
                if k == -1:
                    out += content[i:]
                    return bytes(out), changes
                out += content[i:k + len(close)]
                i = k + len(close)
                continue
            else:
                k = content.find(b'\n', i)
                if k == -1:
                    out += content[i:]
                    return bytes(out), changes
                out += content[i:k]
                i = k
                continue

        # 长字符串
        if c == LB:
            j = i + 1
            eq = 0
            while j < n and content[j] == EQ:
                eq += 1; j += 1
            if j < n and content[j] == LB:
                close = b']' + b'=' * eq + b']'
                k = content.find(close, j + 1)
                if k == -1:
                    out += content[i:]
                    return bytes(out), changes
                out += content[i:k + len(close)]
                i = k + len(close)
                continue

        # 双引号字符串
        if c == DQ:
            j = i + 1
            ended = False
            while j < n:
                if content[j] == BS:
                    j += 2; continue
                if content[j] == DQ:
                    ended = True; break
                if content[j] == NL:
                    break
                j += 1
            if not ended:
                out += content[i:]
                return bytes(out), changes
            inner = content[i + 1:j]
            new_inner, ch = decode_string_inner(inner, only_non_ascii)
            if ch: changes += 1
            out.append(DQ); out += new_inner; out.append(DQ)
            i = j + 1
            continue

        # 单引号字符串
        if c == SQ:
            j = i + 1
            ended = False
            while j < n:
                if content[j] == BS:
                    j += 2; continue
                if content[j] == SQ:
                    ended = True; break
                if content[j] == NL:
                    break
                j += 1
            if not ended:
                out += content[i:]
                return bytes(out), changes
            inner = content[i + 1:j]
            new_inner, ch = decode_string_inner(inner, only_non_ascii)
            if ch: changes += 1
            out.append(SQ); out += new_inner; out.append(SQ)
            i = j + 1
            continue

        out.append(c)
        i += 1

    return bytes(out), changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input",  help="输入目录或单个 .lua 文件")
    ap.add_argument("output", nargs="?",
                    help="输出目录 (可选); 不给则原地替换")
    ap.add_argument("--all", action="store_true",
                    help="也替换能组成纯 ASCII 的 \\ddd")
    args = ap.parse_args()

    in_path  = args.input
    out_path = args.output
    inplace  = out_path is None   # 未指定输出目录 -> 原地替换

    # 收集输入文件
    if os.path.isfile(in_path):
        targets = [(in_path, None)]
        base = os.path.dirname(in_path) or "."
    else:
        base = in_path
        targets = []
        for root, _, files in os.walk(in_path):
            for name in files:
                if name.lower().endswith(".lua"):
                    src = os.path.join(root, name)
                    rel = os.path.relpath(src, in_path)
                    targets.append((src, rel))

    only_non_ascii = not args.all
    mode = "原地替换" if inplace else f"输出到 {out_path}"
    print(f"输入: {in_path}    共 {len(targets)} 个 .lua")
    print(f"模式: {mode}    only_non_ascii={only_non_ascii}")
    print("-" * 60)

    files_changed = 0
    total_changes = 0
    copied_unchanged = 0

    for i, (src, rel) in enumerate(targets, 1):
        try:
            with open(src, 'rb') as f:
                content = f.read()
            new_content, changes = process_lua(content, only_non_ascii)
        except Exception as e:
            print(f"  [ERR] {src}: {e}")
            continue

        # 决定目标路径
        if inplace:
            dst = src
        else:
            if rel is None:  # 单文件输入 + 输出目录
                dst = os.path.join(out_path, os.path.basename(src))
            else:
                dst = os.path.join(out_path, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)

        if changes:
            files_changed += 1
            total_changes += changes
            with open(dst, 'wb') as f:
                f.write(new_content)
        else:
            # 无改动: 原地模式跳过, 输出模式仍需复制一份保持结构完整
            if not inplace:
                copied_unchanged += 1
                with open(dst, 'wb') as f:
                    f.write(content)

        if i % 500 == 0:
            print(f"  {i}/{len(targets)}  已处理 {files_changed} 个有修改")

    print("-" * 60)
    print(f"完成: {files_changed} 个文件有修改, 共 {total_changes} 处替换")
    if not inplace:
        print(f"      另有 {copied_unchanged} 个文件无修改但已复制")
        print(f"输出目录: {out_path}")


if __name__ == "__main__":
    main()