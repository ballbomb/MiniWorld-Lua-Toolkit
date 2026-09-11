# MiniWorld Lua Toolkit

Mini World (迷你世界) 国际版 1.7.15 `script_res.pkg` 解包 + Lua 字节码反编译工具集。

**产物**：6000+ 个可读 Lua 源码文件（实际 6092 个）

---

## 目录

- [1. 环境准备](#1-环境准备)
- [2. pkg 资源包格式](#2-pkg-资源包格式)
- [3. Lua 字节码格式](#3-lua-字节码格式)
- [4. 反编译流程](#4-反编译流程)
- [5. 脚本清单](#5-脚本清单)
- [6. 完整使用示例](#6-完整使用示例)
- [7. 关键技术原理](#7-关键技术原理)
- [8. 常见问题](#8-常见问题)

---

## 1. 环境准备

### 1.1 需要的工具/库

| 工具/库 | 用途 | 获取方式 |
|---|---|---|
| **Python 3.8+** | 运行所有脚本 | python.org |
| **lz4** (`pip install lz4`) | 解压 pkg 数据区/索引区的 LZ4 块 | `pip install lz4` |
| **Java 8+** | 运行 unluac | adoptium.net |
| **unluac.jar** | Lua 5.1 字节码反编译 | [SourceForge](https://sourceforge.net/projects/unluac/) |
| **Ghidra** (可选) | 逆向 `liblua.dll` 提取 opcode 映射 | ghidra-sre.org |

### 1.2 安装依赖

```bash
pip install lz4
```

### 1.3 工作目录

```
工作目录/
├── unluac_2025_12_23.jar     # unluac 反编译器
├── opmap.txt                  # opcode 映射表
├── unpack_script_res.py       # pkg 解包脚本
├── batch_decompile.py         # 批量反编译脚本
├── decode_escapes.py          # \ddd 转义还原脚本
├── merge_resources.py         # 资源合并脚本
├── script_res.pkg             # 待解包的资源包
└── ...
```
### 1.4 关于 unluac.jar

本项目根目录自带了 `unluac_2025_12_23.jar`（unluac v1.2.3.569），
来源于 [SourceForge unluac 项目](https://sourceforge.net/projects/unluac/)，
遵循 MIT 许可，版权声明见根目录的 `unluac_LICENSE.txt`。

所有脚本默认在本目录查找 `unluac_2025_12_23.jar`。
如果想用其他版本，把新 jar 放同目录并改 `batch_decompile.py` 顶部的 `UNLUAC` 变量即可。

---

## 2. pkg 资源包格式

国际版 1.7.15 的 `script_res.pkg` 是 Rainbow Engine 的 PackageAsset 格式。

### 2.1 头部 (16 字节)

```
偏移  大小  字段          值 (本版本)        说明
0     4     version       0x8b               版本号
4     4     unknown       9                  未知字段
8     4     index_offset  55211403           索引区起始偏移
12    4     index_size    335237             索引区压缩后大小
```

`index_offset + index_size = 文件总大小`

### 2.2 数据区

紧跟 16 字节头部，长度 = `index_offset - 16`。

**未整体压缩**——但**每个文件块独立是 LZ4 压缩的**：

```
文件块结构:
  [u32 uncompressed_size]     # 解压后的大小
  [LZ4 compressed_data]       # LZ4 block
```

- 文件在数据区中的偏移 = `X - 16`
- 文件块总长度 = `Y`（包含 4 字节前缀）
- `rec.md5` = MD5(整个文件块，即 `[u32][LZ4]` 未解压状态)

**示例**（rec[0] 头部）：
```
bf 98 00 00  f5 03 ... 
└─ 0x98bf ──┘└─ LZ4 数据
   39103 = 解压后大小
```

### 2.3 索引区

索引区紧跟数据区。前 4 字节是**解压后大小**，之后是 LZ4 压缩数据。

解压后结构：

```
u32 N                              # 记录数 = 8172
N * [16B MD5][u32 X][u32 Y][u32 Z] # 28 字节/记录
N * [u32 A][u32 L][L字节路径]       # 无对齐填充
u32 0x085e                         # 尾部 4 字节（未使用）
```

字段说明：
| 字段 | 含义 |
|---|---|
| `MD5` | 文件块的 MD5（针对压缩状态） |
| `X` | 文件在数据区中的偏移 = `X - 16` |
| `Y` | 文件块总长度（含 4 字节 usize 前缀） |
| `Z` | 常量 1（未使用） |
| `A` | 未使用（hash/ID，与配对无关） |
| `L` | 路径字节长度 |

### 2.4 ⚠️ 路径与记录的配对规则（关键）

**`path[i]` 与 `rec[i]` 不是按位置一一对应！**

正确规则：
> 将 paths 按**路径字节序（`bytes` 字典序）** 排序后，第 `k` 个排序后的路径对应第 `k` 个 record。

```python
order = sorted(range(N), key=lambda i: paths[i])   # 路径排序
for k in range(N):
    p = paths[order[k]]     # 排序后的第 k 个路径
    r = recs[k]             # ⚠️ 用 k 索引 records（records 不排序）
```

**错误做法**（会得到一堆错位的文件）：
```python
p = paths[k]        # ❌ 按位置取 path
p = paths[order[k]] # ❌ 排序 path 但 records 也排序
```

**症状**：`anticrack.csv` 里存的是 `AILuaLookIdle.lua` 的内容——文件名和内容完全对不上。

### 2.5 文件提取流程

```
1. 读取 16 字节头
2. 从 index_offset 读 LZ4 索引块，解压
3. 解析 N 条 record + N 条 path
4. paths 按字节序排序，与 records 位置配对
5. 对每个 (path, record):
   - 从数据区 offset = X - 16 处取 Y 字节
   - 校验 MD5（针对压缩块）
   - 从 [u32 usize][LZ4 block] 结构中解压
   - 按路径写入磁盘
```

---

## 3. Lua 字节码格式

### 3.1 头部（12 字节，标准 Lua 5.1）

```
1B 4C 75 61 51 00 01 04 04 04 08 00
│  └──"Lua"──┘ │  │  │  │  │  │  └─ num_is_integral = 0
│              │  │  │  │  │  └──── sizeof(lua_Number) = 8
│              │  │  │  │  └─────── sizeof(Instruction) = 4
│              │  │  │  └────────── sizeof(size_t) = 4
│              │  │  └───────────── sizeof(int) = 4
│              │  └──────────────── endianness = 1 (little)
│              └─────────────────── format = 0
└────────────────────────────────── escape = 0x1B
   版本号 0x51 = 'Q' = Lua 5.1
```

**`LuaQ` 中的 `Q` 只是 Lua 5.1 的版本标识，不是加密标识。**

### 3.2 opcode 重映射

游戏**修改了 Lua 5.1 的 opcode 表**——只影响 `0x0A` 到 `0x14` 一段：

| 标准 opcode | 游戏实际 | 指令 |
|---|---|---|
| 0-9 | 0-9 | move / loadk / loadbool / loadnil / getupval / getglobal / gettable / setglobal / setupval / settable |
| 12 (ADD) | **10** | add |
| 13 (SUB) | **11** | sub |
| 14 (MUL) | **12** | mul |
| 15 (DIV) | **13** | div |
| 16 (MOD) | **14** | mod |
| 17 (POW) | **15** | pow |
| 18 (UNM) | **16** | unm |
| 19 (NOT) | **17** | not |
| 20 (LEN) | **18** | len |
| 10 (NEWTABLE) | **19** | newtable |
| 11 (SELF) | **20** | self |
| 21-37 | 21-37 | concat / jmp / eq / ... / vararg |

**映射表**（游戏 opcode → 标准 opcode）：

```python
GAME_TO_STD = list(range(64))
GAME_TO_STD[10] = 12; GAME_TO_STD[11] = 13; GAME_TO_STD[12] = 14
GAME_TO_STD[13] = 15; GAME_TO_STD[14] = 16; GAME_TO_STD[15] = 17
GAME_TO_STD[16] = 18; GAME_TO_STD[17] = 19; GAME_TO_STD[18] = 20
GAME_TO_STD[19] = 10; GAME_TO_STD[20] = 11
```

### 3.3 获取映射的方法（逆向流程）

1. 定位游戏的 `liblua.dll`（Windows）/ `liblua.so`（Android）
2. 用 Ghidra 加载，Analyze All
3. 找最大的函数（约 5600 字节）—— **几乎肯定是 `luaV_execute`**
4. 该函数是一个巨大 `switch (instr & 0x3F)`，逐个 `case` 的实现对照标准 Lua 5.1 `lvm.c`
5. 记录每个 case 编号对应的真实指令，生成映射

**`luaV_execute` 特征**：
- 是 DLL 里最大的函数之一
- 反编译开头有 `switch (x & 0x3f)`，38+ 个 case
- 每个 case 里都在操作 `L->base`、`L->ci`、寄存器数组
- 有个大循环 `while(1)` / `for(;;)`

### 3.4 字符串转义

Lua 字节码里的中文字符串以 `\ddd`（十进制转义）形式存储，例如：

```
\229\133\165\229\143\163\233\133\141\231\189\174\229\138\160\232\189\189\229\174\140\230\136\144
```

这是 UTF-8 字节的十进制转义。还原为：`入口配置加载完成`

---

## 4. 反编译流程

### 4.1 整体架构

```
script_res.pkg
     │
     │  unpack_script_res.py
     ▼
test/                        ← Lua 字节码 (游戏 opcode)
     │
     │  batch_decompile.py
     │    阶段 D: --opmap       (unluac 读 opmap.txt)
     │    阶段 A: swap          (物理改 opcode 为标准值)
     │    阶段 B: swap + patch  (修 maxstacksize)
     │    兜底:  空文件
     ▼
test_lua_src/                ← 反编译成功的 Lua 源码 (中文仍是 \ddd)
     │
     │  decode_escapes.py
     ▼
test_lua_src_decoded/        ← 最终可读源码 (中文正常显示)
     │
     │  merge_resources.py
     ▼
merged/                      ← 反编译 lua + 原始资源 (完整目录)
```

### 4.2 反编译阶段详解

`batch_decompile.py` 采用**多阶段降级策略**，每个文件依次尝试：

#### 阶段 D: `--opmap`

直接用 unluac 的 `--opmap opmap.txt` 参数，让 unluac 按自定义 opcode 表解释。

**优点**：保留变量名、函数名、行号。**覆盖 99% 的文件**。

#### 阶段 A: `swap opcode`

把字节码里的游戏 opcode **物理修改**为标准 Lua 5.1 opcode，然后 unluac 用**默认模式**处理。

**优点**：走标准路径的 unluac 更健壮，某些 `--opmap` 会崩的文件在 swap 后能过。

#### 阶段 B: `swap + patch maxstacksize`

对某些文件，unluac 因 `maxstacksize` 字段偏小（导致寄存器数组越界）而崩溃。此阶段：
- swap opcode
- 遍历所有函数，把 `maxstacksize` 改成 `max(原值, 用到的最大寄存器号 + 1)`

#### 兜底: 空文件

三阶段都失败或输出为"骨架"时，写 0 字节空文件。

### 4.3 骨架判定

某些空逻辑文件（如 `hotfix_temp.lua`），字节码只有一两条 `RETURN`，unluac 输出无意义的 `local L0_1, L1_1`。判定条件：

- 输出 < 500 字节
- 每个非空行匹配 `local Lx_x[, Ly_y]*` / `return` / `-- 注释`

满足则视为骨架，写 0 字节空文件。

### 4.4 输出结构

最终产出：
- **6092 个 .lua 文件**（对应原始 pkg 里的 6092 条 `.lua` 路径）
- 其中：
  - 大部分走 `D_opmap` / `A_swap` / `B_patch` → 完整 Lua 源码
  - 少数空逻辑文件 → 0 字节（stage=`empty`）
  - 非字节码文件（明文 Lua 或数据）→ 原样复制（stage=`copy`）

注意：pkg 索引里的记录数是 8172，但其中只有 **6092 条路径**以 `.lua` 结尾。
其余是 `.csv` / `.json` / `.xml` / 图片等资源，不在反编译范围内。

`test_lua_src/` 目录结构：

```
test_lua_src/
├── _manifest.txt          # 每个文件走的哪个阶段
├── script/                # 与 pkg 相同的目录结构
│   ├── gameenum.lua
│   └── luascript/
│       └── class.lua
└── commonresource/
    └── ...
```

`_manifest.txt` 每行格式：

```
stage	source	output	note
D_opmap	test\script\gameenum.lua	test_lua_src\script\gameenum.lua
A_swap	test\...\filtermgr.lua	test_lua_src\...\filtermgr.lua
empty	test\script\hotfix_temp.lua	test_lua_src\script\hotfix_temp.lua
copy	test\...\accelkeysf12_init.lua	test_lua_src\...\accelkeysf12_init.lua
```

阶段含义：
| stage | 说明 |
|---|---|
| `D_opmap` | 原文件 + `--opmap` |
| `A_swap` | swap opcode + 默认模式 |
| `B_patch` | swap + patch maxstacksize |
| `empty` | 骨架或彻底失败 → 0 字节空文件 |
| `copy` | 非字节码（明文 Lua 或数据文件）→ 原样复制 |
| `read-error` / `fix-error` | 读写或转换异常 |

---

## 5. 脚本清单

### 5.1 `unpack_script_res.py`

**功能**：解包 `script_res.pkg` 为单个 `.lua` 字节码文件。

**用法**：
```bash
python unpack_script_res.py script_res.pkg test
#                            ↑ 输入 pkg    ↑ 输出目录
```

**核心逻辑**：
1. 读 16 字节头，得到 `index_offset` / `index_size`
2. 从 `index_offset` 读 LZ4 压缩的索引，解压
3. 解析 `u32 N` + N 条 `[MD5][X][Y][Z]` 记录 + N 条 path
4. **对 paths 排序，与 records 位置配对**
5. 对每个文件：从数据区读 Y 字节 → 校验 MD5 → 解压 `[usize][LZ4]` 块 → 按路径写入

**输出**：`test/` 目录（含 8172 个文件：6092 个 lua 字节码 + 2080 个资源）+ `_unpack_report.json`（报告，不属于 pkg 内容）

### 5.2 `batch_decompile.py`

**功能**：批量把 Lua 字节码反编译为 Lua 源码。

**用法**：
```bash
python batch_decompile.py test test_lua_src
#                          ↑ 输入  ↑ 输出
```

**依赖**：
- `unluac_2025_12_23.jar`（同目录）
- `opmap.txt`（同目录，可选，缺失会跳过阶段 D）
- Java 环境

**多阶段降级**：D_opmap → A_swap → B_patch → 空文件

**输出**：`test_lua_src/`（6092 个 lua 源码）+ `_manifest.txt`（阶段报告）

### 5.3 `decode_escapes.py`

**功能**：把 Lua 源码字符串字面量内的 `\ddd` 十进制转义还原为 UTF-8 字符。

**用法**：
```bash
# 输出到新目录（推荐，先检查）
python decode_escapes.py test_lua_src test_lua_src_decoded

# 原地替换
python decode_escapes.py test_lua_src

# 也处理纯 ASCII 的 \ddd（默认只处理非 ASCII）
python decode_escapes.py test_lua_src out --all
```

**状态机解析**：
- 只处理 `"..."` 和 `'...'` 字符串**内部**的转义
- `[[...]]` / `[=[...]=]` 长字符串不处理
- `--` 行注释和 `--[[ ... ]]` 块注释不处理
- `\"` `\'` 转义引号正确跳过
- 连续 `\ddd` 一起 UTF-8 解码（如 `\229\133\165` → `入`）
- UTF-8 解码失败或含 `\0` 时保留原样

### 5.4 `merge_resources.py`

**功能**：把原始 pkg 解包目录（含 `.csv` / `.json` / `.xml` / 图片等）和反编译后的 lua 目录合并为一个完整资源目录。

**用法**：
```bash
# 输出到新目录（推荐，可对比）
python merge_resources.py <原始解包目录> <反编译目录> [输出目录]

# 示例
python merge_resources.py test test_lua_src_decoded merged

# 原地合并（用反编译 lua 覆盖原始目录的 lua 字节码）
python merge_resources.py test test_lua_src_decoded
```

**合并规则**：

| 原始文件 | 反编译目录是否有对应 | 处理 |
|---|---|---|
| `.lua` (字节码) | ✅ 有 | 用反编译后的源码替换 |
| `.lua` (字节码) | ❌ 无 | 保留原始字节码（列入报告）|
| `.csv` / `.json` / `.xml` / 图片等 | — | 直接复制 |
| `_unpack_report.json` (解包报告) | — | 跳过（不属于游戏资源） |
| `_manifest.txt` (反编译阶段报告) | — | 跳过（不属于游戏资源） |

**文件数量对照**：

| 项目 | 数量 |
|---|---|
| pkg 索引记录数 | 8172 |
| 其中 `.lua` 文件 | 6092 |
| 其中非 lua 资源 (csv/json/xml/图片等) | 2080 |
| 反编译产出 lua 源码 | 6092 |
| **合并后目录文件总数** | **8172** |

> **说明**：合并后得到 **8172 个文件** = 6092 个反编译 lua + 2080 个原始资源。
> 解包脚本自动生成的 `_unpack_report.json` 是报告文件，不属于 pkg 内容，合并时忽略。

### 5.5 `opmap.txt`

**功能**：unluac 的 opcode 映射表。

**格式**：`.op <opcode值> <标准指令名小写>`

**当前内容**（国际版 1.7.15）：

```
.op 0  move
.op 1  loadk
.op 2  loadbool
.op 3  loadnil
.op 4  getupval
.op 5  getglobal
.op 6  gettable
.op 7  setglobal
.op 8  setupval
.op 9  settable
.op 10 add
.op 11 sub
.op 12 mul
.op 13 div
.op 14 mod
.op 15 pow
.op 16 unm
.op 17 not
.op 18 len
.op 19 newtable
.op 20 self
.op 21 concat
.op 22 jmp
.op 23 eq
.op 24 lt
.op 25 le
.op 26 test
.op 27 testset
.op 28 call
.op 29 tailcall
.op 30 return
.op 31 forloop
.op 32 forprep
.op 33 tforloop
.op 34 setlist
.op 35 close
.op 36 closure
.op 37 vararg
```

**注意**：unluac 的 `--opmap` 要求指令名**小写**（`move` 而非 `MOVE`）。

---

## 6. 完整使用示例

### 6.1 一键流程

```bash
# 1. 解包 pkg (得到 8172 个文件, 含 lua 字节码 + 各类资源)
python unpack_script_res.py script_res.pkg test

# 2. 批量反编译 (得到 6092 个 lua 源码)
python batch_decompile.py test test_lua_src

# 3. 转义还原 (中文从 \ddd 恢复)
python decode_escapes.py test_lua_src test_lua_src_decoded

# 4. 合并资源 (反编译 lua + 原始资源 = 完整目录, 共 8172 个文件)
python merge_resources.py test test_lua_src_decoded merged
```

### 6.2 单个文件调试

```bash
# 只反编译一个文件
java -jar unluac_2025_12_23.jar --opmap opmap.txt test/script/gameenum.lua

# 反汇编查看 opcode
java -jar unluac_2025_12_23.jar --opmap opmap.txt --disassemble test/script/gameenum.lua

# 单文件转义还原
python decode_escapes.py test_lua_src/script/gameenum.lua out.lua
```

### 6.3 最终产物

```
merged/                       ← 8172 个文件
├── script/
│   ├── gameenum.lua          ← 反编译 + 中文还原
│   ├── hotfix_temp.lua       ← 0 字节 (骨架)
│   └── luascript/
│       └── class.lua
├── commonresource/
│   ├── script/
│   │   ├── csvdef/utf8/*.csv ← 原始 CSV
│   │   ├── json/*.json       ← 原始 JSON
│   │   └── luascript/*.lua   ← 反编译源码
│   └── ui/mobile/*.xml       ← 原始 XML
└── ...
```

可以直接给任何 Lua 编辑器 / IDE 加载、运行、分析——lua 是源码，其它是原始资源，路径结构和游戏内完全一致。

---

## 7. 关键技术原理

### 7.1 为什么"LuaQ"不是加密？

`LuaQ` 是标准 Lua 5.1 字节码的头部标识，`Q = 0x51 = 81 = 'Q'`，是版本号。

证据：
- 文件头字节 `1B 4C 75 61 51` 完全匹配标准 Lua 5.1 签名
- unluac 能直接读入（说明结构合法）
- 反汇编输出的常量表、指令流完全可读
- 明文路径 `F:/RainbowMiniwMastPc/...` 在字节码里直接可见

### 7.2 为什么 `(not class)` 是 opcode 重映射的产物？

`class.lua` 源码里应该有：
```lua
local class = _G.class
local BuddyDetail = {}
BuddyDetail.uin = 0
```

但 unluac 输出：
```lua
local class = _G.class
;(not class).uin = 0
```

**原因**：
- 游戏把 `NEWTABLE` 的 opcode 从 10 改成了 19
- unluac 用标准表读，把 `19` 解释成 `NOT`
- 输出 `(not xxx)` 或 `(#xxx)`

**修复**：swap opcode 或 `--opmap`。

### 7.3 `maxstacksize` 为什么会导致崩溃？

Lua 5.1 每个函数头部有一个字节 `maxstacksize`，告诉虚拟机为该函数分配多少寄存器槽位。

游戏编译时可能：
- 把内层函数的 `maxstacksize` 统一改成偏小的值
- 运行时引擎用动态扩容绕过

unluac 严格按 `maxstacksize` 分配数组，遇到超出就 `ArrayIndexOutOfBoundsException`。

**修复**：扫描每条指令的实际使用寄存器，把 `maxstacksize` 提高到安全值。

### 7.4 为什么 `paths` 需要排序？

**实验数据**（国际版 1.7.15，8172 个文件）：

| 配对策略 | 类型匹配率 |
|---|---|
| `path[k] ↔ rec[k]`（不排序） | 3.8% |
| `path[order[k]] ↔ rec[k]`（排序） | **99.2%** |
| 其他排序组合 | 都 < 71% |

**推断**：游戏引擎内部维护一个按路径名排序的文件索引表，写盘时按排序后的顺序输出文件块。所以读取时也要按同样规则重新配对。

### 7.5 `\ddd` 转义的还原

Lua 字符串支持 `\ddd` 十进制转义。中文字符的 UTF-8 编码为 3 字节：

```
"入口" = \xe5\x85\xa5\xe5\x8f\xa3  (UTF-8 字节)
       = \229\133\165\229\143\163  (十进制转义)
```

逐字节 UTF-8 解码即可还原为中文。**必须连续 `\ddd` 一起解码**，否则单独一个 `\229` 无法构成有效字符。

---

## 8. 常见问题

### Q1: `unluac` 报 `bad header in precompiled chunk`

**答**：`luac5.1.exe` 等工具对头部校验严格，容易拒绝自定义格式。**改用 unluac.jar**（容忍度更高）。

### Q2: `unluac` 报 `bad code in precompiled chunk`

**答**：luadec 或严格版 luac 遇到未知 opcode 的报错。用 unluac + `--opmap` 处理。

### Q3: `unluac` 报 `ArrayIndexOutOfBoundsException`

**答**：`maxstacksize` 字段偏小导致寄存器越界。用 `batch_decompile.py` 的 B_patch 阶段修复。

### Q4: 输出全是 `(not xxx)` / `(#xxx)`

**答**：opcode 重映射问题。确保 `opmap.txt` 存在且 `batch_decompile.py` 走的是 D_opmap 或 A_swap 阶段。

### Q5: 输出是 `.version 5.1` 开头的反汇编

**答**：`--disassemble` 参数或 unluac 遇到无法反编译的字节码时输出反汇编（以 `.version` 开头）。

本项目实际数据里**没有文件落到这一步**——所有能出的 lua 都正常反编译成源码了。`_is_disasm()` 检查就是为防止把这种输出误当源码写入磁盘。

### Q6: 输出是 `local L0_1, L1_1` 这种空壳

**答**：原始字节码就是空函数（只有 `RETURN`）。这类文件按骨架处理，写 0 字节空文件。

### Q7: 中文显示为 `\229\133\165`

**答**：需要跑 `decode_escapes.py` 转义还原。

### Q8: 文件大小对得上但内容错乱

**答**：路径配对错误。必须用 `paths[order[k]] ↔ recs[k]`（paths 排序，records 不排序）。

### Q9: `_manifest.txt` 里一堆 `copy`

**答**：那些文件不是 Lua 字节码（可能是明文 Lua 或数据文件），被原样复制。打开看看内容。

### Q10: `decode_escapes.py` 改坏了文件

**答**：先跑不带输出目录的模式看看统计，或用 `--inplace` 前先备份。若单引号字符串里的 `\'` 被误处理，反馈具体文件。

---

## 附录 A: manifest 状态码速查

| Stage | 含义 | 输出内容 |
|---|---|---|
| `D_opmap` | unluac --opmap 成功 | 完整源码 |
| `A_swap` | swap opcode 后默认模式成功 | 完整源码 |
| `B_patch` | swap + patch maxstack 成功 | 完整源码 |
| `empty` | 骨架或彻底失败 | 0 字节空文件 |
| `copy` | 非字节码文件 | 原样复制 |
| `read-error` | 读取源文件失败 | 无 |
| `fix-error` | 字节码转换失败 | 无 |

## 附录 B: 关键命令速查

```bash
# 解包
python unpack_script_res.py <pkg> <outdir>

# 反编译
python batch_decompile.py <indir> <outdir>

# 转义还原
python decode_escapes.py <indir> <outdir>
python decode_escapes.py <indir>              # 原地替换
python decode_escapes.py <indir> <outdir> --all  # 也处理纯ASCII

# 合并资源
python merge_resources.py <原始目录> <反编译目录> [输出目录]

# 单个文件
java -jar unluac_2025_12_23.jar --opmap opmap.txt <file>
java -jar unluac_2025_12_23.jar --opmap opmap.txt --disassemble <file>

# 查 manifest
findstr /C:"D_opmap" <outdir>\_manifest.txt    # Windows
grep "D_opmap" <outdir>/_manifest.txt          # Linux/Mac
```

## 附录 C: 字节码结构参考

标准 Lua 5.1 函数在字节码中的结构：

```
[source 字符串: u32 长度 + 内容]
[u32 linedefined]
[u32 lastlinedefined]
[u8 nups]           # upvalue 数量
[u8 numparams]      # 参数数量
[u8 is_vararg]      # 是否变参
[u8 maxstacksize]   # ← B_patch 阶段修改这个
[u32 ncode]
[Instruction * ncode]                # 指令数组
[u32 nconst]
[常量列表，每个: u8 type + 数据]
  type 0 = nil
  type 1 = bool (u8)
  type 3 = number (f64)
  type 4 = string (u32 len + bytes)
[u32 nproto]
[嵌套函数 (递归相同结构)]
[u32 nlineinfo]
[u32 * nlineinfo]                    # 行号表
[u32 nlocvars]
[local 变量表]
[u32 nupvalues]
[upvalue 名列表]
```

## 附录 D: 已知限制

- **国际版 1.7.15 专属**：其他版本的 `liblua.dll` opcode 映射可能不同，`GAME_TO_STD` 需重新逆向
- **`decode_escapes.py` 不处理**：十六进制转义 `\xNN`、`\u{...}`（Lua 5.1 也不支持）
- **`liblua.dll` 未 strip**：如果未来版本 strip 了符号，需通过字符串/xref 定位 `luaV_execute`
- **空逻辑文件**（如 `hotfix_temp.lua`）：字节码本身只有一两条 `RETURN`，反编译结果是无实质内容的骨架，按规则写 0 字节空文件

---

*文档版本: v1.0*  
*针对游戏: Mini World (迷你世界) 国际版 1.7.15*  
*逆向目标: `script_res.pkg` + Lua 5.1 字节码*