# Backtest：日频回测框架

框架将行情与成交、策略判断、账户记账、统计研究分离。策略输出订单，
引擎推进交易日；同一成本模型服务下单预算和实际成交。

## 架构

~~~text
YAML ── market / strategy / broker / engine / analysis / data / research
                   │
MarketDataSource ── SimBroker（行情、挂单、成交） ── TransactionCostModel
                   ↑               │                     └─ SlippageModel
Strategy ── Order ── Engine ── Account（现金、持仓、每日历史）
                     │
                     └─ BacktestResult ── performance / research
~~~

| 模块 | 职责 |
|---|---|
| account.py | 现金、整数持仓、只读账户视图、列表形式的每日快照 |
| data.py | 数据接口、内存行情、课程CSV缓存、日期窗口与训练/验证分割 |
| alpaca.py | 只读获取美国股票/ETF日线，不接入交易API |
| costs.py | 滑点成交价、佣金、卖出印花税、交易手数约束 |
| broker.py | 对齐共同交易日、执行订单、保存挂单和成交历史 |
| strategy.py | generate_orders(account, market, date) 接口 |
| allocation.py / risk_controls.py | 等权、对角风险平价、RAM、买入持有、止损/止盈 |
| engine.py | 先撮合历史挂单→策略下单→成交后保护单→日末估值 |
| performance.py | 总体、自然年、月度、回撤事件统计 |
| research.py | 独立运行工厂、参数扫描及选优 |
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
