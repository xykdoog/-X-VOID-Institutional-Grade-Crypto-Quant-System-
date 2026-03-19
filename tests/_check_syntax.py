import py_compile
try:
    py_compile.compile(r'd:\wj\WJ-BOT\trading_engine.py', doraise=True)
    print('OK: No syntax errors')
except py_compile.PyCompileError as e:
    print(f'SYNTAX ERROR: {e}')
