# 协议层与本地读取审查报告（slug=protocol-reader）

审查范围：`atst/protocol/`、`atst/codec/`、`atst/charset/`、`atst/reader/`、`atst/profile/`、`atst/domain/`、`atst/universe/`、`atst/batch.py`。
方法：逐文件读实现，关键结论以一手代码（文件:行号 + 引文）为据；凡运行过验证的结论标注「已验证」。审查日期 2026-11-22，代码版本 v2.1.0 工作区快照。

---

## 1. 结论摘要

L1/L2/L3 三级解析的**接缝在结构上是清楚的**（dispatch → parser → generic → L3 raw，失败降级路径完整），但接缝的**账目分裂在三处**（commands 账本、register_parser 装饰器、BaseParser.TIER），置信度由 tier 常量推导（L1 恒 1.0 / L2 恒 0.9）而非由实际校验产出，账本与解析器的 tier 已经出现口径漂移。**最重的两个实际问题都在正确性/生产路径上**：① 分派热路径上 ProtocolSniffer 对每个 L2/L3 响应同步落盘（持全局锁、相对 cwd 建目录）；② `BaseFileReader._prepare` 捕获所有异常继续用默认档案，直接违背 `reader/profile.py` 自己写下的「探测失败绝不静默猜测」契约，错误 price_scale 会静默产出 100 倍错价。字符集探测存在**两套优先级相反的实现**；profile 探测存在**两套互不调用的六步管线**。GBK 处理上，`decode_gbk`/`detect_encoding` 的「GBK 优先 + 可打印比例过滤」对汉字文本无效（乱码汉字同样可打印），UTF-8 中文会被静默解成 GBK 乱码。

整体置信：高（每条发现均有代码位置；H1/H3 的行为已按代码路径推演，H2 为契约与实现的直接冲突）。

---

## 2. 发现清单

### 2.1 高严重度

| 编号 | 类别 | 位置 | 问题 | 证据与改法 |
|---|---|---|---|---|
| H1 | 架构+细节 | `atst/protocol/registry.py:435-437`、`atst/protocol/generic.py:305-328` | ProtocolSniffer 归档接在 `_generic_or_raw` 的**生产分派路径**上：每个 L2/L3 响应都同步写 `.bin` + `.meta.json`，持模块级全局锁 `_ARCHIVE_LOCK`，默认根 `PROTOCOL_SPEC/_sniffer` 相对进程 cwd。 | 引文（registry.py:435）：`get_sniffer().archive(frame, result, family)`，外层仅 `except Exception: pass`。generic.py:305 默认 `root="PROTOCOL_SPEC/_sniffer"`，:328 `bin_path.write_bytes(frame.payload)`。未验证命令（0x000F/0x0010/0x044D 等）每次响应都落盘；批量跑未知命令时是每响应两次文件 IO + 锁竞争。**改法**：归档默认关闭或异步队列（内存 ring + 后台线程批量刷盘），root 改由配置注入绝对路径；至少给 `ProtocolSniffer(enabled=...)` 接到配置面而不是构造默认值。 |
| H2 | 细节（正确性） | `atst/reader/formats.py:160` vs `atst/reader/profile.py:30` | `_prepare` 捕获**所有**异常后「使用默认档案」继续解码，违背同模块写下的契约「探测失败（置信度 < 0.5）时抛 ProfileUndetectable，**绝不静默猜测**」。 | formats.py:160 引文：`except Exception as exc:  # ProfileUndetectable` → `self.warnings.append(f"自动探测失败，使用默认档案: {exc}")`。默认档案 `a_share_day` 是 `price_scale=100, uint32`（reader/profile.py `BUILTIN_PROFILES`）。一份 scale=1000 的文件探测失败后按 ×100 解码，价格放大 10 倍且只在 warnings 里留痕。**改法**：`strict=True`（默认）时让 `ProfileUndetectable` 上抛；`strict=False` 才降级默认档案并告警。 |
| H3 | 架构 | `atst/codec/primitive.py:558-589` vs `atst/charset/encoding.py:66` | 字符集探测**两套实现、优先级相反**：primitive 为 `("gbk", "gb18030", "big5", "utf-8")`（GBK 优先），charset 模块为 `("utf-8", "gbk", "gb18030", "big5")`（UTF-8 优先）。charset/encoding.py 模块头自己承认「边界样本（合法 UTF-8 的中文文本）结论相反」。 | primitive.py:589 `detect_encoding` 注释称「UTF-8 多字节序列误判为中文 GBK 的概率较低」——不成立：GBK 解码空间极大，UTF-8 中文几乎总能被 GBK 成功解码为乱码，而 `_printable_ratio`（primitive.py:613）用 `ch.isprintable()`，乱码汉字同样是可打印 CJK 字符，过滤失效。生产链（parsers、BlockReader、generic 尾巴解码）全走 primitive 版；charset 版生产零调用（全仓仅 `tests/i18n/test_encoding.py`、`tests/test_bridges.py` 引用）。**改法**：删掉 charset 模块的独立探测或把它降级为 primitive 的薄封装；`decode_gbk` 对「多编码都可解」的情形要求调用方显式传 encoding，不做静默择优。 |

