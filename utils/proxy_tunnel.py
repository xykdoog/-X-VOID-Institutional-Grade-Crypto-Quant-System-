# proxy_tunnel.py
import socks
import socket
import os
import logging

def enable_smart_proxy(host="127.0.0.1", port=4780):
    """
    🔥 智能流量劫持引擎：只劫持特定端口 (如 WebSocket 常用端口)
    避免干扰 requests 库的普通 HTTPS (443 端口) 请求
    """
    try:
        # 1. 保存原生的 socket
        _original_socket = socket.socket
        
        # 2. 创建一个带“脑子”的智能 socket 类
        class SmartSocket(socks.socksocket):
            def connect(self, dest_pair):
                # dest_pair 格式通常是 (host, port)
                target_host, target_port = dest_pair
                
                # 🔥 核心分流逻辑：
                # 如果是连接币安的流媒体域名 (fstream / stream)，强制使用代理
                if 'stream.binance.com' in str(target_host).lower() or 'fstream' in str(target_host).lower():
                    # 设定代理为我们的机场节点
                    self.set_proxy(socks.HTTP, host, int(port))
                    logging.info(f"✈️ [Smart Tunnel] 拦截到 WebSocket 流量，强行切入代理: {target_host}:{target_port}")
                else:
                    # 其他普通流量（如 REST API），不使用此底层代理，交由常规环境变量处理
                    # 这样可以避免 SSL 证书校验错误 (Unexpected peer connection)
                    self.set_proxy(None) 
                    
                super().connect(dest_pair)

        # 3. 偷天换日：用智能 Socket 替换系统底层 Socket
        socket.socket = SmartSocket
        
        # 4. 仅设置普通的 http 环境变量供 requests 库使用
        proxy_url = f"http://{host}:{port}"
        os.environ['http_proxy'] = proxy_url
        os.environ['https_proxy'] = proxy_url
        
        print(f"🛡️ [Smart Tunnel] 智能分流引擎已启动！(WS走劫持, API走常规)")
        
    except Exception as e:
        print(f"❌ [Smart Tunnel] 启动失败: {e}")