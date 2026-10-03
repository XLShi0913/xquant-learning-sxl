# Backtest：日频回测框架

框架将行情与成交、策略判断、账户记账、统计研究分离。策略输出订单，
引擎推进交易日；同一成本模型服务下单预算和实际成交。

## 架构

~~~text
YAML ── market / strategy / broker / engine / analysis / data / research / validation
                   │
MarketDataSource ── SimBroker（行情、挂单、成交） ── TransactionCostModel
                   ↑               │                     └─ SlippageModel
Strategy ── Order ── Engine ── Account（现金、持仓、每日历史）
                     │
                     └─ BacktestResult ── performance / research

validation ── 日期窗口 / 精确行号序列 ── 调用方选择训练与验证数据
              （不持有引擎、账户或策略，不自动执行或选优）
~~~

| 模块 | 职责 |
|---|---|
| account.py | 现金、整数持仓、只读账户视图、列表形式的每日快照 |
| data.py | 数据接口、内存行情、课程CSV缓存、日期窗口与训练/验证分割 |
| alpaca.py | 只读获取美国股票/ETF日线，不接入交易API |
| yahoo.py | 雅虎日线适配器，优先读取兼容的本地CSV缓存 |
| costs.py | 滑点成交价、佣金、卖出印花税、交易手数约束 |
| broker.py | 对齐共同交易日、执行订单、保存挂单和成交历史 |
| strategy.py | generate_orders(account, market, date) 接口 |
| allocation.py / risk_controls.py | 等权、对角风险平价、RAM、买入持有、止损/止盈 |
| engine.py | 先撮合历史挂单→策略下单→成交后保护单→日末估值 |
| performance.py | 总体、自然年、月度、回撤事件统计 |
| research.py | 独立运行工厂、参数扫描及选优 |
| validation.py | 顶层数据划分：滚动/锚定前推、时间序列CV、随机K折 |
| configuration.py | YAML读取、合法键校验、防御性配置快照 |

## Notebook 导入与最短用法

在项目根目录或任一章节子目录启动 Notebook，第一格加入：

~~~python
from pathlib import Path
import sys
root = next(p for p in (Path.cwd(), *Path.cwd().parents)
            if (p / "backtest").is_dir() and (p / "pyproject.toml").is_file())
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from backtest import load_config, create_data_source, run_backtest
config = load_config(root / "q5" / "config.yaml")
source = create_data_source(config, root=root)
result = run_backtest(source, config=config, method="ram", stop_loss=0.05)
display(result.metrics)
display(result.annual)
(result.equity / result.engine.account.initial_cash).plot(title="扣费后净值")
~~~

BacktestResult 包含 engine、ledger、equity、metrics、annual。
成交在 result.engine.broker.trades；订单状态在 order_history；
策略目标权重在 result.engine.strategy.audit，实际权重应按持仓市值计算。

## 配置与优先级

公共默认值在 backtest/config/default.yaml；章节配置在 q4/config.yaml、
q5/config.yaml。未列出的键继承公共值，未知键报错。

**明确传入的方法参数 > 已加载的配置值**。
加载顺序：公共默认→指定YAML→load_config 的 overrides。
0 不会当作“未传”；保护参数显式传 None 表示关闭保护。

~~~python
config = load_config(root / "q5" / "config.yaml", overrides={
    "market": {"start": "2023-01-01", "end": "2026-03-18"},
    "strategy": {"volatility_window": 30, "interval": 10},
    "broker": {"initial_cash": 200000.0},
})
# 本次窗口15覆盖配置中的30；None关闭配置中的止损。
result = run_backtest(source, config=config, volatility_window=15, stop_loss=None)
~~~

