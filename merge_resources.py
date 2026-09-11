#!/usr/bin/env python3
"""合并资源目录: 用反编译的 lua 源码替换原始解包目录里的 lua 字节码。

用法:
    python merge_resources.py <原始解包目录> <反编译目录> [输出目录]

参数:
    原始解包目录   pkg 解包结果 (含 lua 字节码 + json/csv/xml/图片等)
    反编译目录     反编译并转义还原后的 lua 目录 (含 .lua 源码)
    输出目录       可选; 不给则原地合并 (用反编译 lua 覆盖原目录)

合并规则:
  - 遍历原始目录的所有文件, 复制到输出目录
  - 若原始文件是 .lua 且反编译目录有同路径文件 -> 用反编译版本替换
  - 只跳过 _unpack_report.json 和 _manifest.txt 这两个报告文件
  - 保持目录结构不变
"""
import os, sys, shutil


# 只跳过这两个明确的报告文件 (不能误伤 lua)
SKIP_NAMES = {
    "_unpack_report.json",
    "_manifest.txt",
}


def should_skip(name):
    return name in SKIP_NAMES


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    raw_dir  = sys.argv[1]                       # 原始解包目录
    lua_dir  = sys.argv[2]                       # 反编译目录
    out_dir  = sys.argv[3] if len(sys.argv) > 3 else raw_dir
    inplace  = (out_dir == raw_dir)

    if not os.path.isdir(raw_dir):
        sys.exit(f"原始解包目录不存在: {raw_dir}")
    if not os.path.isdir(lua_dir):
        sys.exit(f"反编译目录不存在: {lua_dir}")

    print(f"原始解包目录: {raw_dir}")
    print(f"反编译目录:   {lua_dir}")
    print(f"输出目录:     {out_dir}  ({'原地合并' if inplace else '输出到新目录'})")
    print("-" * 60)

    # 索引反编译目录里的所有 .lua 文件 (相对路径 -> 绝对路径)
    lua_map = {}
    for root, _, files in os.walk(lua_dir):
        for name in files:
            if not name.lower().endswith(".lua"):
                continue
            if should_skip(name):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, lua_dir)
            lua_map[rel] = abs_path

    print(f"反编译 lua 文件数: {len(lua_map)}")
    if not lua_map:
        sys.exit("反编译目录里没有 .lua 文件")

    # 遍历原始目录
    stats = {"replaced": 0, "kept": 0, "lua_missing": 0,
             "copied_other": 0, "skipped": 0}
    missing_lua = []

    for root, _, files in os.walk(raw_dir):
        for name in files:
            src_abs = os.path.join(root, name)

            # 只跳过这两个报告文件
            if should_skip(name):
                stats["skipped"] += 1
                continue

            rel = os.path.relpath(src_abs, raw_dir)

            # 计算输出路径
            if inplace:
                dst_abs = src_abs
            else:
                dst_abs = os.path.join(out_dir, rel)
                os.makedirs(os.path.dirname(dst_abs), exist_ok=True)

            is_lua = name.lower().endswith(".lua")

            if is_lua:
                # 尝试用反编译版本
                repl_src = lua_map.get(rel)
                if repl_src is not None:
                    if inplace:
                        if not _same_content(src_abs, repl_src):
                            shutil.copy2(repl_src, dst_abs)
                            stats["replaced"] += 1
                        else:
                            stats["kept"] += 1
                    else:
                        shutil.copy2(repl_src, dst_abs)
                        stats["replaced"] += 1
                else:
                    # 反编译目录里没有 -> 保留原始字节码
                    if not inplace:
                        shutil.copy2(src_abs, dst_abs)
                    stats["lua_missing"] += 1
                    missing_lua.append(rel)
            else:
                # 非 lua: 直接复制
                if not inplace:
                    shutil.copy2(src_abs, dst_abs)
                stats["copied_other"] += 1

    print("-" * 60)
    print(f"替换 lua (反编译版):  {stats['replaced']}")
    print(f"保留原 lua (无对应):  {stats['lua_missing']}")
    print(f"未变 lua (内容相同):  {stats['kept']}")
    print(f"其它资源文件:         {stats['copied_other']}")
    print(f"跳过 (报告文件):      {stats['skipped']}")
    print(f"输出目录: {out_dir}")

    # 输出目录文件总数 (只排除那两个报告文件)
    total = 0
    for root, _, files in os.walk(out_dir):
        for name in files:
            if not should_skip(name):
                total += 1
    print(f"合并后文件总数: {total}")

    if missing_lua:
        print(f"\n以下 {len(missing_lua)} 个 lua 未在反编译目录找到对应文件:")
        for r in missing_lua[:20]:
            print(f"  {r}")
        if len(missing_lua) > 20:
            print(f"  ... 共 {len(missing_lua)} 个")


def _same_content(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, 'rb') as f1, open(b, 'rb') as f2:
            return f1.read() == f2.read()
    except Exception:
        return False


if __name__ == "__main__":
    main()