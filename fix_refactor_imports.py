import os
import re

# 定义文件到目录的映射关系
IIMPORT_MAP = {
    # Core 
    'trading_engine': 'core', 'risk_manager': 'core', 'execution_algo': 'core',
    'enhanced_black_swan': 'core', 'position_isolation': 'core', 'enhanced_mtf_resonance': 'core',
    'correlation_engine': 'core', 'correlation_matrix': 'core', 'dlq_worker': 'core',
    'worker_logic': 'core', 'websocket_manager': 'core', 'api_weight_monitor': 'core',
    'execution_quality_monitor': 'core', 'monitors': 'core', 'backtest_worker': 'core',
    'human_override': 'core', 'websocket_queue_integration': 'core', 'web_api': 'core',
    # Agents
    'ai_analyst': 'agents', 'ai_analyst_validators': 'agents', 'llm_worker': 'agents',
    'intelligence_hub': 'agents', 'ai_emergency_control': 'agents',
    # Bot
    'bot_callbacks': 'bot', 'bot_handlers': 'bot', 'bot_handlers_additions': 'bot',
    # UI (新)
    'dashboard': 'ui',
    # Utils
    'utils': 'utils', 'logger_setup': 'utils', 'redis_manager': 'utils',
    'config_redis_functions': 'utils', 'network_config': 'utils', 'proxy_tunnel': 'utils',
    'plot_equity': 'utils',
    # Tests
    'smc_signal_template': 'tests'
}

def fix_imports_in_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    
    for module, folder in IMPORT_MAP.items():
        # 模式 1: 替换 "import module" 为 "from folder import module"
        content = re.sub(rf'^import {module}\b', f'from {folder} import {module}', content, flags=re.MULTILINE)
        
        # 模式 2: 替换 "from module import" 为 "from folder.module import"
        content = re.sub(rf'^from {module} import', f'from {folder}.{module} import', content, flags=re.MULTILINE)

    if content != original_content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"✅ 已修复: {file_path}")

def run_fix():
    # 扫描所有文件夹中的 .py 文件
    for root, dirs, files in os.walk('.'):
        if '.git' in dirs: dirs.remove('.git') # 跳过 git 目录
        if '__pycache__' in dirs: dirs.remove('__pycache__')
        
        for file in files:
            if file.endswith('.py') and file != 'fix_refactor_imports.py':
                fix_imports_in_file(os.path.join(root, file))

if __name__ == "__main__":
    print("🚀 开始批量修复 Import 路径...")
    run_fix()
    print("🎯 修复完成！请尝试运行 main.py 检查。")