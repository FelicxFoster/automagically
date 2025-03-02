import akshare as ak
import pandas as pd
import numpy as np
import os
from datetime import datetime
import json
import concurrent.futures
import warnings

# Bokeh相关导入
from bokeh.plotting import figure, output_file, save
from bokeh.models import ColumnDataSource, HoverTool, Span, LinearAxis, Range1d, CustomJS
from bokeh.layouts import column
from bokeh.models.widgets import Div

# 设置中文显示（Bokeh专用）
from bokeh.core.properties import value
from bokeh.io import export_png
from bokeh.models import Label

# 确保路径正确
DATA_DIR = os.path.join(os.getcwd(), "data")
HTML_DIR = os.path.join(os.getcwd(), "html")

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(HTML_DIR, exist_ok=True)

class DataHandler:
    """数据处理类（已修复ETF接口和列名问题）"""
    @staticmethod
    def get_index_data(symbol):
        """获取指数数据（兼容港股和A股）"""
        try:
            if symbol.startswith('hk'):
                # 港股指数接口
                df = ak.stock_hk_daily(symbol=symbol.replace("hk", ""))
                df = df.rename(columns={"日期": "date", "收盘": "close"})
            else:
                # A股指数接口
                df = ak.stock_zh_index_daily(symbol=symbol)
                df = df.rename(columns={"date": "date", "close": "close"})
            return df[["date", "close"]].rename(columns={"close": "price"})
        except Exception as e:
            print(f"{symbol} 指数数据获取失败: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_etf_data(symbol):
        """获取ETF数据（修复代码格式问题）"""
        try:
            # 去掉市场前缀（如 sh/sz）
            clean_symbol = symbol[2:] if symbol[:2] in ['sh', 'sz'] else symbol
            # 使用东方财富ETF接口
            df = ak.fund_etf_hist_em(symbol=clean_symbol)
            df = df.rename(columns={"日期": "date", "收盘": "close"})
            return df[["date", "close"]].rename(columns={"close": "price"})
        except Exception as e:
            print(f"{symbol} ETF数据获取失败: {e}")
            return pd.DataFrame()

    """数据处理类（已修复路径问题）"""
    @staticmethod
    def get_file_path(filename):
        """获取数据文件完整路径"""
        return os.path.join(DATA_DIR, filename)

    @staticmethod
    def update_data(file_name, new_data):
        """优化后的数据更新方法，路径指向DATA_DIR"""
        file_path = DataHandler.get_file_path(file_name)

        warnings.simplefilter(action='ignore', category=FutureWarning)
        if os.path.exists(file_path):
            existing = pd.read_csv(file_path, parse_dates=['date'])
        else:
            existing = pd.DataFrame(columns=['date', 'price'])

        if not new_data.empty:
            new_data['date'] = pd.to_datetime(new_data['date'])
            new_data = new_data.dropna(subset=['date'])

            if not existing.empty:
                last_existing_date = existing['date'].max()
                new_data = new_data[new_data['date'] > last_existing_date]

            if not new_data.empty:
                combined = pd.concat([existing, new_data], ignore_index=True)
                combined = combined.drop_duplicates('date', keep='last')
                combined = combined.sort_values('date')
                combined.to_csv(file_path, index=False)
                return combined
        return existing

class ChartGenerator:
    def __init__(self, config):
        self.config = config

    def create_figure(self, merged_df):
        """修正悬停工具：单提示框，位置右侧，并修复vline定义"""
        merged_df = merged_df.copy()
        merged_df['date'] = pd.to_datetime(merged_df['date'])
        merged_df['date_str'] = merged_df['date'].dt.strftime('%Y-%m-%d')

        # 创建统一数据源
        source = ColumnDataSource(data=merged_df)

        # 主画布配置
        p = figure(
            title=f"{self.config['title']}（最近半年）",
            x_axis_type='datetime',
            tools="pan,wheel_zoom,box_zoom,reset,save",
            width=1200,
            height=600,
            toolbar_location="above"
        )

        # 绘制指数线（左轴）
        line_index = p.line(
            x='date', y='price_index',
            source=source,
            line_width=2,
            color='#2CA02C',
            alpha=0.8,
            legend_label='指数'
        )
        p.yaxis.axis_label = "指数价格"
        p.yaxis.axis_label_text_color = "#2CA02C"

        # 添加ETF线（右轴）
        p.extra_y_ranges = {"etf": Range1d(
            start=merged_df['price_etf'].min() * 0.98,
            end=merged_df['price_etf'].max() * 1.02
        )}
        line_etf = p.line(
            x='date', y='price_etf',
            source=source,
            line_width=2,
            color='#1F77B4',
            alpha=0.8,
            y_range_name="etf",
            legend_label='ETF'
        )
        p.add_layout(LinearAxis(
            y_range_name="etf",
            axis_label='ETF价格',
            axis_label_text_color='#1F77B4'
        ), 'right')

        # 关键修复1：重新定义红色参考线
        vline = Span(location=0, dimension='height',
                    line_color='red', line_dash='dashed',
                    line_width=1.5, visible=False)
        p.add_layout(vline)

        # 悬停工具配置
        hover = HoverTool(
            tooltips=[
                ("日期", "@date_str"),
                ("指数价格", "@price_index{0.2f}"),
                ("ETF价格", "@price_etf{0.2f}")
            ],
            mode='vline',
            line_policy='nearest',
            attachment='right',
            renderers=[line_index]
        )
        p.add_tools(hover)

        # 关键修复2：正确传递vline到回调
        callback = CustomJS(args=dict(source=source, vline=vline), code="""
            const x = cb_obj.x;
            const dates = source.data.date.map(d => d.valueOf());

            let closest = 0;
            let minDiff = Infinity;

            for (let i = 0; i < dates.length; i++) {
                const diff = Math.abs(dates[i] - x);
                if (diff < minDiff) {
                    minDiff = diff;
                    closest = i;
                }
            }

            vline.location = source.data.date[closest];
            vline.visible = true;
        """)
        p.js_on_event('mousemove', callback)

        # 隐藏参考线回调
        hide_callback = CustomJS(args=dict(vline=vline), code="vline.visible = false;")
        p.js_on_event('mouseleave', hide_callback)

        # 生成HTML
        safe_title = self.config['title'].replace(" ", "_").replace("/", "-")
        output_file(os.path.join(HTML_DIR, f"{safe_title}.html"))  # 修改输出路径
        save(column(
            Div(text=f"<h2 style='text-align:center;'>{self.config['title']}</h2>"),
            p
        ))

class IndexETFComparator:
    """主比较类（仅需修改绘图调用部分）"""
    def __init__(self, config_file):
        self.config_file = config_file
        self.configs = self._load_json_config()
        self.data_handler = DataHandler()

    def _load_json_config(self):
        """加载 JSON 配置文件"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载 JSON 配置文件失败: {e}")
            return []

    def process_all(self):
        """修改后的主流程"""
        print("正在更新数据并生成图表...")

        # 多线程获取数据
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = [executor.submit(self._fetch_data_for_config, cfg) for cfg in self.configs]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"配置处理出错: {e}")

        # 生成图表
        for cfg in self.configs:
            try:
                print(f"生成图表 {cfg['title']}：")
                self._process_single_config(cfg)
            except Exception as e:
                print(f"生成图表 {cfg['title']} 失败: {e}")

        print("所有图表已生成，请查看当前目录下的HTML文件")

    def _fetch_data_for_config(self, cfg):
        """多线程数据获取方法"""
        try:
            print(f"开始更新 {cfg['title']} 数据...")
            self._get_index_data(cfg)
            self._get_etf_data(cfg)
            print(f"完成 {cfg['title']} 数据更新")
        except Exception as e:
            print(f"{cfg['title']} 数据更新失败: {e}")

    def _process_single_config(self, cfg):
        """修改后的单个配置处理"""
        index_data = self._get_index_data(cfg)
        etf_data = self._get_etf_data(cfg)

        if index_data.empty or etf_data.empty:
            print(f"警告：{cfg['title']} 数据缺失，跳过生成图表")
            return

        # 合并数据
        merged = pd.merge(
            index_data.rename(columns={'price': 'price_index'}),
            etf_data.rename(columns={'price': 'price_etf'}),
            on='date',
            how='inner'
        )

        if not merged.empty:
            # 时间范围过滤（最近半年）
            merged['date'] = pd.to_datetime(merged['date'])
            cutoff_date = pd.Timestamp.now() - pd.DateOffset(months=6)
            filtered = merged[merged['date'] >= cutoff_date]

            # 如果过滤后数据为空，使用全部数据
            final_df = filtered if not filtered.empty else merged

            # 生成图表
            ChartGenerator(cfg).create_figure(final_df)

    def _get_index_data(self, cfg):
        # print("正在更新指数数据...")
        index_new = self.data_handler.get_index_data(cfg["index_symbol"])
        return self.data_handler.update_data(cfg["index_file"], index_new)

    def _get_etf_data(self, cfg):
        # print("正在更新ETF数据...")
        etf_new = self.data_handler.get_etf_data(cfg["etf_symbol"])
        return self.data_handler.update_data(cfg["etf_file"], etf_new)

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(HTML_DIR, exist_ok=True)

    config_file = "configs.json"
    comparator = IndexETFComparator(config_file)
    comparator.process_all()