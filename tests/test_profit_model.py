"""tests/test_profit_model.py"""
from core.profit_model import ProfitModel


class TestSingleCommodity:
    """单原材料利润模型（如中国海油）"""

    def setup_method(self):
        self.config = {
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
        }
        self.model = ProfitModel.from_config(self.config)

    def test_calculate_at_base_price(self):
        """基准价格时利润应等于基准利润 × 年化乘数"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 72.5},
        ]}
        result = self.model.calculate(prices)
        # H1x2: 1200 * 2 = 2400
        assert result[0]["annualized_profit"] == 2400.0

    def test_calculate_price_increase(self):
        """价格上涨时利润应增加"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 82.5},  # +10
        ]}
        result = self.model.calculate(prices)
        # base=1200, delta=10*15.2=152, period_profit=1352, annualized=1352*2=2704
        assert result[0]["annualized_profit"] == 2704.0

    def test_calculate_ytd_avg(self):
        """年内均价年化利润应基于累计均价"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 70.0},
            {"date": "2026-01-16", "close": 75.0},
        ]}
        result = self.model.calculate(prices)
        # day1: avg=70, delta=-2.5*15.2=-38, profit=1162, ann=2324
        assert result[0]["ytd_avg_annualized_profit"] == 2324.0
        # day2: avg=72.5, delta=0, profit=1200, ann=2400
        assert result[1]["ytd_avg_annualized_profit"] == 2400.0


class TestMultiCommodity:
    """多原材料利润模型（如紫金矿业）"""

    def setup_method(self):
        self.config = {
            "stock_name": "紫金矿业",
            "commodities": [
                {"name": "铜", "symbol": "HG=F", "source": "yfinance", "unit": "USD/lb"},
                {"name": "黄金", "symbol": "GC=F", "source": "yfinance", "unit": "USD/oz"},
            ],
            "annualization": "Qx4",
            "base_period": "2026Q1",
            "parameters": {
                "copper": {"base_profit": 450, "base_price": 95000, "sensitivity": 3.2},
                "gold": {"base_profit": 280, "base_price": 1050, "sensitivity": 0.8},
            },
        }
        self.model = ProfitModel.from_config(self.config)

    def test_multi_commodity_at_base(self):
        """多原材料基准价格时利润 = 各子项之和 × 年化乘数"""
        prices = {
            "HG=F": [{"date": "2026-01-15", "close": 95000}],
            "GC=F": [{"date": "2026-01-15", "close": 1050}],
        }
        result = self.model.calculate(prices)
        # (450+280)*4 = 2920
        assert result[0]["annualized_profit"] == 2920.0


class TestScenarios:
    """三场景汇总"""

    def setup_method(self):
        self.config = {
            "stock_name": "测试",
            "commodities": [
                {"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}
            ],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {
                "base_profit": 1000,
                "base_commodity_price": 80.0,
                "sensitivity": 10.0
            },
        }
        self.model = ProfitModel.from_config(self.config)

    def test_scenarios_returns_three(self):
        """scenarios 应返回三个场景"""
        prices = {"BZ=F": [
            {"date": "2026-02-01", "close": 80.0},
            {"date": "2026-02-02", "close": 85.0},
            {"date": "2026-03-06", "close": 90.0},
        ]}
        result = self.model.scenarios(prices)
        assert len(result) == 3
        assert result[0]["scenario"] == "最新价格"
        assert result[1]["scenario"] == "最近1个月均价"
        assert result[2]["scenario"] == "年初至今均价年化"
