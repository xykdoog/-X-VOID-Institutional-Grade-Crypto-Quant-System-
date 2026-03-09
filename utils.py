#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
工具函数模块 - utils.py
负责通用工具函数：网络重试、熔断器、消息发送、精度处理等
"""

import time
import math
import html
import functools
from decimal import Decimal, ROUND_DOWN, InvalidOperation
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

import telebot
from telebot import apihelper

from logger_setup import logger

from config import (
    SYSTEM_CONFIG, NETWORK_CONFIG,
    circuit_breaker_state, circuit_breaker_lock,
    symbol_precisions, price_precisions, tick_sizes,
    all_symbols_cache, SYMBOLS_CACHE_DURATION,
    cache_lock,
)
import config  # 用于修改模块级变量

# ==========================================
# 消息发送线程池
# ==========================================
MESSAGE_THREAD_POOL = ThreadPoolExecutor(
    max_workers=5,
    thread_name_prefix="tg_msg_"
)

# ==========================================
# 价格缓存系统 (极速读取，缓解 API 请求压力)
# ==========================================
import threading

_global_price_cache = {
    'timestamp': 0,
    'prices': {}
}
_price_cache_lock = threading.Lock()
PRICE_CACHE_TTL = 1.5  # 缓存生命周期：1.5秒

# ==========================================
# 熔断器
# ==========================================

def check_circuit_breaker():
    """
    检查熔断器状态（机构级三态状态机 v2.0）
    
    状态转换逻辑：
    - CLOSED: 正常运行，允许所有请求
    - OPEN: 熔断开启，拒绝所有请求
    - HALF_OPEN: 试探性恢复，允许单个试探请求
    
    Returns:
        bool: True=熔断中（拒绝请求），False=允许请求
    """
    with circuit_breaker_lock:
        if circuit_breaker_state["state"] == "CLOSED":
            return False
            
        if circuit_breaker_state["last_failure_time"]:
            elapsed = (datetime.now() - circuit_breaker_state["last_failure_time"]).total_seconds()
            
            # 冷却期结束，进入半开状态
            if elapsed > NETWORK_CONFIG["CIRCUIT_BREAKER_TIMEOUT"]:
                if circuit_breaker_state["state"] == "OPEN":
                    logger.info("⏳ 熔断器冷却结束，进入 HALF_OPEN (半开) 试探状态...")
                    circuit_breaker_state["state"] = "HALF_OPEN"
                    circuit_breaker_state["half_open_testing"] = False
                
                if circuit_breaker_state["state"] == "HALF_OPEN":
                    if not circuit_breaker_state["half_open_testing"]:
                        # 允许第一个请求去试探网络
                        circuit_breaker_state["half_open_testing"] = True
                        return False
                    else:
                        # 试探请求还没返回前，拦截其他并发请求
                        return True
        
        return True


def record_failure():
    """记录失败并触发状态流转"""
    with circuit_breaker_lock:
        circuit_breaker_state["failures"] += 1
        circuit_breaker_state["last_failure_time"] = datetime.now()
        
        if circuit_breaker_state["state"] == "HALF_OPEN":
            # 试探请求失败，立即打回 OPEN 状态
            circuit_breaker_state["state"] = "OPEN"
            circuit_breaker_state["half_open_testing"] = False
            logger.warning("🔴 试探请求失败，熔断器重新进入 OPEN 状态！")
            
        elif circuit_breaker_state["state"] == "CLOSED":
            if circuit_breaker_state["failures"] >= NETWORK_CONFIG["CIRCUIT_BREAKER_THRESHOLD"]:
                circuit_breaker_state["state"] = "OPEN"
                logger.warning(f"🔴 熔断器已开启！连续失败 {circuit_breaker_state['failures']} 次")
                logger.info(f"将在 {NETWORK_CONFIG['CIRCUIT_BREAKER_TIMEOUT']} 秒后进入试探状态")


def reset_circuit_breaker():
    """重置为关闭状态"""
    with circuit_breaker_lock:
        if circuit_breaker_state["state"] != "CLOSED" or circuit_breaker_state["failures"] > 0:
            if circuit_breaker_state["state"] == "HALF_OPEN":
                logger.info("✅ 试探请求成功，熔断器全面恢复为 CLOSED 状态！")
            circuit_breaker_state["state"] = "CLOSED"
            circuit_breaker_state["failures"] = 0
            circuit_breaker_state["last_failure_time"] = None
            circuit_breaker_state["half_open_testing"] = False


def retry_on_failure(max_retries=None, retry_delay=None, timeout=None, operation_name="API调用"):
    """统一的重试装饰器（集成 API 权重监控）"""
    if max_retries is None:
        max_retries = NETWORK_CONFIG["MAX_RETRIES"]
    if retry_delay is None:
        retry_delay = NETWORK_CONFIG["RETRY_DELAY"]
    if timeout is None:
        timeout = NETWORK_CONFIG["API_TIMEOUT"]
    
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if check_circuit_breaker():
                raise Exception(f"熔断器已开启，{operation_name}暂时不可用")
            
            last_exception = None
            
            for attempt in range(max_retries):
                try:
                    if 'timeout' not in kwargs:
                        kwargs['timeout'] = timeout
                    
                    result = func(*args, **kwargs)
                    reset_circuit_breaker()
                    
                    # 🔥 集成 API 权重监控：从响应头中提取权重
                    try:
                        if hasattr(result, 'headers'):
                            from api_weight_monitor import get_api_weight_monitor
                            monitor = get_api_weight_monitor()
                            monitor.update_weight(result.headers)
                    except Exception as monitor_err:
                        logger.debug(f"API 权重监控更新失败（非致命）: {monitor_err}")
                    
                    if attempt > 0:
                        logger.info(f"{operation_name}成功 (尝试 {attempt+1}/{max_retries})")
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    error_msg = str(e)
                    
                    logger.warning(f"{operation_name}失败 (尝试 {attempt+1}/{max_retries}): {error_msg[:100]}")
                    record_failure()
                    
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.info(f"等待 {wait_time} 秒后重试...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"{operation_name}最终失败，已达到最大重试次数", exc_info=True)
            
            raise last_exception
        
        return wrapper
    return decorator


# ==========================================
# 消息发送函数（依赖 bot 实例，通过参数传入）
# ==========================================

# 全局 bot 引用，在 main.py 中设置
_bot_instance = None

def set_bot_instance(bot):
    """设置全局 bot 实例"""
    global _bot_instance
    _bot_instance = bot

def get_bot():
    """获取全局 bot 实例"""
    return _bot_instance


def _threaded_send_message(chat_id, message, max_retries=3):
    """线程化发送消息"""
    from telebot.apihelper import ApiTelegramException
    
    bot = get_bot()
    if bot is None:
        logger.info(f"[离线模式] 模拟发送TG消息: {message[:50].replace(chr(10), ' ')}...")
        return False
    
    for attempt in range(max_retries):
        try:
            if len(message) > 4096:
                logger.warning(f"消息过长 ({len(message)}字符)，自动截断...")
                message = message[:4000] + "\n\n...(消息已截断)"
            
            bot.send_message(chat_id, message, parse_mode="HTML")
            logger.info(f"Telegram消息发送成功: {message[:50].replace(chr(10), ' ')}...")
            return True
            
        except ApiTelegramException as e:
            error_code = e.error_code
            error_msg = str(e)
            
            if error_code == 403:
                logger.warning(f"TG发送失败: 用户已屏蔽bot (chat_id: {chat_id})")
                return False
            elif error_code == 400 and "chat not found" in error_msg.lower():
                logger.warning(f"TG发送失败: 聊天不存在 (chat_id: {chat_id})")
                return False
            
            logger.warning(f"TG发送失败 (尝试 {attempt+1}/{max_retries}): {error_msg[:100]}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                logger.error("TG发送最终失败", exc_info=True)
                return False
                
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"TG发送异常 (尝试 {attempt+1}/{max_retries}): {error_msg[:100]}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                logger.error(f"TG发送最终异常失败: {error_msg}", exc_info=True)
                return False
    
    return False


def send_tg_msg(message, chat_id=None, parse_mode="HTML"):
    """全局推送消息引擎"""
    if not SYSTEM_CONFIG.get("SIGNALS_ENABLED", True):
        return
    
    if chat_id is None:
        chat_id = SYSTEM_CONFIG.get("TG_CHAT_ID", "")
    
    bot = get_bot()
    if bot is None or not chat_id:
        logger.info(f"[离线模式] 模拟发送TG消息: {message[:50].replace(chr(10), ' ')}...")
        return
    
    try:
        MESSAGE_THREAD_POOL.submit(_threaded_send_message, chat_id, message)
    except Exception as e:
        logger.error(f"提交消息发送任务失败: {e}", exc_info=True)

# 兼容旧代码
send_tg_alert = send_tg_msg


def shutdown_message_pool():
    """优雅关闭消息发送线程池"""
    logger.info("正在关闭消息发送线程池...")
    try:
        MESSAGE_THREAD_POOL.shutdown(wait=True)
        logger.info("消息发送线程池已关闭")
    except Exception as e:
        logger.error(f"关闭消息发送线程池时出错: {e}", exc_info=True)


# ==========================================
# 安全的 bot 操作包装函数
# ==========================================

def safe_send_message(chat_id, text, **kwargs):
    """安全的发送消息包装"""
    bot = get_bot()
    if bot is None:
        return None
    if len(text) > 4096:
        text = text[:4000] + "\n\n...(消息已截断)"
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.warning(f"发送消息失败: {str(e)[:100]}")
        return None


def safe_edit_message(chat_id, message_id, text, **kwargs):
    """安全的编辑消息包装"""
    bot = get_bot()
    if bot is None:
        return None
    if len(text) > 4096:
        text = text[:4000] + "\n\n...(消息已截断)"
    try:
        return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kwargs)
    except Exception as e:
        logger.warning(f"编辑消息失败: {str(e)[:100]}")
        return None


def safe_delete_message(chat_id, message_id):
    """安全的删除消息包装"""
    bot = get_bot()
    if bot is None:
        return None
    try:
        return bot.delete_message(chat_id, message_id)
    except Exception as e:
        logger.warning(f"删除消息失败: {str(e)[:100]}")
        return None


def safe_answer_callback(call_id, text=None, show_alert=False):
    """安全的回答回调查询包装"""
    bot = get_bot()
    if bot is None:
        return None
    try:
        return bot.answer_callback_query(call_id, text, show_alert)
    except Exception as e:
        logger.warning(f"回答回调失败: {str(e)[:100]}")
        return None


# ==========================================
# 价格和精度处理工具
# ==========================================

def _to_decimal(value):
    """安全地将 float/str/int 转换为 Decimal，避免 float 的 repr 精度污染"""
    try:
        if isinstance(value, Decimal):
            return value
        # 🔥 关键：用 str() 桥接 float -> Decimal，截断 IEEE 754 噪声
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        logger.warning(f"Decimal 转换失败: {value}，回退为 0")
        return Decimal('0')


def round_to_tick_size(price, symbol):
    """根据tickSize精度要求截断价格（Decimal 精确版 v2.0）
    
    🔥 100% 使用 Python decimal 模块替代 math.floor 浮点运算
    彻底消除 float 除法产生的 .999999 尾数，确保输出结果严格符合币安 API 规范
    """
    try:
        # 优先使用 tickSize（PRICE_FILTER 规则）
        if symbol in tick_sizes:
            d_price = _to_decimal(price)
            d_tick = _to_decimal(tick_sizes[symbol])
            if d_tick > 0:
                # 核心：Decimal 整除后乘回，等价于 floor(price/tick)*tick 但无浮点噪声
                # 使用 to_integral_value(ROUND_DOWN) 确保向下取整
                rounded = (d_price / d_tick).to_integral_value(rounding=ROUND_DOWN) * d_tick
                # 使用 quantize 对齐到 tickSize 的精度位数（消除尾部多余零）
                rounded = rounded.quantize(d_tick, rounding=ROUND_DOWN)
                result = float(rounded)
                logger.debug(f"{symbol} 价格精度处理(Decimal): {price} -> {result} (tickSize={tick_sizes[symbol]})")
                return result
        
        # 回退到传统精度处理
        if symbol in price_precisions:
            precision = price_precisions[symbol]
            d_price = _to_decimal(price)
            quantizer = Decimal(10) ** -precision  # e.g. Decimal('0.01') for precision=2
            result = float(d_price.quantize(quantizer, rounding=ROUND_DOWN))
            logger.debug(f"{symbol} 价格精度处理(Decimal传统): {price} -> {result} (precision={precision})")
            return result
        
        # 最终兜底：默认2位小数
        logger.warning(f"未找到 {symbol} 的价格精度信息，使用默认2位小数")
        return float(_to_decimal(price).quantize(Decimal('0.01'), rounding=ROUND_DOWN))
    
    except Exception as e:
        logger.error(f"❌ 价格精度处理异常 {symbol}: {e}, 原始值={price}")
        # 异常兜底：返回保守的2位小数
        return float(_to_decimal(price).quantize(Decimal('0.01'), rounding=ROUND_DOWN))


def round_to_quantity_precision(quantity, symbol):
    """根据数量精度要求截断数量（Decimal 精确版 v2.0，严格遵循stepSize规则）
    
    🔥 100% 使用 Python decimal 模块替代 math.floor 浮点运算
    彻底废弃 math.floor(float/float)*float 的浮点陷阱，
    使用 Decimal.quantize(ROUND_DOWN) 确保结果绝对符合 Binance LOT_SIZE 的 stepSize。
    杜绝粉尘余额和 -1013 报错。
    """
    try:
        # 优先使用 LOT_SIZE 的 stepSize 进行精度处理
        if hasattr(config, 'quantity_step_sizes') and symbol in config.quantity_step_sizes:
            step_size = config.quantity_step_sizes[symbol]
            if step_size > 0:
                d_qty = _to_decimal(quantity)
                d_step = _to_decimal(step_size)
                
                # 核心：Decimal 整除 + ROUND_DOWN，零浮点噪声
                final_qty = (d_qty / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step
                
                # 用 quantize 对齐到 stepSize 的精度位数（消除尾部多余零）
                final_qty = final_qty.quantize(d_step, rounding=ROUND_DOWN)
                
                result = float(final_qty)
                logger.debug(f"{symbol} 数量精度处理(Decimal): {quantity} -> {result} (stepSize={step_size})")
                return result
        
        # 如果没有 stepSize，使用传统精度处理
        if symbol in symbol_precisions:
            precision = symbol_precisions[symbol]
            d_qty = _to_decimal(quantity)
            quantizer = Decimal(10) ** -precision  # e.g. Decimal('0.001') for precision=3
            final_qty = d_qty.quantize(quantizer, rounding=ROUND_DOWN)
            result = float(final_qty)
            logger.debug(f"{symbol} 数量精度处理(Decimal传统): {quantity} -> {result} (precision={precision})")
            return result
        
        # 最终兜底：默认3位小数
        logger.warning(f"未找到 {symbol} 的数量精度信息，使用默认3位小数")
        return float(_to_decimal(quantity).quantize(Decimal('0.001'), rounding=ROUND_DOWN))
    
    except Exception as e:
        logger.error(f"❌ 数量精度处理异常 {symbol}: {e}, 原始值={quantity}")
        # 异常兜底：返回保守的3位小数
        return float(_to_decimal(quantity).quantize(Decimal('0.001'), rounding=ROUND_DOWN))


def get_all_valid_symbols(client):
    """获取所有有效的合约交易对及精度（包含stepSize）"""
    now = time.time()
    if (config.all_symbols_cache and config.symbols_cache_time and 
        (now - config.symbols_cache_time < SYMBOLS_CACHE_DURATION)):
        return config.all_symbols_cache
    
    if client:
        try:
            exchange_info = client.futures_exchange_info()
            config.all_symbols_cache = []
            for s in exchange_info['symbols']:
                if s['status'] == 'TRADING':
                    symbol = s['symbol']
                    config.all_symbols_cache.append(symbol)
                    config.symbol_precisions[symbol] = s.get('quantityPrecision', 3)
                    config.price_precisions[symbol] = s.get('pricePrecision', 2)
                    
                    # 提取 PRICE_FILTER 和 LOT_SIZE 过滤器
                    for filter_item in s.get('filters', []):
                        if filter_item['filterType'] == 'PRICE_FILTER':
                            config.tick_sizes[symbol] = float(filter_item.get('tickSize', 0.01))
                        elif filter_item['filterType'] == 'LOT_SIZE':
                            # 使用 LOT_SIZE 的 stepSize 作为数量精度标准
                            step_size = float(filter_item.get('stepSize', 0.001))
                            # 将 stepSize 存储到 tick_sizes（复用字典，或创建新字典）
                            # 这里我们用一个新的字典来存储数量的 stepSize
                            if not hasattr(config, 'quantity_step_sizes'):
                                config.quantity_step_sizes = {}
                            config.quantity_step_sizes[symbol] = step_size
            
            config.symbols_cache_time = now
            logger.info(f"已加载 {len(config.all_symbols_cache)} 个交易对的精度信息（含stepSize）")
            return config.all_symbols_cache
        except Exception as e:
            logger.warning(f"获取交易对列表失败: {e}")
    return []


def _fetch_batch_prices(client, symbols):
    """
    批量获取多个币种价格（内部函数，使用 bookTicker 接口）
    
    Args:
        client: Binance client 实例
        symbols: 交易对列表
    
    Returns:
        dict: {symbol: price} 映射表
    """
    try:
        # 使用 bookTicker 接口批量获取（权重：每个交易对 2，但可批量）
        tickers = client.get_orderbook_tickers()
        
        # 构建价格映射表（只保留需要的交易对）
        symbol_set = set(symbols) if symbols else set()
        price_map = {}
        
        for ticker in tickers:
            sym = ticker['symbol']
            if not symbol_set or sym in symbol_set:
                # 使用买一价和卖一价的中间价作为当前价格
                bid = float(ticker['bidPrice'])
                ask = float(ticker['askPrice'])
                price_map[sym] = (bid + ask) / 2.0
        
        logger.debug(f"批量获取 {len(price_map)} 个交易对价格")
        return price_map
        
    except Exception as e:
        logger.error(f"批量获取价格失败: {e}")
        return {}


def _refresh_price_cache(client, symbols=None):
    """
    刷新全局价格缓存（线程安全）
    
    Args:
        client: Binance client 实例
        symbols: 需要刷新的交易对列表（None=刷新全部）
    
    Returns:
        bool: 刷新是否成功
    """
    global _global_price_cache
    
    try:
        # 批量获取价格
        price_map = _fetch_batch_prices(client, symbols)
        
        if not price_map:
            logger.warning("批量获取价格返回空结果")
            return False
        
        # 更新缓存（加锁保护）
        with _price_cache_lock:
            _global_price_cache['prices'].update(price_map)
            _global_price_cache['timestamp'] = time.time()
        
        logger.debug(f"价格缓存已刷新，包含 {len(price_map)} 个交易对")
        return True
        
    except Exception as e:
        logger.error(f"刷新价格缓存失败: {e}")
        return False


def get_current_price(client, symbol):
    """获取指定币种的当前价格（机构级优化：带1.5秒全局缓存与批量查询）"""
    if client is None:
        logger.info(f"模拟获取 {symbol} 价格")
        return 50000.0 if symbol == "BTCUSDT" else 3000.0 if symbol == "ETHUSDT" else 100.0
    
    # 1. 尝试从缓存读取（1.5秒有效期）
    with _price_cache_lock:
        if time.time() - _global_price_cache['timestamp'] < 1.5:
            if symbol in _global_price_cache['prices']:
                return _global_price_cache['prices'][symbol]
    
    # 2. 缓存失效，触发全量 API 请求
    max_retries = NETWORK_CONFIG["MAX_RETRIES"]
    retry_delay = NETWORK_CONFIG["RETRY_DELAY"]
    
    for attempt in range(max_retries):
        try:
            # 🔥 核心优化：一次性拉取全市场最新价格，替代逐个拉取
            tickers = client.futures_symbol_ticker()
            
            with _price_cache_lock:
                for t in tickers:
                    _global_price_cache['prices'][t['symbol']] = float(t['price'])
                _global_price_cache['timestamp'] = time.time()
                
            reset_circuit_breaker()
            
            return _global_price_cache['prices'].get(symbol, 0.0)
            
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"批量获取价格失败 (尝试 {attempt+1}/{max_retries}): {error_msg[:100]}")
            record_failure()
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                time.sleep(wait_time)
            else:
                logger.error("批量获取价格最终失败", exc_info=True)
                raise


def invalidate_price_cache():
    """
    手动清空价格缓存（用于强制刷新或异常恢复）
    
    使用场景：
    - 检测到价格数据异常时
    - 系统重启或重新连接时
    - 手动触发全量刷新时
    """
    global _global_price_cache
    
    with _price_cache_lock:
        _global_price_cache['timestamp'] = 0
        _global_price_cache['prices'].clear()
        logger.info("🔄 价格缓存已手动清空")


def get_cache_stats():
    """
    获取价格缓存统计信息
    
    Returns:
        dict: {
            'cache_size': int,        # 缓存的交易对数量
            'cache_age': float,       # 缓存年龄（秒）
            'is_valid': bool,         # 缓存是否有效
            'ttl': float,             # 缓存TTL（秒）
        }
    """
    global _global_price_cache
    
    with _price_cache_lock:
        current_time = time.time()
        cache_age = current_time - _global_price_cache['timestamp']
        
        return {
            'cache_size': len(_global_price_cache['prices']),
            'cache_age': cache_age,
            'is_valid': cache_age < PRICE_CACHE_TTL,
            'ttl': PRICE_CACHE_TTL,
        }


def get_24h_change(client, symbol):
    """获取24小时价格变化百分比"""
    if client is None:
        return 0.02
    
    max_retries = NETWORK_CONFIG["MAX_RETRIES"]
    retry_delay = NETWORK_CONFIG["RETRY_DELAY"]
    
    for attempt in range(max_retries):
        try:
            ticker = client.get_ticker(symbol=symbol)
            change = float(ticker['priceChangePercent']) / 100
            reset_circuit_breaker()
            return change
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"获取24h行情失败 (尝试 {attempt+1}/{max_retries}): {error_msg[:100]}")
            record_failure()
            if attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)
                time.sleep(wait_time)
            else:
                logger.error("获取24h行情最终失败", exc_info=True)
                raise


def search_symbols_fuzzy(client, keyword):
    """模糊搜索币种（支持部分匹配，返回所有USDT合约）"""
    all_symbols = get_all_valid_symbols(client)
    keyword_upper = keyword.upper()
    
    # 筛选包含关键词且以USDT结尾的合约
    matches = [s for s in all_symbols if keyword_upper in s and s.endswith('USDT')]
    
    # 优先排序：完全匹配 > 开头匹配 > 包含匹配
    exact_match = [s for s in matches if s == f"{keyword_upper}USDT"]
    starts_with = [s for s in matches if s.startswith(keyword_upper) and s not in exact_match]
    contains = [s for s in matches if s not in exact_match and s not in starts_with]
    
    return exact_match + starts_with + contains


def normalize_weights(client=None, chat_id=None):
    """自动归一化资产权重"""
    bot = get_bot()
    total = sum(SYSTEM_CONFIG["ASSET_WEIGHTS"].values())
    if total == 0:
        return
    if abs(total - 1.0) > 0.001:
        for k in SYSTEM_CONFIG["ASSET_WEIGHTS"]:
            SYSTEM_CONFIG["ASSET_WEIGHTS"][k] = round(SYSTEM_CONFIG["ASSET_WEIGHTS"][k] / total, 4)
        
        current_total = sum(SYSTEM_CONFIG["ASSET_WEIGHTS"].values())
        if abs(current_total - 1.0) > 0.0001 and len(SYSTEM_CONFIG["ASSET_WEIGHTS"]) > 0:
            last_sym = list(SYSTEM_CONFIG["ASSET_WEIGHTS"].keys())[-1]
            SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] = round(
                SYSTEM_CONFIG["ASSET_WEIGHTS"][last_sym] + (1.0 - current_total), 4
            )
        
        from config import save_data
        save_data()
        
        msg = "⚖️ <b>资产权重已自动归一化 (总和为 1.0)</b>\n\n<b>当前权重:</b>\n"
        for k, v in SYSTEM_CONFIG["ASSET_WEIGHTS"].items():
            msg += f"• {k}: {round(v*100, 2)}%\n"
        if chat_id and bot:
            bot.send_message(chat_id, msg, parse_mode="HTML")
        else:
            send_tg_msg(msg)


def create_progress_bar(value, max_value, length=10):
    """创建进度条"""
    if max_value == 0:
        filled = 0
    else:
        filled = int((abs(value) / max_value) * length)
        filled = min(filled, length)
    
    bar = "█" * filled + "░" * (length - filled)
    return f"[{bar}]"


# ==========================================
# 保险库资金划转核心逻辑（自适应动态阈值引擎 v2.0）
# ==========================================

def calculate_dynamic_vault_ratio():
    """
    计算自适应动态保险库触发比例
    
    基于凯利公式(Kelly Factor)和当前回撤率，自动上下调节触发抽水的利润比例。
    
    策略逻辑：
    - 顺风局（Kelly > 1.0）：扩张阈值，让利润跑得更远再抽水
    - 逆风局（Kelly < 1.0）：收缩阈值，及时落袋为安
    - 回撤介入（drawdown > 5%）：强制降至最低阈值（防守优先）
    
    Returns:
        dict: {
            'dynamic_ratio': float,       # 最终生效的动态触发比例
            'kelly_factor': float,         # 当前凯利系数
            'base_ratio': float,           # 基准比例
            'drawdown_pct': float,         # 当前回撤率 %
            'drawdown_override': bool,     # 是否触发回撤强制介入
            'regime': str,                 # 市场状态: '顺风局扩张' / '逆风局收缩' / '回撤防守'
            'auto_adapt_enabled': bool,    # 自适应是否启用
        }
    """
    from config import SYSTEM_CONFIG
    
    base_ratio = SYSTEM_CONFIG.get("VAULT_BASE_RATIO", 0.15)
    min_ratio = SYSTEM_CONFIG.get("VAULT_MIN_RATIO", 0.05)
    max_ratio = SYSTEM_CONFIG.get("VAULT_MAX_RATIO", 0.30)
    auto_adapt = SYSTEM_CONFIG.get("VAULT_AUTO_ADAPT", True)
    
    # 默认结果（自适应关闭时使用固定阈值）
    result = {
        'dynamic_ratio': base_ratio,
        'kelly_factor': 1.0,
        'base_ratio': base_ratio,
        'drawdown_pct': 0.0,
        'drawdown_override': False,
        'regime': '固定阈值',
        'auto_adapt_enabled': auto_adapt,
    }
    
    if not auto_adapt:
        return result
    
    # ====== Step 1: 获取凯利系数 ======
    kelly_factor = 1.0
    try:
        from trading_engine import get_performance_stats
        perf_stats = get_performance_stats(50)
        kelly_factor = perf_stats.get('kelly_factor', 1.0)
        # 安全兜底：避免极端值
        kelly_factor = max(0.1, min(3.0, kelly_factor))
    except Exception as e:
        logger.warning(f"获取凯利系数失败，使用默认值 1.0: {e}")
        kelly_factor = 1.0
    
    result['kelly_factor'] = kelly_factor
    
    # ====== Step 2: 计算动态阈值 = 基准比例 × 凯利系数 ======
    dynamic_ratio = base_ratio * kelly_factor
    
    # 边界截断：确保在 [MIN_RATIO, MAX_RATIO] 区间
    dynamic_ratio = max(min_ratio, min(max_ratio, dynamic_ratio))
    
    # 判断市场状态
    if kelly_factor >= 1.0:
        result['regime'] = '顺风局扩张'
    else:
        result['regime'] = '逆风局收缩'
    
    # ====== Step 3: 回撤介入机制 ======
    peak_equity = SYSTEM_CONFIG.get("PEAK_EQUITY", 0.0)
    benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
    
    # 计算当前回撤率（距离 PEAK_EQUITY 的回撤）
    drawdown_pct = 0.0
    if peak_equity > 0:
        # 使用 benchmark 作为当前净值的近似（因为此函数不接收 client）
        # 实际在 execute_vault_transfer 中会用真实余额覆盖此判断
        drawdown_pct = ((peak_equity - benchmark) / peak_equity * 100) if benchmark < peak_equity else 0.0
    
    result['drawdown_pct'] = drawdown_pct
    
    # 如果回撤 > 5% 且当前净值 > BENCHMARK_CASH，强制降至最低阈值（落袋为安）
    if drawdown_pct > 5.0:
        dynamic_ratio = min_ratio
        result['drawdown_override'] = True
        result['regime'] = '回撤防守'
    
    result['dynamic_ratio'] = dynamic_ratio
    return result


def execute_vault_transfer(client):
    """
    执行保险库资金划转（合约 -> 现货）- 自适应动态阈值引擎 v2.0
    
    触发条件（自适应模式）：
    1. VAULT_ENABLED = True
    2. 当前利润 >= BENCHMARK_CASH × 动态阈值
    3. 划转金额 >= 5.0 U（最低划转门槛）
    
    动态阈值引擎：
    - 引入凯利公式(Kelly Factor)数据源
    - dynamic_ratio = VAULT_BASE_RATIO × kelly_factor
    - 边界截断: [VAULT_MIN_RATIO, VAULT_MAX_RATIO]
    - 回撤介入: drawdown > 5% 时强制降至 VAULT_MIN_RATIO
    
    Returns:
        dict: {
            'success': bool,
            'amount': float,
            'vault_balance': float,
            'new_benchmark': float,
            'message': str,
            'adaptive_info': dict  # 自适应引擎信息
        }
    """
    from config import SYSTEM_CONFIG, state_lock, save_data
    import config
    
    # 默认的自适应信息
    adaptive_info = {}
    
    try:
        # 检查保险库是否启用
        vault_enabled = SYSTEM_CONFIG.get("VAULT_ENABLED", False)
        if not vault_enabled:
            return {
                'success': False,
                'amount': 0,
                'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                'new_benchmark': SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0),
                'message': "保险库未启用",
                'adaptive_info': {}
            }
        
        # 获取当前合约账户余额
        if client and not SYSTEM_CONFIG.get("DRY_RUN", False) and not config.VERIFICATION_MODE:
            try:
                acc = client.futures_account()
                current_balance = float(acc['totalMarginBalance'])
            except Exception as e:
                logger.error(f"获取合约账户余额失败: {e}")
                return {
                    'success': False,
                    'amount': 0,
                    'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                    'new_benchmark': SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0),
                    'message': f"获取账户余额失败: {str(e)[:50]}",
                    'adaptive_info': {}
                }
        else:
            # 模拟模式：使用模拟余额
            current_balance = SYSTEM_CONFIG.get("SIM_CURRENT_BALANCE", SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0))
        
        # 计算净利润
        benchmark = SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0)
        net_profit = current_balance - benchmark
        
        # ====== 🔥 自适应动态阈值计算 ======
        adaptive_info = calculate_dynamic_vault_ratio()
        auto_adapt = SYSTEM_CONFIG.get("VAULT_AUTO_ADAPT", True)
        
        if auto_adapt:
            dynamic_ratio = adaptive_info['dynamic_ratio']
            
        # 🔥 回撤介入机制（使用真实余额重新计算）
        # 🔒 使用 state_lock 保护 PEAK_EQUITY 读取
        with state_lock:
            peak_equity = SYSTEM_CONFIG.get("PEAK_EQUITY", 0.0)
        
        if peak_equity > 0 and current_balance < peak_equity:
            real_drawdown_pct = (peak_equity - current_balance) / peak_equity * 100
            adaptive_info['drawdown_pct'] = real_drawdown_pct
            
            # 如果回撤 > 5% 且当前净值 > BENCHMARK_CASH，强制降至最低阈值
            if real_drawdown_pct > 5.0 and current_balance > benchmark:
                min_ratio = SYSTEM_CONFIG.get("VAULT_MIN_RATIO", 0.05)
                dynamic_ratio = min_ratio
                adaptive_info['dynamic_ratio'] = dynamic_ratio
                adaptive_info['drawdown_override'] = True
                adaptive_info['regime'] = '回撤防守'
            
            # 动态阈值触发条件：净利润 >= 基准本金 × 动态比例
            vault_thr_dynamic = benchmark * dynamic_ratio
            
            logger.info(
                f"🤖 自适应保险库引擎 | Kelly={adaptive_info['kelly_factor']:.2f} | "
                f"基准比例={adaptive_info['base_ratio']*100:.1f}% -> 动态={dynamic_ratio*100:.1f}% | "
                f"阈值=${vault_thr_dynamic:.2f} | 回撤={adaptive_info['drawdown_pct']:.1f}% | "
                f"状态={adaptive_info['regime']}"
            )
            
            # 检查是否达到动态触发阈值
            if net_profit < vault_thr_dynamic:
                return {
                    'success': False,
                    'amount': 0,
                    'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                    'new_benchmark': benchmark,
                    'message': f"净利润 ${net_profit:.2f} 未达动态阈值 ${vault_thr_dynamic:.2f} ({dynamic_ratio*100:.1f}%)",
                    'adaptive_info': adaptive_info
                }
        else:
            # 固定阈值模式（向后兼容）
            vault_thr = SYSTEM_CONFIG.get("VAULT_THR", 250.0)
            if net_profit < vault_thr:
                return {
                    'success': False,
                    'amount': 0,
                    'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                    'new_benchmark': benchmark,
                    'message': f"净利润 ${net_profit:.2f} 未达到阈值 ${vault_thr:.2f}",
                    'adaptive_info': adaptive_info
                }
        
        # 计算划转金额
        withdraw_ratio = SYSTEM_CONFIG.get("WITHDRAW_RATIO", 0.5)
        transfer_amount = net_profit * withdraw_ratio
        
        # 防御性检查：划转金额必须 >= 5.0 U（最低划转门槛）
        if transfer_amount < 5.0:
            return {
                'success': False,
                'amount': 0,
                'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                'new_benchmark': benchmark,
                'message': f"划转金额 ${transfer_amount:.2f} 低于最低门槛 $5.00",
                'adaptive_info': adaptive_info
            }
        
        # 执行物理划转（实盘模式）
        transfer_executed = False
        if client and not SYSTEM_CONFIG.get("DRY_RUN", False) and not config.VERIFICATION_MODE:
            try:
                # 调用币安API：合约转现货 (type=2)
                asset = SYSTEM_CONFIG.get("VAULT_ASSET", "USDT")
                response = client.futures_account_transfer(
                    asset=asset,
                    amount=transfer_amount,
                    type=2  # 1=现货->合约, 2=合约->现货
                )
                
                logger.info(f"✅ 保险库划转成功: {transfer_amount:.2f} {asset} (tranId: {response.get('tranId', 'N/A')})")
                transfer_executed = True
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"❌ 保险库划转API调用失败: {error_msg}")
                send_tg_alert(
                    f"🚨 <b>[保险库划转失败]</b>\n\n"
                    f"划转金额: ${transfer_amount:.2f}\n"
                    f"错误: {error_msg[:200]}\n\n"
                    f"⚠️ 请检查API权限或账户余额！"
                )
                return {
                    'success': False,
                    'amount': 0,
                    'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                    'new_benchmark': benchmark,
                    'message': f"划转API调用失败: {error_msg[:100]}",
                    'adaptive_info': adaptive_info
                }
        else:
            # 模拟模式：直接标记为成功
            logger.info(f"🧪 模拟保险库划转: {transfer_amount:.2f} USDT")
            transfer_executed = True
        
        # 🔒 更新状态（使用 state_lock 保护所有 PEAK_EQUITY 写操作）
        if transfer_executed:
            with state_lock:
                # 累加到保险库余额
                SYSTEM_CONFIG["VAULT_BALANCE"] = SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0) + transfer_amount
                
                # 上调基准本金（建立新的高水位）
                new_benchmark = benchmark + transfer_amount
                SYSTEM_CONFIG["BENCHMARK_CASH"] = new_benchmark
                
                # 🔥 重置 PEAK_EQUITY 为新的 BENCHMARK_CASH（划转后重新开始追踪）
                SYSTEM_CONFIG["PEAK_EQUITY"] = new_benchmark
                
                save_data()
            
            new_vault_balance = SYSTEM_CONFIG["VAULT_BALANCE"]
            new_benchmark = SYSTEM_CONFIG["BENCHMARK_CASH"]
            
            # 构建自适应信息通知
            adapt_msg = ""
            if auto_adapt:
                adapt_msg = (
                    f"\n🤖 <b>自适应引擎:</b>\n"
                    f"├ Kelly系数: <code>{adaptive_info['kelly_factor']:.2f}x</code>\n"
                    f"├ 动态阈值: <code>{adaptive_info['dynamic_ratio']*100:.1f}%</code>\n"
                    f"└ 市场判断: {adaptive_info['regime']}\n"
                )
            
            # 发送成功通知
            send_tg_alert(
                f"💰 <b>[保险库划转成功]</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
                f"✅ 本次划转: <code>${transfer_amount:.2f}</code>\n"
                f"🏦 累计保险库余额: <code>${new_vault_balance:.2f}</code>\n"
                f"📊 新基准本金: <code>${new_benchmark:.2f}</code>\n"
                f"🎯 PEAK_EQUITY 已重置为: <code>${new_benchmark:.2f}</code>\n"
                f"{adapt_msg}\n"
                f"💡 利润已安全转移到现货账户，降低回撤风险！"
            )
            
            return {
                'success': True,
                'amount': transfer_amount,
                'vault_balance': new_vault_balance,
                'new_benchmark': new_benchmark,
                'message': f"成功划转 ${transfer_amount:.2f} 到现货账户",
                'adaptive_info': adaptive_info
            }
        else:
            return {
                'success': False,
                'amount': 0,
                'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
                'new_benchmark': benchmark,
                'message': "划转未执行（未知原因）",
                'adaptive_info': adaptive_info
            }
        
    except Exception as e:
        logger.error(f"❌ 保险库划转执行异常: {e}", exc_info=True)
        return {
            'success': False,
            'amount': 0,
            'vault_balance': SYSTEM_CONFIG.get("VAULT_BALANCE", 0.0),
            'new_benchmark': SYSTEM_CONFIG.get("BENCHMARK_CASH", 1800.0),
            'message': f"执行异常: {str(e)[:100]}",
            'adaptive_info': adaptive_info
        }


# ==========================================
# 🔥 异步可视化战报生成器
# ==========================================

def generate_trade_chart(symbol, direction, entry_price, exit_price, pnl, trade_id="", timestamp=None):
    """
    生成交易可视化图表（matplotlib Agg 后端，线程安全）
    
    Args:
        symbol: 交易对
        direction: 方向 ('LONG' or 'SHORT')
        entry_price: 开仓价
        exit_price: 平仓价
        pnl: 盈亏金额
        trade_id: 交易ID
        timestamp: 时间戳（可选）
    
    Returns:
        str: 图表文件路径，失败返回空字符串
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # 🔥 无头后端，线程安全
        import matplotlib.pyplot as plt
        import os
        from datetime import datetime
        
        # 创建图表目录
        chart_dir = "trade_charts"
        if not os.path.exists(chart_dir):
            os.makedirs(chart_dir)
        
        # 生成文件名
        ts = timestamp if timestamp else datetime.now()
        filename = f"{chart_dir}/{symbol}_{direction}_{trade_id}_{ts.strftime('%Y%m%d_%H%M%S')}.png"
        
        # 创建图表
        fig, ax = plt.subplots(figsize=(10, 6))
        
        # 绘制价格线
        prices = [entry_price, exit_price]
        labels = ['Entry', 'Exit']
        colors = ['green' if direction == 'LONG' else 'red', 
                  'red' if pnl < 0 else 'green']
        
        ax.plot([0, 1], prices, marker='o', linewidth=2, markersize=10)
        
        # 标注价格
        for i, (price, label) in enumerate(zip(prices, labels)):
            ax.annotate(f'{label}\n${price:.4f}', 
                       xy=(i, price), 
                       xytext=(0, 10 if i == 0 else -20),
                       textcoords='offset points',
                       ha='center',
                       fontsize=10,
                       bbox=dict(boxstyle='round,pad=0.5', fc=colors[i], alpha=0.3))
        
        # 设置标题和标签
        pnl_color = 'green' if pnl >= 0 else 'red'
        pnl_sign = '+' if pnl >= 0 else ''
        ax.set_title(f'{symbol} {direction} Trade | PnL: {pnl_sign}${pnl:.2f}', 
                    fontsize=14, fontweight='bold', color=pnl_color)
        ax.set_ylabel('Price (USDT)', fontsize=12)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(labels)
        ax.grid(True, alpha=0.3)
        
        # 添加交易信息
        info_text = (
            f"Trade ID: {trade_id}\n"
            f"Direction: {direction}\n"
            f"Entry: ${entry_price:.4f}\n"
            f"Exit: ${exit_price:.4f}\n"
            f"PnL: {pnl_sign}${pnl:.2f}"
        )
        ax.text(0.02, 0.98, info_text, 
               transform=ax.transAxes,
               fontsize=9,
               verticalalignment='top',
               bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        
        # 保存图表
        plt.tight_layout()
        plt.savefig(filename, dpi=100, bbox_inches='tight')
        plt.close(fig)
        
        logger.info(f"📊 交易图表已生成: {filename}")
        return filename
        
    except Exception as e:
        logger.error(f"❌ 生成交易图表失败: {e}", exc_info=True)
        return ""


# ==========================================
# 🔥 K线图生成器 — 千里眼视觉对账系统 (mplfinance + BytesIO 零磁盘碎片)
# ==========================================

def get_kline_chart_buffer(df, symbol="", num_candles=80, mav=(7, 25, 99)):
    """
    高性能 K 线蜡烛图生成器（纯内存流，零磁盘 I/O）

    基于 mplfinance + charles 样式，输出红绿蜡烛图 + 成交量 + 多条均线。
    图片以 PNG bytes 返回，可直接喂给 Telegram send_photo 或 Gemini Vision。

    Args:
        df: pandas DataFrame，必须包含 Open/High/Low/Close/Volume 列，
            Index 为 DatetimeIndex。
        symbol: 交易对名称，用于图表标题（如 "BTCUSDT"）。
        num_candles: 截取最近 N 根 K 线绘制，范围 [50, 100]，默认 80。
        mav: 均线周期元组，默认 (7, 25, 99) 即 MA7 / MA25 / MA99。

    Returns:
        bytes: PNG 图片二进制数据。失败返回 None。
    """
    try:
        import io
        import mplfinance as mpf

        # ── 1. 数据校验 ──
        if df is None or df.empty:
            logger.warning("get_kline_chart_buffer: DataFrame 为空")
            return None

        required = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not all(c in df.columns for c in required):
            logger.warning(f"get_kline_chart_buffer: 缺少必要列，当前: {df.columns.tolist()}")
            return None

        # 限制 K 线数量在 [50, 100]
        num_candles = max(50, min(100, num_candles))
        df_plot = df.tail(num_candles).copy()

        if len(df_plot) < 10:
            logger.warning(f"get_kline_chart_buffer: 数据不足 ({len(df_plot)} 根)，至少需要 10 根")
            return None

        # ── 2. 样式配置（基于 charles 红绿蜡烛图）──
        mc = mpf.make_marketcolors(
            up='#26a69a',       # 涨：青绿
            down='#ef5350',     # 跌：红
            edge='inherit',
            wick='inherit',
            volume={'up': '#26a69a', 'down': '#ef5350'},
        )

        style = mpf.make_mpf_style(
            base_mpf_style='charles',
            marketcolors=mc,
            gridstyle='--',
            gridcolor='#e0e0e0',
            facecolor='#fafafa',
            figcolor='#ffffff',
            y_on_right=False,
            rc={'font.size': 9},
        )

        # ── 3. 过滤掉超出数据长度的均线周期 ──
        valid_mav = tuple(m for m in mav if m <= len(df_plot))

        # ── 4. 渲染到内存流（零磁盘 I/O）──
        buf = io.BytesIO()

        title = f"{symbol}  ({len(df_plot)} candles)" if symbol else f"{len(df_plot)} candles"

        plot_kwargs = dict(
            type='candle',
            style=style,
            volume=True,
            title=title,
            ylabel='Price (USDT)',
            ylabel_lower='Volume',
            figsize=(14, 8),
            tight_layout=True,
            savefig=dict(fname=buf, dpi=120, bbox_inches='tight', pad_inches=0.2),
        )

        if valid_mav:
            plot_kwargs['mav'] = valid_mav

        mpf.plot(df_plot, **plot_kwargs)

        image_data = buf.getvalue()
        buf.close()

        logger.info(
            f"✅ K线图已生成: {symbol} | {len(df_plot)} 根 | "
            f"MA{valid_mav} | {len(image_data):,} bytes"
        )
        return image_data

    except ImportError:
        logger.error("❌ mplfinance 未安装，请运行: pip install mplfinance")
        return None
    except Exception as e:
        logger.error(f"❌ get_kline_chart_buffer 失败: {e}", exc_info=True)
        return None


# 向后兼容旧调用
get_kline_buffer = get_kline_chart_buffer


logger.info("工具函数模块已加载")