### 2.2 中严重度

| 编号 | 类别 | 位置 | 问题 | 证据与改法 |
|---|---|---|---|---|
| M1 | 架构 | `atst/protocol/commands.py:163-177`、`atst/protocol/parsers/_std7709_quote.py:47-53`、`atst/protocol/registry.py:261` | L1/L2/L3 的 tier **三处声明**：commands 账本 `tier`/`verified`/`status`、`@register_parser(tier=...)`、`BaseParser.TIER`。`ParseResult.confidence` 由 tier 常量推导：`confidence=1.0 if self.TIER == TIER_L1 else 0.9`（registry.py:261）——L2 解析器哪怕解出的是按公开资料猜的布局也自报 0.9（≥0.5 永不回落 L2 通用）。账本与解析器已漂移：0x053E/0x054C 账本记 `TIER_DECLARED`（commands.py:163,170），解析器却注册 `tier="L2"`。 | 引文（registry.py:261）。一致性只靠 `tests/protocol/test_registry_spec_verification_alignment.py` 事后比对 spec↔账本，**没有**账本↔解析器 TIER 的结构校验。**改法**：tier 单一事实源放在账本，`register_parser` 从账本读 tier（或注册时校验一致，不一致即 raise）；L2 解析器的 confidence 不应恒 0.9，至少让「结构守卫未触发但布局未 golden 锁定」反映到 0.7~0.8 区间。 |
| M2 | 架构 | `atst/reader/profile.py:409` vs `atst/profile/detect.py` | 两套六步探测器「共享 DataProfile 但互不调用」（reader/profile.py:27 自述）。候选记录长度表 `_RECORD_SIZES = (32, 29, 24, 16, 8, 4)` 两边各抄一份；字段探针（u32/f32 采样 offsets 4/8/12/16）、日期三编码投票、`_sample_records` 抽样逻辑基本同构，但权重不同且 detect.py 的文档权重与实现不符。 | detect.py:36 文档写「加权（0.20 / 0.25 / 0.25 / 0.30）」，实现是 `0.20*ev1 + 0.20*rs_score + 0.25*ev3 + 0.35*ev4`（detect.py:633）。reader 版权重又是另一套 `0.20/0.25/0.20/0.15/0.20`（reader/profile.py `detect()` 内）。**改法**：抽公共打分内核（候选长度→字段探针→日期投票），两个入口只保留权重与输出形态（单档案 vs 带证据排名）的差异；文档权重改为从代码常量生成或直接引用。 |
| M3 | 架构 | `atst/protocol/handshake.py:71-95`、`atst/protocol/prober.py:84,473` | 握手帧是**裸 hex 魔数**，完全绕过 `framing.build_request`/`FrameSpec`：`_MAGIC_STEP1 = bytes.fromhex("0c0218930001030003000d0001")`。帧布局（zip_flag/seq/pkg_len 偏置）改了握手不会跟。Prober 又独立硬编码 `_RESPONSE_HEADER_LEN = 16` 与 magic `0x0074CBB1` 校验（prober.py:84,473），不复用 `FrameSpec.resp_header_size`。 | 引文 handshake.py:71。framing.py 明确把头长做成 `resp_header_size` 属性并删了手抄常数（framing.py FrameSpec 注释），prober 却保留第二份口径。**改法**：握手帧改用 `build_request(0x000D, b"\x01", seq=固定值)` 组装；prober 的剥头逻辑改用 `DEFAULT_7709_SPEC.resp_header_size` 与 `spec.magic`。 |
| M4 | 架构 | `atst/protocol/parsers/`（goods.py 12 处、mac.py 13 处、std7727.py 8 处、std7709_extra.py 11 处、_std7709_bars.py 2 处） | 解析器样板重复：`count = self.guarded_count(...)` + `for _ in range(count): if reader.remaining < size: break` 的定长记录循环在全仓 **40+ 处**逐字重复；`_check_echo` 在 `CapitalChangesParser`（_std7709_bars.py:447）与 `FinanceInfoParser`（:646）各复制一份，仅命令号文案不同。 | 引文 goods.py:63-71 典型循环。**改法**：BaseParser 增设 `iter_fixed_records(reader, ctx, record_size)` 返回 `(count, Iterator[bytes])`，把钳制、截断告警、§2-17 自检收进框架；`_check_echo` 提为 BaseParser 或 `_std7709_common` 的模块函数（参数化命令号）。 |
| M5 | 架构 | `atst/domain/models.py`、`atst/domain/records.py:1-318`、`atst/client/core.py:287`、`atst/reader/formats.py:544` | 领域模型**双轨**：models.py（Bar/Quote/Tick/CapitalChange，reader 与 sources 用）与 records.py（9 个 v14 Record，供 web/session 层）。records.py 每个 Record 手写近乎相同的 `to_dict`/`from_dict`（`_clean_none` + metrics 合并 + 键集过滤），9 份样板无基类。协议解析器全部输出 `list[dict]`，client 层再手工转换（`_row_to_capital`，core.py:287）——L1 解析结果与领域模型之间没有类型化接缝。FinanceReader 还把财务指标塞进 `Bar(datetime=..., extra=dict(d))`（formats.py:544），datetime 置空，语义错位。 | 引文 core.py:287 `def _row_to_capital(row: Mapping[str, Any]) -> CapitalChange`。**改法**：records.py 抽 `to_dict`/`from_dict` 基类（声明字段集 + metrics 键）；解析器对已 golden 锁定的命令（0x0530/0x052D/0x0537/0x0FC5）直接产出领域模型或在 client 层统一转换器表；gpcw 语义化输出改用 records.FinancialRecord 而非 Bar。 |
| M6 | 细节（正确性） | `atst/reader/formats.py:219` vs `:141-158` | `DayBarReader._decode_records` 固定 `size = self.RECORD_SIZE`（类属性 32），而 `_prepare` 探测出的 `record_size` 被合并进 profile 却**无人消费**。探测器支持 29/24/16 字节候选（reader/profile.py `RECORD_SIZES`），解码层永远按 32 切。 | 引文 formats.py:219 `size = self.RECORD_SIZE`。若文件真是 29 字节旧日线布局，探测得分再高也按 32 错位解码。**改法**：`_decode_records` 改用 `p.record_size or self.RECORD_SIZE`；record_size 与 32 不符时告警或拒绝。 |
| M7 | 架构 | `atst/profile/detect.py:413-427,633` | 690 行启发式可控性弱点：① `_build_candidate_profile` 里 `if period in (分钟族): asset_class = STOCK else: asset_class = STOCK`——两分支同值，死分支；② evidence 链不完整：`ev3/ev4` 只在候选刷新最优时 `evidence.extend`，落选候选的探针证据不进链（candidates 里只有分数）；③ presets 命中给首要候选 +0.1 置信（detect.py 主循环），`_match_preset_name` 只按 market_id 粗匹配，SH_A/SH_FUND/SH_BOND 同为 market_id=1，恒返回字典序第一个；④ `_price_scale_sanity` 只覆盖 `scale==100 且中位数/100>5000` 改判 ×1000 的单一情形。 | 引文 detect.py:425/427 两支同为 `AssetClass.STOCK`。**改法**：删死分支；每个候选的 ev3/ev4 无条件入 evidence；preset 匹配带代码前缀（现在只有 market_id，等于没匹配）；sanity 规则表驱动。 |

