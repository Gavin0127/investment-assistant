# 利润跟踪模块设计

日期：2026-03-08

## 目标

为投资助手新增"原材料价格 → 企业利润敏感性"跟踪能力。用户可为每只股票配置利润模型（手动或 LLM 推导），系统每日自动拉取大宗商品价格，计算并展示日度年化利润曲线。

## 数据模型

### 利润模型配置

每只股票的利润模型存储在 `stocks/{stock_id}/profit_model.json`：

```json
{
  "stock_name": "中国海油",
  "commodities": [
    {"name": "Brent原油", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}
  ],
  "annualization": "H1x2",
  "base_period": "2026H1",
  "parameters": {
    "base_profit": 1200,
    "base_commodity_price": 72.5,
    "sensitivity": 15.2
  },
  "created_by": "manual",
  "created_at": "2026-03-08T15:00:00"
}
```

多原材料场景（如紫金矿业）：

```json
{
  "stock_name": "紫金矿业",
  "commodities": [
    {"name": "铜", "symbol": "HG=F", "source": "yfinance", "unit": "USD/lb"},
    {"name": "黄金", "symbol": "GC=F", "source": "yfinance", "unit": "USD/oz"},
    {"name": "白银", "symbol": "SI=F", "source": "yfinance", "unit": "USD/oz"},
    {"name": "碳酸锂", "symbol": "LC0", "source": "akshare", "unit": "CNY/ton"}
  ],
  "annualization": "Qx4",
  "base_period": "2026Q1",
  "parameters": {
    "copper": {"base_profit": 450, "base_price": 95000, "sensitivity": 3.2, "gross_margin": 0.63},
    "gold": {"base_profit": 280, "base_price": 1050, "sensitivity": 0.8},
    "silver": {"base_profit": 50, "base_price": 20000, "sensitivity": 0.15},
    "lithium": {"base_profit": 120, "base_price": 140000, "sensitivity": 0.05}
  },
  "created_by": "llm_derived",
  "created_at": "2026-03-08T15:00:00"
}
```

### 年化方式

| 值 | 含义 | 计算 |
|----|------|------|
| `H1x2` | 半年报 × 2 | 基准期为半年利润，年化 = 利润 × 2 |
| `Qx4` | 单季报 × 4 | 基准期为单季利润，年化 = 利润 × 4 |
| `annual` | 直接年报 | 不做乘数处理 |

### 价格数据（SQLite）

存放在 `~/.investment-assistant/data/commodity_prices.db`：

```sql
CREATE TABLE commodity_prices (
    symbol     TEXT NOT NULL,
    source     TEXT NOT NULL,
    date       TEXT NOT NULL,     -- YYYY-MM-DD
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL NOT NULL,
    currency   TEXT DEFAULT 'USD',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, source, date)
);
```

## 模块架构

### core/profit_tracker.py

```
ProfitTracker(client, storage)          # 主入口，依赖注入
├── create_model_manual(stock_id, config)   # 手动配置利润模型
├── derive_model_with_llm(stock_id)         # LLM 推导利润模型
├── get_daily_profit(stock_id, date_range)  # 日度利润序列
└── get_summary(stock_id)                   # 三场景汇总表

CommodityPriceService(db_path)          # 价格采集 + SQLite 存储
├── fetch_daily(symbol, source, start, end)  # 拉取并存储日度数据
├── fetch_latest(symbol, source)             # 获取最新价格
├── get_cached(symbol, source, start, end)   # 从 SQLite 读取
└── refresh_all(symbols)                     # 批量刷新

ProfitModel                             # 利润计算引擎
├── from_config(config_dict)                 # 从 JSON 配置构建
├── calculate(commodity_prices)              # 计算日度利润
└── scenarios(commodity_prices)              # 三场景汇总
```

遵循现有依赖注入模式，`ProfitTracker` 接收 `LLMClient` 和 `Storage`。

### 数据源

| 品种 | 数据源 | symbol | 成本 |
|------|--------|--------|------|
| 原油 Brent | yfinance | `BZ=F` | 免费 |
| 原油 WTI | yfinance | `CL=F` | 免费 |
| 铜 | yfinance | `HG=F` | 免费 |
| 铝 | yfinance | `ALI=F` | 免费 |
| 黄金 | yfinance | `GC=F` | 免费 |
| 白银 | yfinance | `SI=F` | 免费 |
| 碳酸锂 | AKShare（广期所） | `LC0` | 免费 |

### 新增依赖

```toml
# pyproject.toml
"yfinance>=0.2.0"
"akshare>=1.10.0"
```

## 定时任务

`scripts/update_prices.py`：

- 遍历所有已配置 `profit_model.json` 的股票，收集 symbol 列表
- 调用 `CommodityPriceService.refresh_all()` 拉取当天数据写入 SQLite
- cron 配置：`0 22 * * 1-5`（工作日收盘后）
- 支持 `--manual` 参数手动触发

## Web 端

### 路由

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/profit-dashboard` | 利润跟踪总览 dashboard |
| GET | `/api/profit/<stock_id>` | 单只股票日度利润 JSON |
| POST | `/api/profit/<stock_id>/model` | 创建/更新利润模型 |
| POST | `/api/profit/refresh` | 手动触发价格刷新 |

### 页面

**Dashboard（`/profit-dashboard`）：**
- 卡片式布局，每只配置了利润模型的股票一张卡片
- 每张卡片：ECharts 双线折线图 + 三场景汇总表
- 双线：按当日价格年化利润（橙色）+ 年内均价年化利润（蓝色）
- 右上角手动刷新按钮

**股票详情页（`stock_detail.html`）：**
- 新增"利润跟踪"区块，嵌入同样的图表 + 表格
- 未配置模型时显示"配置利润模型"入口（手动填写 / LLM 推导）

### 图表

ECharts 通过 CDN 引入，不增加构建工具。双线折线图配置：

```
橙色线：每日按当天原材料收盘价计算的年化利润
蓝色线：按年初至当天的原材料均价计算的年化利润
X 轴：日期（01-01 至今）
Y 轴：年化利润（亿元）
```

## LLM 推导利润模型

当用户选择 LLM 推导时，`ProfitTracker.derive_model_with_llm(stock_id)` 会：

1. 读取该股票的 playbook 和历史研究记录作为上下文
2. 构造 prompt 要求 LLM 输出 `profit_model.json` 结构
3. 使用现有的 JSON 提取 fallback 逻辑（4 层）解析响应
4. 返回模型配置供用户确认后保存

## 与现有模块的关系

```
LLMClient + Storage
    ├── InterviewManager(client, storage)
    ├── EnvironmentCollector(client, storage)
    ├── ResearchEngine(client, storage)
    ├── PreferenceLearner(client, storage)
    └── ProfitTracker(client, storage)      ← 新增
```

`ProfitTracker` 是平级模块，不依赖其他业务模块，仅共享 `LLMClient` 和 `Storage`。