| 配置区 | 内容 | 用途 |
|---|---|---|
| market | 日期、标的、显示名称 | 数据与实验范围 |
| strategy | 方法、动量/波动率窗口、调仓间隔、止损/止盈 | 策略运行与回测共用 |
| broker | 初始资金、整手、佣金、税率、滑点、成交采样价、标的类型 | 仅模拟成交，不参与权重公式 |
| engine | 信号时点、年交易日数 | 时间推进与年化统计 |
| analysis | MAR、回撤阈值、回撤排名数量 | 结果统计 |
| data | 数据源、缓存目录、下载开关、Alpaca数据参数 | 行情获取 |
| research | 扫描列表、训练/验证边界、实验对比止损值 | 实验设计 |
| validation | 训练/验证周期、推进步长、间隔、折数、随机种子 | 仅验证实验设计，不属于策略或券商参数 |

run_backtest 默认采用公共A股配置。直接使用低层 Account、SimBroker、Engine
且不传 config 时，采用 backtest/config/legacy.yaml：一股单位、千分之一佣金、
最低5元、无税费/滑点、开盘成交/前日信号。
低层组装时请显式给三者传同一个 config，以启用A股100股和新成本。
legacy.yaml、us.yaml 也可以显式加载。

## 成本和交易数量

公共A股配置：

- 买卖订单都必须为100的整数倍，不合规则拒单，券商不会偷偷改股数。
- 每笔完整成交佣金为 max(成交额×0.0002, 5元)，买卖双向收取。
- 股票卖出印花税为成交额×0.0005；买入不收。
- ETF免印花税；broker.instrument_types 明确标记 stock / etf，未标记默认股票。
- 固定不利滑点：买入价=观察价×1.001，卖出价=观察价×0.999。
  限价及止损限价的成交价截断在限价边界，不会劣于限价。

~~~yaml
broker:
  lot_size: 100
  commission_rate: 0.0002
  minimum_commission: 5.0
  stamp_tax_rate: 0.0005
  slippage_rate: 0.001
  instrument_types:
    600519.SS: stock
    510300.SS: etf
~~~

买入现金变化为 −数量×成交价−佣金−税；卖出为 +数量×成交价−佣金−税。
Trade.price 已含滑点，market_price 为原始观察价，slippage 为与原价的差额，
仅用于归因，**不能从现金再次扣除**。Trade.fees = commission + stamp_tax。
净值按原始收盘价估值，成交滑点因而即时影响净值。

策略按整手向下取整，卖出优先；买入通过 broker.quote_order 核对资金，
不足时按整手缩减。剩余现金不被强行归一化分配。
扩展滑点请实现 SlippageModel.execution_price(symbol, side, shares, market_price, *, date=None)，
通过 SimBroker 的 slippage_model 参数注入。数量相关模型需保证总买入支出随数量单调不减。

