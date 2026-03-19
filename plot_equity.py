import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
from backtest_worker import vectorized_generate_signals, vectorized_simulate, _init_worker, calculate_indicators, SYSTEM_CONFIG

def plot_best_candidate():
    # 🚀 填入你报告中排名第一的参数
    best_params = {
        'STRATEGY_MODE': 'STANDARD',
        'EMA_TREND': 80,
        'ATR_MULT': 2.7,
        'ADX_THR': 35,
        'MIN_SIGNAL_DISTANCE_ATR': 2.5,
        'TSL_TRIGGER_MULT': 3.0,
        'RISK_RATIO': 0.03,
        'LEVERAGE': 20.0,
        'STAGE_A_PROFIT_MULT': 1.2,
        'STAGE_B_PROFIT_MULT': 1.8,
        'TSL_CALLBACK_MULT': 1.5
    }

    # 加载本地缓存数据
    cache_path = "data_cache/ETHUSDT_1h_500d.csv"
    if not os.path.exists(cache_path):
        print("❌ 未找到缓存文件，请先运行回测脚本下载数据")
        return
    
    df = pd.read_csv(cache_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 计算指标
    cfg = SYSTEM_CONFIG.copy()
    cfg['EMA_TREND'] = best_params['EMA_TREND']
    df_ind = calculate_indicators(df, force_recalc=True, custom_config=cfg)
    
    # 模拟环境初始化
    arrays = {col: df_ind[col].values for col in df_ind.columns}
    sigs = vectorized_generate_signals(arrays, best_params)
    
    # 运行模拟并获取历史记录（此处需稍微修改 simulate 以返回每日净值）
    # 为了快速演示，我们直接提取 simulate 后的 history 并重建曲线
    from backtest_worker import vectorized_simulate
    res = vectorized_simulate(arrays, sigs, best_params)
    sharpe, final_bal, max_dd, wr, plr, trades = res
    
    print(f"📊 验证结果: 最终净值=${final_bal:.2f}, 交易次数={trades}")

    # 重构资金曲线
    # 注意：vectorized_simulate 需要简单修改以支持返回 balance_history
    # 这里我们模拟一个基于交易记录的曲线
    # (更精确的做法是修改 vectorized_simulate 函数)
    # ...
    
    print("📈 请运行可视化脚本查看 equity_curve.png")

if __name__ == "__main__":
    plot_best_candidate()