### 2.3 低严重度

| 编号 | 类别 | 位置 | 问题 | 证据与改法 |
|---|---|---|---|---|
| L1 | 细节 | `atst/protocol/generic.py:88-110` | `_looks_like_uint16_date` 全库零调用点，注释自认「保留并标注」——死代码占着 L2 候选启发位。 | 删除或真正接入 `_classify_chunk` 的 uint16 日期分支。 |
| L2 | 细节 | `atst/protocol/prober.py:416` | `self._total_probes += 1` 在 `_rate_lock` **外**自增，多线程并发探测时计数失真（限速本身有锁，计数没有）。 | 移入 `_enforce_rate` 的锁内，或改 `threading.Lock` 保护的计数器。 |
| L3 | 细节 | `atst/reader/formats.py:549-556` | `FinanceReader._detect_fields` 按 `FIELD_COUNTS` 升序取第一个整除者：28 优先。30 字段 × 14 条记录的文件（n=1680）同时是 112 的倍数，会被误判为 28 字段。回退分支 `max(1, n//4)` 把不可整除的文件整体当一条记录。 | 候选按「整除且余数为 0 的最大字段数」或要求文件头确认；不可整除时抛 `FileFormatError` 而非静默单条。 |
| L4 | 细节 | `atst/reader/formats.py:244-258` vs `:216-241` | `MinBarReader` 逐记录 `_iter_records` 切片（每条拷贝 32 字节临时 bytes），`DayBarReader` 已改 `unpack_from` 原地解码——同模块两套解码姿势，分钟线大批量时多一次拷贝。 | 分钟线改用与日线相同的 `_decode_record_at` 原地解码。 |
| L5 | 细节 | `atst/protocol/registry.py:362` | L1 失败降级时 `fallback = _generic_or_raw(...)`，若置信度低于 `min_confidence` 会 raise `LowConfidenceParse`——原始 L1 异常信息（err_msg 尚未插入 warnings）丢失，调用方只看到置信度不足。 | 先构造 fallback 再判阈值，或在 LowConfidenceParse 的 context 里带上 L1 错误。 |
| L6 | 细节 | `atst/codec/primitive.py` BinaryWriter | `uint8/uint16/uint32` 用 `v & 0xFF` 掩码静默截断越界值，与 BinaryReader 的越界抛错不对称。 | 掩码改校验（越界抛 ValueError），与 `_require_uint` 口径一致。 |
| L7 | 细节 | `atst/reader/formats.py:_decode_datetime` | 日线日期非法（如损坏记录）时静默回退 datetime32 位域重解，产出**另一个错误日期**而非报错；`get_datetime_from_lc` 也不校验 day≤31。 | 非法日期记 warning 并保留原始整数，或 strict 下抛错。 |
| L8 | 细节 | `atst/batch.py:105-107` | `BatchResult.__post_init__` 对 items/errors/meta 各做一次 `copy.deepcopy`——大批量行情每构造一个审计结果都全量深拷贝，热路径开销。 | items 已是不可变 BatchItem 时可浅拷贝 + `MappingProxyType`；仅对可变 meta 保留深拷贝。 |
| L9 | 架构 | `atst/tools/codegen.py` | codegen 与手写代码的边界：生成物只是 **stub 骨架**，写入 `atst/tools/generated_draft/_generated.py`「草稿区，不进包内」，不覆盖已有解析器。即全部 L1 解析器实为手写，spec↔实现漂移只靠测试拦截，codegen 在主链路上是断头的。 | 二选一：让 codegen 产物真正成为 D→L2 的落地路径（生成可注册的 L2 解析器进包），或明确降级为「脚手架生成器」并从架构文档的「协议全覆盖」叙事中摘除。 |