规则依据：[上交所ETF免印花税说明](https://etf.sse.com.cn/fund/learning/knowledge/c/5704298.shtml)。
固定税率为本次指定的模拟假设，不按历史日期还原税率变化。

## 挂单与撮合状态

~~~python
from backtest import Order
market = Order("510300.SS", "BUY", 100)
limit = Order("510300.SS", "BUY", 100, order_type="LIMIT", limit_price=3.5)
stop = Order("510300.SS", "SELL", 100, order_type="STOP", stop_price=3.2)
stop_limit = Order("510300.SS", "SELL", 100, order_type="STOP_LIMIT",
                   stop_price=3.2, limit_price=3.15)
~~~

限价买：观察价≤限价；限价卖：观察价≥限价。
止损买：观察价≥触发价；止损卖：观察价≤触发价。
STOP触发后按观察价加滑点成交，不保证止损价；
STOP_LIMIT触发状态锁存，尚不满足限价时继续等待。
挂单为GTC，不冻结资金或持仓，成交时才校验。撤单使用 cancel_order(order_id)。

broker._match 状态机，观察价为指定 Open 或 Close：

~~~mermaid
flowchart TD
    A[新委托或历史挂单] --> B{标的/账户/日期/整手合法?}
    B -- 否 --> R[REJECTED]
    B -- 是 --> C{止损类且未触发?}
    C -- 是 --> D{触及触发价?}
    D -- 否 --> P[PENDING]
    D -- 是: 锁存触发 --> E{限价类?}
    C -- 否 --> E
    E -- 是 --> F{观察价满足限价?}
    F -- 否: 止损限价已触发 --> T[TRIGGERED]
    F -- 否: 普通限价 --> P
    F -- 是 --> G[滑点价不突破限价; 计算佣金/税]
    E -- 否 --> G
    G --> H{现金/持仓足够?}
    H -- 否 --> R
    H -- 是 --> I[FILLED: 记录现金/持仓/Trade]
    P --> J{下一日重试或撤单}
    T --> J
    J -- 重试 --> A
    J -- 撤单 --> K[CANCELED]
~~~

引擎先处理历史挂单，使策略看到触发后的持仓，再处理当日订单及保护单。
券商按订单ID和日期防止同一天反复评估旧挂单。
保护策略止损/止盈基于含滑点、不含佣金的加权买入均价；
退出后仅在下一计划调仓日重新入场，禁止退出当天重买。

## 内置分配策略

- equal：等权，每隔 interval 个共同交易日恢复目标权重。
- risk_parity：假定零相关，权重正比于日对数收益样本标准差的倒数；
  只使用波动率窗口。
- ram：RAM=窗口内平均日对数收益/日对数收益样本标准差。
  两窗口可分别设置，仅正RAM按得分归一化，全非正则100%现金。
- buy_hold：首次收盘等权买入，之后不调仓、不卖出。

指标策略需窗口+1个价格，不隐式读取起始日期前数据，历史不足持有现金。
第0、N、2N…个共同交易日调仓，因此首次入场可能晚于刚满足窗口的日期。
资产加现金目标权重之和与1的差每次校验不超过1e-6。

## 数据源：本地缓存、雅虎与 Alpaca

DataFrameDataSource({symbol: frame}) 接收日期索引和 Open / Close，
校验重复日期和无效价格；日期为当地日历日、无时区，返回独立副本。
`create_data_source(config, root=root)` 由 `data.provider` 选择数据源：

- `cache`：本地 CSV；默认不联网，兼容旧版显式开启下载的行为。
- `yfinance`：雅虎日线；先读缓存，只有 `download_if_missing: true` 才下载缺失区间。
- `alpaca`：Alpaca 美股日线，使用独立的连接与缓存配置。

默认仍为 `cache`，不改变已有实验的联网行为。雅虎逻辑没有删除，
由 `YFinanceDataSource` 统一提供；旧缓存接口的下载分支也复用它。
文件声明区间需覆盖请求范围，错误明确抛出，不伪造结果或静默切换提供商。
券商只在所有标的都有完整开盘/收盘价的共同交易日运行，排除日在 excluded_dates。

### 雅虎配置

~~~yaml
data:
  provider: yfinance
  download_if_missing: true
  yfinance:
    auto_adjust: true
    cache_dir: data/yfinance
    timeout: 30
    threads: false
    progress: false
~~~

~~~python
from backtest import load_config, create_data_source
config = load_config(root / "q5" / "config.yaml").override(
    data={"provider": "yfinance", "download_if_missing": True})
source = create_data_source(config, root=root)
bars = source.get_bars("510300.SS", "2021-01-01", "2026-03-18")
# 或单独使用完整雅虎示例配置：backtest/config/yfinance.yaml。
~~~

`YFinanceDataSource` 的显式构造参数优先于 YAML；无需雅虎 API 密钥。
读取目录由 `data.cache_dirs` 加上雅虎专用目录组成，兼容 q3/q4/q5 已有缓存。
新下载写入雅虎专用目录。复权文件后缀为 `adjusted.csv`，非复权为 `raw.csv`，
两种口径不会混用。公共接口日期含两端，下载时将结束日向后移动一天。
日线固定 `interval="1d"`，返回本地日历日期索引和单层列。
下载依赖可通过 `pip install -e ".[download]"` 安装；缓存命中时不导入 yfinance。
网络错误、限流和空结果仍会失败，不自动重复请求，也不把无效结果写入缓存。
参数依据：[yfinance.download 官方文档](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)。

### Alpaca 配置

Alpaca只适用于美股股票/ETF，如SPY、QQQ、GLD，不能下载中国上市的
510300.SS、513100.SS、518880.SS，也不会用美股替代它们。

~~~python
from backtest import AlpacaDataSource
config = load_config(root / "backtest" / "config" / "us.yaml")
# 设置环境变量，或使用独立的 backtest/config/alpaca.local.yaml。
# 密钥不要写入公共配置、Notebook或聊天；浏览器登录不等于API鉴权。
source = AlpacaDataSource(config=config)
spy = source.get_bars("SPY", "2024-01-01", "2024-12-31")
~~~

独立本地文件的格式如下（示例是占位符，不是真实密钥）：

~~~yaml
endpoint: https://paper-api.alpaca.markets/v2
key: YOUR_PAPER_KEY
secret: YOUR_PAPER_SECRET
~~~

本地文件路径由 data.alpaca.credentials_file 配置，默认为
backtest/config/alpaca.local.yaml；它被Git忽略并从打包数据中排除。
公共配置和回测configuration.json只记录这个路径，不复制密钥。
这是明文存储，需保护本机文件；.gitignore并不等于加密。
鉴权优先级：方法显式传入完整key/secret对 > 完整环境变量对 > 本地YAML对。
环境变量只设置一项则报错，不与另一来源的密钥混用；显式None关闭鉴权。
测试可传 credentials_file=None，禁用本地文件读取。

endpoint是模拟交易端点；行情数据仍从data.alpaca.data_endpoint的官方Market Data
服务获取，两者不能互换。适配器不向交易端点发送请求。

适配器固定访问官方Market Data endpoint，仅GET，不调用交易API；
按 next_page_token 翻页，拒绝重复令牌或未完成的分页。
日线UTC时间戳转换为纽约日期，起止日期含两端。
缓存键区分标的、日期、feed、adjustment、timeframe，密钥不会入缓存，
命中缓存不需密钥。401/403/429、网络错误、空数据明确报错，不静默回退。
默认iex不是全市场SIP，权限以账户套餐为准；all表示复权。
us.yaml是数据/模拟配置示例，不代表Alpaca实际收取此处设定的佣金。

依据：[官方数据范围与鉴权](https://docs.alpaca.markets/us/docs/about-market-data-api)、
[日线API及分页参数](https://docs.alpaca.markets/us/reference/stockbars)。
离线测试使用合成HTTP响应，不证明真实账户下载权限，真实下载需另行验证。

## 参数扫描与时间分割

~~~python
from backtest import scan_parameter, scan_tied_windows, scan_grid, scan_metrics, select_best
runner = lambda **p: run_backtest(source, config=config, **p)
frequencies = scan_parameter(runner, "interval", [5, 10, 21, 63])
ram_windows = scan_tied_windows(runner, [10, 15, 20, 25, 30, 40],
                               fixed={"method": "ram"})
display(scan_metrics(ram_windows))  # RAM两窗口同步，不是组合网格
grid = scan_grid(runner, {"interval": [5, 10], "stop_loss": [None, 0.05]})
~~~

扫描每次应创建独立账户/券商/策略，run_backtest已保证。
scan_cases支持命名参数组合和任意返回值；scan_metrics / select_best面向BacktestResult。
失败不会被吞掉；选优忽略非有限分数，全部无效则报错。

~~~python
config = load_config(root / "q5" / "config.yaml")
source = create_data_source(config)
m, r = config.section("market"), config.section("research")
parts = source.split(train_start=m["start"], train_end=r["train_end"],
                     validation_start=r["validation_start"], validation_end=m["end"])
training_runner = lambda **p: run_backtest(parts.train, config=config,
    start=parts.train.start, end=parts.train.end, method="ram", **p)
training = scan_tied_windows(training_runner, r["windows"])
best = select_best(training, "simplified_sharpe")
validation = run_backtest(parts.validation, config=config,
    start=parts.validation.start, end=parts.validation.end, method="ram",
    momentum_window=best, volatility_window=best)
display(validation.metrics)
~~~

分割返回有明确边界的视图：请求再宽也只能获取当前阶段数据，训练/验证不能重叠。
验证重新初始化账户与指标，不继承持仓或预热历史。
只在训练集选参数，冻结后验证一次，不能依据验证结果再次挑参数。
q4逐步选优、q5全区间敏感性图仍属样本内研究，不是样本外验证。

## 多折验证数据划分

`validation.py` 只决定数据如何划分；不下载行情、不调参、不撮合交易、
不保存账户状态，也不自动拼接各折收益。算法与现有执行组件独立，
仅复用公共配置读取。可以先检查序列，再自行组合参数扫描和回测。

### 滚动式与锚定式前推

~~~python
from backtest import walk_forward_splits

rolling = walk_forward_splits("2021-01-01", "2026-03-18",
                              train_period="2Y", test_period="6M", mode="rolling")
anchored = walk_forward_splits("2021-01-01", "2026-03-18",
                               train_period="2Y", test_period="6M", mode="anchored")
display(pd.DataFrame(f.to_dict() for f in rolling))
~~~

两者返回 `tuple[DateSplit, ...]`，每折有 `train_start`、`train_end`、
`validation_start`、`validation_end`，均为含端点的 ISO 日期。
默认第1折是训练 2021-01-01～2022-12-31、验证 2023-01-01～2023-06-30。
第2折滚动训练改为 2021-07-01～2023-06-30；锚定训练仍从 2021-01-01 开始。
本区间默认生成7折，最后验证段截断到 2026-03-18。

- `train_period`、`test_period`、`step` 支持正整数加 `Y/M/D`，例如 `2Y/6M/126D`。
  **D是自然日，不是交易日**；交易日由数据源和券商实际行情确定。
- `step=None` 随验证周期推进；否则按指定步长移动。游标由最初起点加步长倍数计算，
  月末偏移依照 pandas 的日历规则，并非固定天数。
- `mode="rolling"` 移动训练起点；`"anchored"` 固定训练起点，逐步扩大训练集。
- `gap_days` 在训练结束与验证开始之间留出自然日间隔，默认0。
- `include_partial=True` 保留并截断最后的不完整验证窗口；False只保留完整窗口。
  没有足够数据形成首个完整训练段及至少一天验证段时返回空元组。
- 每折严格保证训练早于验证，但自定义步长可能让**不同折**的验证段重叠或留空档。
  不要直接拼接重叠段净值，否则会重复计入同一日期。

与已有时间分割、参数扫描的组合方式：

~~~python
from backtest import scan_tied_windows, select_best, run_backtest

fold = rolling[0]
parts = source.split(**fold.to_dict())
runner = lambda **p: run_backtest(parts.train, config=config,
    start=fold.train_start, end=fold.train_end, method="ram", **p)
training = scan_tied_windows(runner, config.section("research")["windows"])
best = select_best(training, "simplified_sharpe")
out_of_sample = run_backtest(parts.validation, config=config,
    start=fold.validation_start, end=fold.validation_end,
    method="ram", momentum_window=best, volatility_window=best)
~~~

每折都在训练段独立选参数，然后冻结参数跑验证段；每次运行创建新账户。
有日期窗口不保证其中有交易行情，应先检查短窗口、节假日和窗口预热长度。
默认验证不读取训练段作指标预热、不继承持仓，保持现有公共模块语义。

### 时间序列交叉验证

~~~python
from backtest import time_series_cv_splits

folds = time_series_cv_splits("2021-01-01", "2026-03-18",
                              n_splits=5, expanding=True, gap_days=0)
display(pd.DataFrame(f.to_dict() for f in folds))
~~~

同样返回 `DateSplit`。将含结束日的自然日范围划为 `n_splits+1` 个近似等长块，
逐折用下一块验证。`expanding=True` 用所有此前块训练；False只用紧邻的前一块。
前一种对应同作者本地q6参考规范的 `TimeSeriesCV(n_splits=5, expanding=True)` 思路。
各折严格先训练后验证；最后一块包含指定结束日。`gap_days` 删除验证块开始的若干天，
如果留下空验证块则报错，不悄悄减少折数。末日和余数按本框架含端点约定分配，
不承诺与其他SDK的日期舍入方式逐日一致。

### 随机K折交叉验证

~~~python
from backtest import random_cv_splits

# prices是按日期升序、无重复日期的实际观测表；多标的应先对齐共同日期。
folds = random_cv_splits(prices.index, n_splits=5, seed=42)
train, validation = folds[0].take(prices)
print(folds[0].train_indices, folds[0].validation_indices)
print(folds[0].is_chronological)
~~~

返回 `tuple[IndexSplit, ...]`，不返回单一的起止区间。
局部随机数生成器将观测位置随机分成K组，每次留1组验证，其余组训练；
每行恰好验证一次，同折训练/验证无交集，组大小最多差1行。
组内按原日期顺序返回，不打乱价格序列。相同日期、参数和种子生成相同序列，
也不改变全局随机数状态。

`train_indices/validation_indices` 是原表的零基行号；
`train_dates/validation_dates` 是精确日期；`take()` 返回独立副本，
会拒绝日期错位、乱序或缺行的输入。所有划分记录不可变。

**随机K折不是严格样本外时间验证**：训练集可能包含验证期之后的数据，
相邻收益或重叠指标窗口也可能造成信息泄漏。不能取随机日期的min/max再调用
`source.split()`，那会把未选日期重新混入；也不能把抽中的价格当成连续行情，
直接计算RAM或拼接净值。这里不自动提供这种误导性的回测执行方式。
随机K折适用于观测级模型/特征研究；评价交易策略未来表现优先用前推或时间序列CV。
本实现是随机逐行K折，不是随机区块CV、purged CV或组合式CV。

### 配置

默认设置放在 `backtest/config/default.yaml` 的独立 `validation` 区域：

~~~yaml
validation:
  walk_forward:
    train_period: 2Y
    test_period: 6M
    mode: rolling
    step: null
    gap_days: 0
    include_partial: true
  time_series_cv:
    n_splits: 5
    expanding: true
    gap_days: 0
  random_cv:
    n_splits: 5
    seed: 42
~~~

三个函数均支持 `config=config`；显式方法参数覆盖YAML，日期范围仍由调用方提供。
例如 `walk_forward_splits(m["start"], m["end"], config=config, mode="anchored")`。

规则依据：[同作者前推规范](https://github.com/xingwudao/xquant-learning/blob/main/q6-avoid-overfitting/specs/spec-02-walk-forward.md)、
[交叉验证规范](https://github.com/xingwudao/xquant-learning/blob/main/q6-avoid-overfitting/specs/spec-03-cross-validation.md)。
[线上6.2/6.3章节](https://xquant.shop/courses/book/q6-avoid-overfitting)当前访问只显示购买前预览，
未核实受限正文；时间顺序方法按本地参考规范实现，随机K折按本次明确需求单独提供。

## 指标与验证

指标基于扣成本后的每日净资产：净值、累计收益、CAGR、年化波动率、
最大回撤、简化夏普、卡玛、索提诺。
CAGR使用实际估值间隔日数/365.25；
简化夏普为日收益均值/样本标准差×√年交易日数，无风险利率0。
索提诺使用全序列中低于日MAR的超额收益负部RMS，年MAR默认0%。
零分母返回NaN，首日费用计入日收益，初始资金计入回撤峰值。
自然年切片不中断策略或重置持仓，以前年末净资产为期初。
月度收益含首尾不完整月份；回撤事件必须回到前峰才恢复，未恢复日期留空。

~~~powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/verify_research_notebooks.py
~~~

第二个命令执行q4/q5，更新指标、图表及Notebook输出。
执行前把旧结果和Notebook保存到各自results中的before-cost-model-时间.zip。
run_backtest自动核对逐笔现金、净值、非负持仓、整手数量和目标权重。

## 模型边界

本框架是单日价格采样、全额成交、只做多的教学模型，不含盘中路径、成交量容量、
涨跌停、T+1、零股卖出例外、资金冻结、交易所级OCO、最小价格变动或费用分币取整。
same_close是课程理想化假设，不保证看到收盘价后仍能在同一收盘价成交。
低层自定义策略可选previous_close和开盘成交，内置组合策略要求same_close。
复权行情用于研究，不逐笔处理分红/拆股的真实现金及持仓变化。
