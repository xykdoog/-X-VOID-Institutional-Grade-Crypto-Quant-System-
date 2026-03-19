# 🔥 多进程架构实施清单

## 快速开始

本清单提供逐步实施指南，确保零错误集成多进程架构。

---

## ✅ 实施步骤

### 步骤 1：备份现有文件

```bash
# 在 WJ-BOT 目录下执行
copy main.py main.py.backup
copy websocket_manager.py websocket_manager.py.backup
copy trading_engine.py trading_engine.py.backup
```

---

### 步骤 2：修改 `trading_engine.py`

#### 2.1 添加导入（文件顶部）

在现有导入之后添加：

```python
# 🔥 V7.0 新增：多进程支持
from indicator_calculator_worker import indicator_worker_loop
```

**位置：** 在所有 `import` 语句之后，在第一个函数定义之前

**验证：** 确保没有导入错误

---

### 步骤 3：修改 `websocket_manager.py`

#### 3.1 添加全局变量（文件顶部）

在现有全局变量（如 `kline_cache`）之后添加：

```python
# 🔥 V7.0 新增：全局 input_queue 引用
_global_input_queue = None
_global_input_queue_lock = threading.Lock()
```

#### 3.2 添加队列注入函数

在 `start_websocket_streams` 函数之前添加：

```python
def set_input_queue(input_queue):
    """注入 input_queue（由 main.py 调用）"""
    global _global_input_queue
    with _global_input_queue_lock:
        _global_input_queue = input_queue
    print("✅ WebSocket 已绑定 input_queue")
```

#### 3.3 修改 `_handle_kline_update` 函数

**找到现有代码：**
```python
# 旧代码（需要移除或注释）
threading.Thread(
    target=_trigger_signal_check,
    args=(symbol, client),
    daemon=True
).start()
```

**替换为：**
```python
# 🔥 V7.0 核心重构：推送到 input_queue（零阻塞）
global _global_input_queue
if _global_input_queue is not None:
    try:
        _global_input_queue.put({
            'symbol': symbol,
            'ohlcv': ohlcv
        }, block=False)
        print(f"📤 {symbol} OHLCV 已推送到计算进程队列")
    except Exception as queue_e:
        print(f"⚠️ 推送队列失败: {queue_e}")
```

**完整的 `_handle_kline_update` 函数参考：** 见 `websocket_queue_integration.py`

---

### 步骤 4：修改 `main.py`

#### 4.1 添加导入（文件顶部）

在现有导入之后添加：

```python
# 🔥 V7.0 新增：多进程架构
import multiprocessing as mp
from multiprocessing_integration import (
    start_multiprocessing_architecture,
    shutdown_multiprocessing_architecture
)
```

#### 4.2 修改 `start_background_services` 函数

在函数开头（threads = [] 之后）添加：

```python
def start_background_services(client):
    """启动后台服务线程（V7.0 含多进程架构）"""
    logger.info("🚀 启动后台监控线程（V7.0 - 多进程架构）...")
    
    threads = []
    
    # 🔥 V7.0 新增：启动多进程架构
    from config import SYSTEM_CONFIG
    start_multiprocessing_architecture(client, SYSTEM_CONFIG)
    
    # ... 保留其他所有现有代码 ...
```

#### 4.3 修改 `graceful_shutdown` 函数

在函数开头添加：

```python
def graceful_shutdown():
    """优雅退出（V7.0 含多进程架构关闭）"""
    config.TRADING_ENGINE_ACTIVE = False
    logger.info("⏹️ 交易引擎已停止")
    
    # 🔥 V7.0 新增：关闭多进程架构
    shutdown_multiprocessing_architecture()
    
    # ... 保留其他所有现有代码 ...
```

#### 4.4 修改 `if __name__ == "__main__":` 块

替换为：

```python
if __name__ == "__main__":
    # 🔥 V7.0 新增：Windows 多进程支持
    mp.freeze_support()
    main()
```

---

### 步骤 5：配置文件修改

编辑 `bot_config.json`，添加：

```json
{
  "NUM_INDICATOR_WORKERS": 1
}
```

**推荐值：**
- 测试环境：1
- 生产环境（4核）：2
- 生产环境（8核+）：3-4

---

### 步骤 6：测试验证

#### 6.1 语法检查

```bash
python -m py_compile main.py
python -m py_compile websocket_manager.py
python -m py_compile trading_engine.py
python -m py_compile indicator_calculator_worker.py
python -m py_compile multiprocessing_integration.py
```

