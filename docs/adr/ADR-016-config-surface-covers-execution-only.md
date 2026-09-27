# ADR-016: 配置面只覆盖执行参数（the config surface covers execution only）

**状态**: Accepted
**日期**: 2026-09-19
**对应债务**: V17 F-16（P0，配置系统未接入产品链路）+ F-13（P1，配置面装饰化）

## 背景

v17 Phase 5 的实测证据（不是推断）：

1. `load_config()` 在 `atst/` 包内**零调用者**。CLI / HTTP / WS / MCP / `Client`
   全都不读配置文件，`Client.__init__` 也没有 `config=` 入口 —— 而
   `docs/troubleshooting.md` 长期指导用户"改 `atst.toml` 换主站"。写配置文件
   不改变任何行为。
2. `Config` 唯一进入运行期的路径是调用方自己构造后传给 `ConnectionPool.from_config`。
   而那个第二读者读的键名（`rate_call_auction`/`rate_continuous`/…）与
   `RateLimitConfig` 的字段名（`in_session`/`pre_post`/`closed`）**从不重合**，
   `getattr(..., 默认)` 于是把每一次配置赋值都静默丢弃成限流器自己的默认值 ——
   连数字都是两套互不相识的（配置段默认 15/30/60，实际生效 80/120/25/15）。
   它的契约测试还是照着幻影键名写的，因此全绿。
3. 12 个配置段里 `cache`/`output`/`profile`/`sources`/`observability`/`compatibility`/
   `feedback` 七段的 dataclass 在 `config/` 包外零引用。其中 `[cache]` 与
   "数据请求零缓存"的口径直接冲突：配置面承诺了一个内核刻意不提供的能力。

## 决策

**接线（而非收缩为内部结构）**，并确立一条可审计的口径：

> **配置面即执行面契约**：`Config` 里存在的每一个键，都必须被单一内核读取并改变
> 行为。不改变行为的键不存在。

由此推出五条实现规则：

1. **唯一读者**：`UnifiedRuntime` 是配置面的唯一消费者（`Client()` 缺省经
   `atst.config.get_config()` 惰性取进程级单例）；`WebQuoteClient` 作为独立
   legacy 入口读 `[web]` 段。显式构造参数永远优先于配置。
2. **唯一翻译点**：配置 → 传输层构造参数只在
   `atst.transport.pool.pool_settings_from_config()` 一处完成；
   `ConnectionPool.from_config` 已删除，不允许第二个读者存在。
3. **键名对齐事实**：`[rate_limit]` 字段名与 `SessionState` 成员一一对应，
   `SessionRateLimiter.from_config` 用直接属性访问而非 `getattr(..., 默认)` ——
   读不到的键名必须是 `AttributeError`，不是静默默认值。
4. **fail-closed**：未知段、未知字段、未知 `ATST_*` 变量、类型不符（bool 位写 `1`、
   int 位写 `1.5`、非有限浮点）一律立即抛错，绝不降级为默认值。删除的七段现在
   会命中"未知配置段"错误，而不是被忽略。
5. **主站解析单点**：`hosts.servers` 只在 `UnifiedRuntime` 解释一次，再作为已解析
   入参交给执行器，避免同一个键在两处各有语义。

保留的 5 段：`core` / `hosts` / `rate_limit` / `web` / `security`。

## 后果

- ✅ "写 `atst.toml` 不生效"这一 P0 口径缺陷消除，文档可以如实描述配置行为
  （见 [docs/configuration.md](../configuration.md)）。
- ✅ 配置面收缩 12 段 → 5 段：`atst/config/schema.py` −320/+45 行；连同第二个配置
  读者（`ConnectionPool.from_config` 及其 76 行硬化层
  `atst/transport/_pool_factory_hardening.py`）与 2 个只测幻影键的契约测试一并
  物理删除，`atst/` 包整体净减 355 行。
- ✅ 幻影键静默丢弃的整类缺陷有了回归锁（限流字段改名后旧键名必然抛）。
- ❌ 进程级惰性单例意味着"改文件后需 `reset_config()` 或重启进程"，配置不再是
  每请求重读。这是刻意取舍：六源合并含文件系统与网络盘探测，按请求重读会
  把 I/O 放进热路径。
- ❌ 已有用户若曾写 `[cache]` 之类的段，升级后会立即收到 `ValidationError`。
  这是 fail-closed 口径下**期望**的行为，不引入兼容忽略。

## 被否决的替代方案

| 方案 | 否决理由 |
|---|---|
| (b) 把配置降级为 `transport` 内部结构、`configure`/`load_config` 退出公开导出面 | `atst.toml`/`ATST_*` 已是公开面并写在文档里，撤退同样是口径破坏；且执行参数确有需要外部配置 |
| 执行器改为注入 `ConnectionPool.from_config(...)` 构造的池 | `TdxClient(pool=...)` 设 `_owns_pool=False` ⇒ 心跳线程生命周期外泄给调用方 |
| `_tdx_client` 改成上下文管理器 | 破坏 `tests/sink/test_local_day.py` 既有的打桩接缝，且每请求构造成本不因此改变 |
| 把已解析的 `HostEntry` 列表透传进 `TdxClient(hosts=...)` | 会二次 `resolve_hosts`，并被默认 `max_hosts` 静默截断 |
| 保留七个装饰段"以后再接线" | 与零缓存/零降级口径冲突的配置项留着就是谎言；F-16 的根因正是"先有契约面后有消费者" |
| 保留 `--cov-fail-under` 于 Makefile 与 CI | 三处副本使"改一处仍绿"，阈值事实源必须唯一（`pyproject.toml [tool.coverage.report] fail_under`） |

## 验证

| 锁 | 测试 |
|---|---|
| TOML → `Client`/内核/传输层端到端一致 | `tests/runtime/test_kernel_config_wiring.py` |
| 配置→池参数唯一翻译点、限流值真的抵达令牌桶 | `tests/transport/test_pool_settings_from_config_contract.py` |
| 幻影键名必须抛、不再静默丢 | `tests/transport/test_ratelimit_contract.py` |
| 只有 5 段、被删段 fail-closed | `tests/config/test_merge.py` |
| strict 校验语义 | `tests/config/test_schema_semantics.py`、`tests/config/test_schema_strict_fields.py` |
| 发布 wheel 冒烟引用的是唯一 seam | `tests/compatibility/test_release_pool_factory_wheel_contract.py` |