### 2.4 已排查未发现问题

- `encode_leb128`/`decode_leb128` 有符号往返：**已验证** `range(-3000, 3001)` 全部往返无损（含多字节负数），不存在符号位截断问题。单测只覆盖 `{0,±1,±32,±8192}`，建议补 -100 这类中间量，但不是缺陷。
- `count_guard` 钳制、`guarded_count`、§2-17 截断自检、回声校验（0x0530/0x000F/0x0010）构成畸形流防线，逻辑自洽。
- `framing.py` 的 pkg_len uint16 上限检查、zip_size==unzip_size 的 zlib 包装兜底、`iter_frames` 帧长守卫，质量良好。
- `handshake.py` 默认 30 零字节产品标识块有 A/B 实测支撑（模块文档），无实现问题。

---

## 3. 待证项

| 线索 | 现状 | 建议查法 |
|---|---|---|
| `gpcw*.dat` 是否带文件头 | `FinanceReader` 把整个文件当扁平 float32 数组（formats.py:521-527），若真实 gpcw 有版本头/股票头，字段探测从第一个字节就错位 | 取一份真实 gpcw 文件看前 64 字节；对照通达信目录结构文档 |
| `.day` 29 字节旧布局是否在野 | 探测器候选含 29（reader/profile.py `RECORD_SIZES`），但解码固定 32（M6） | 找 29 整除且 32 不整除的样本文件验证 |
| L2 启发式对真实未知命令的命中/误报率 | `parse_generic` 阈值 0.5、`CANDIDATE_RECORD_SIZES` 含 1 字节，理论误报面大，但没有对 `reports/capture_live` 之外未知命令样本的离线评估 | 用 `PROTOCOL_SPEC/_sniffer` 存量样本跑 `infer_record_layout` 统计得分分布 |
| `Market` 三重定义的运行期影响 | `reader/profile.Market`、`domain/symbol.Market`、`_std7709_common.Market` 同名不同义（reader/profile.py 文档自认与 domain.symbol.Market 重名） | 梳理三者消费方，确认无跨模块误用（静态检查 mypy 别名） |

---

## 4. 建议的下一步

1. **先堵正确性**（H2 + M6 + L3）：`_prepare` 让 `ProfileUndetectable` 按 strict 语义上抛；解码消费探测出的 record_size；gpcw 字段数不可判定时报错。这三个改动都不动协议层，收益直接。
2. **再摘热路径 IO**（H1）：sniffer 改异步/可关，配置注入归档根。
3. **收拢双实现**（H3 + M2）：字符集探测合一；两套 profile 探测器抽公共打分内核。
4. **账目合一**（M1 + M3）：tier 单一事实源放账本；握手帧与 prober 剥头改走 FrameSpec。
5. **样板收敛**（M4 + M5）：`iter_fixed_records` 进 BaseParser；records.py 抽序列化基类；解析器→领域模型的转换器集中到 client 层一张表。