#### 6.2 启动测试

```bash
python main.py
```

**预期输出：**
```
🚀 正在启动多进程架构...
✅ 多进程队列已初始化
✅ WebSocket 已绑定 input_queue
✅ 指标计算进程 1/1 已启动 (PID: XXXXX)
✅ 信号消费线程已启动
🎉 多进程架构启动完成 (1 个工作进程)
```

#### 6.3 K线推送测试

等待 WebSocket 接收新 K 线，应看到：

```
📈 BTCUSDT 新K线: 45678.1234
📤 BTCUSDT OHLCV 已推送到计算进程队列
```

#### 6.4 信号生成测试

当策略触发时，应看到：

```
📤 [Worker-XXXXX] BTCUSDT 信号已推送
🔔 收到 BTCUSDT 交易信号
```

#### 6.5 优雅关闭测试

按 `Ctrl+C`，应看到：

```
⏹️ 交易引擎已停止
🛑 正在关闭多进程架构...
✅ POISON PILL 已发送
✅ [Worker-XXXXX] 指标计算进程已退出
✅ 多进程架构已关闭
```

---

## 🚨 常见问题排查

### 问题 1：导入错误

**症状：** `ModuleNotFoundError: No module named 'indicator_calculator_worker'`

**解决：**
- 确认 `indicator_calculator_worker.py` 在 `WJ-BOT` 目录下
- 确认文件名拼写正确

### 问题 2：队列未初始化

**症状：** `⚠️ input_queue 未初始化，跳过信号处理`

**解决：**
- 确认 `start_multiprocessing_architecture()` 在 WebSocket 启动之前调用
- 检查 `start_background_services()` 中的调用顺序

### 问题 3：进程无法启动

**症状：** 没有看到 "指标计算进程已启动" 消息

**解决：**
- 检查 `bot_config.json` 中的 `NUM_INDICATOR_WORKERS` 配置
- 确认 `indicator_worker_loop` 函数可以正常导入

### 问题 4：信号不执行

**症状：** 看到 "信号已推送" 但没有交易执行

**解决：**
- 检查 `_signal_consumer_loop` 是否正常运行
- 检查 `process_trading_signals` 函数是否有异常

---

## 📊 性能监控

### 监控指标

1. **队列深度**
   - 正常：0-10
   - 警告：10-50
   - 危险：50+

2. **进程 CPU 使用率**
   - 正常：10-30%
   - 警告：30-60%
   - 危险：60%+

3. **K线处理延迟**
   - 目标：< 10ms
   - 可接受：< 50ms
   - 需优化：> 100ms

### 监控命令（Windows）

```powershell
# 查看进程
tasklist | findstr python

# 查看 CPU 使用率
wmic cpu get loadpercentage
```

---

## 🔄 回滚方案

如果出现严重问题，快速回滚：

```bash
# 恢复备份文件
copy main.py.backup main.py
copy websocket_manager.py.backup websocket_manager.py
copy trading_engine.py.backup trading_engine.py

# 重启系统
python main.py
```

---

## ✅ 完成清单

- [ ] 步骤 1：备份完成
- [ ] 步骤 2：trading_engine.py 修改完成
- [ ] 步骤 3：websocket_manager.py 修改完成
- [ ] 步骤 4：main.py 修改完成
- [ ] 步骤 5：配置文件修改完成
- [ ] 步骤 6.1：语法检查通过
- [ ] 步骤 6.2：启动测试通过
- [ ] 步骤 6.3：K线推送测试通过
- [ ] 步骤 6.4：信号生成测试通过
- [ ] 步骤 6.5：优雅关闭测试通过

---

## 📚 相关文档

- `MULTIPROCESSING_REFACTOR_GUIDE.md` - 详细架构说明
- `indicator_calculator_worker.py` - 工作进程实现
- `multiprocessing_integration.py` - 主进程集成代码
- `websocket_queue_integration.py` - WebSocket 集成代码

---

## 🎉 成功标志

当所有测试通过后，您应该看到：

✅ 系统启动无错误
✅ K线数据正常推送到队列
✅ 工作进程正常计算指标
✅ 交易信号正常执行
✅ 系统可以优雅关闭

**预期性能提升：**
- K线处理延迟：从 50-200ms 降至 5-10ms
- 并发处理能力：提升 2-4 倍
- WebSocket 响应性：显著改善
