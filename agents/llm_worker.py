#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 异步消费者线程 (llm_worker.py)
====================================
核心思路：
  - 全局唯一 queue.Queue 作为生产者-消费者桥梁
  - 单线程 + 单事件循环，串行执行所有 LLM 异步调用
  - 彻底消除 ThreadPoolExecutor 嵌套 asyncio.run 的死锁风险
  - 任何单次 LLM 崩溃都被 try/except 吞掉，绝不导致线程退出

任务字典协议 (task dict):
{
    "type":       "market_query" | "ai_war_report" | "free_chat" | "auto_tune" | "audit",
    "chat_id":    int,           # TG 推送目标
    "prompt":     str,           # 已组装好的完整 prompt
    "callback":   callable|None, # 可选：拿到 ai_reply 后的后处理函数 callback(chat_id, ai_reply)
    "meta":       dict,          # 可选：透传给 callback 的额外上下文
}
"""

import asyncio
import queue
import threading
import time
import traceback

from utils.logger_setup import logger

# ==========================================
# 全局任务队列（生产者 put_nowait，消费者 get）
# ==========================================
llm_task_queue = queue.Queue(maxsize=500)


def llm_worker_loop(bot_instance):
    """
    LLM 消费者主循环 —— 在独立守护线程中运行。

    Args:
        bot_instance: pyTelegramBotAPI (telebot) 的 bot 对象，
                      用于 bot_instance.send_message(chat_id, text, ...)
    """
    logger.info("🚀 LLM Worker 线程已启动（单事件循环，串行消费）")

    # 创建本线程专属的事件循环，后续所有 await 都跑在这里
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    while True:
        # ---- 第一层保护：获取任务 ----
        try:
            task = llm_task_queue.get(timeout=2)
        except queue.Empty:
            continue
        except Exception as e:
            logger.error(f"❌ LLM Worker 队列读取异常: {e}")
            continue

        # ---- 第二层保护：执行任务 ----
        chat_id = task.get("chat_id")
        task_type = task.get("type", "unknown")
        prompt = task.get("prompt", "")
        callback = task.get("callback")
        meta = task.get("meta", {})

        try:
            # 延迟导入，避免循环引用
            from ai_analyst import get_commander
            commander = get_commander()

            # 🔥 根据 meta 中是否携带 chart_bytes 决定走视觉分析还是纯文本
            chart_bytes = meta.get("chart_bytes")
            if chart_bytes and hasattr(commander, "analyze_chart_with_vision_bytes"):
                ai_reply = loop.run_until_complete(
                    commander.analyze_chart_with_vision_bytes(chart_bytes, prompt)
                )
            else:
                ai_reply = loop.run_until_complete(
                    commander.ask_commander(prompt)
                )

            # ---- 结果分发 ----
            if callback is not None:
                # 生产者提供了自定义后处理（如格式化、发图、解析 COMMAND 等）
                try:
                    callback(chat_id, ai_reply, meta, bot_instance)
                except Exception as cb_err:
                    logger.error(f"❌ LLM callback 异常 [{task_type}]: {cb_err}")
                    _safe_send(bot_instance, chat_id,
                               f"⚠️ AI 分析完成但后处理失败: {str(cb_err)[:100]}")
            else:
                # 默认行为：直接推送纯文本
                if ai_reply:
                    _safe_send(bot_instance, chat_id, ai_reply[:4000])
                else:
                    _safe_send(bot_instance, chat_id,
                               "⚠️ AI 指挥官未返回有效内容，请稍后重试。")

        except asyncio.TimeoutError:
            logger.warning(f"⏱️ LLM 任务超时 [{task_type}] chat={chat_id}")
            _safe_send(bot_instance, chat_id,
                       "⏱️ AI 分析超时，请稍后重试或简化问题。")
        except Exception as e:
            logger.error(f"❌ LLM 任务执行失败 [{task_type}]: {e}\n{traceback.format_exc()}")
            _safe_send(bot_instance, chat_id,
                       f"⚠️ 指挥官暂时失联，请稍后重试。\n错误: {str(e)[:120]}")
        finally:
            try:
                llm_task_queue.task_done()
            except ValueError:
                pass


def _safe_send(bot_instance, chat_id, text):
    """安全发送 TG 消息，吞掉所有异常"""
    try:
        if bot_instance and chat_id:
            bot_instance.send_message(chat_id, text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"❌ LLM Worker 发送消息失败: {e}")


# ==========================================
# 启动入口
# ==========================================

def start_llm_worker(bot_instance):
    """
    启动 LLM 消费者守护线程。
    应在 main.py 中 bot.polling() 之前调用一次。

    Args:
        bot_instance: telebot.TeleBot 实例
    Returns:
        threading.Thread
    """
    t = threading.Thread(
        target=llm_worker_loop,
        args=(bot_instance,),
        daemon=True,
        name="LLM-Worker"
    )
    t.start()
    logger.info("✅ LLM Worker 守护线程已注册")
    return t


print("✅ llm_worker 模块已加载（生产者-消费者 LLM 架构）")
