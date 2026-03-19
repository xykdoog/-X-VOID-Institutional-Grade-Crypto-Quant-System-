
#!/usr/bin/env python3
"""Quick smoke test for smc_signal_template"""
from smc_signal_template import format_smc_fire_signal, build_smc_signal_from_trade_context

# Test 1: format_smc_fire_signal
msg = format_smc_fire_signal(
    symbol="BTCUSDT",
    price=84500.1234,
    signal_type="BUY",
    entry=84500.0,
    stop_loss=83800.0,
    tp1=85900.0,
    tp2=86950.0,
    leverage=20,
    risk_pct=2.0,
    rr_ratio=2.0,
    atr=350.5,
    trade_id="SIM_123456",
)
print("=" * 60)
print("TEST 1: format_smc_fire_signal (BUY)")
print("=" * 60)
print(msg)

# Test 2: build_smc_signal_from_trade_context
msg2 = build_smc_signal_from_trade_context(
    symbol="ETHUSDT",
    signal_type="SELL",
    price=3200.55,
    position_info={"quantity": 0.5, "leverage": 20, "kelly_factor": 1.1, "allocated_capital": 100},
    atr=45.0,
    adx=32.5,
    stop_loss_price=3290.0,
    trade_id="TRADE_999",
    signal_message="做空信号：MACD死叉+动能确认",
    is_sandbox=True,
)
print("\n" + "=" * 60)
print("TEST 2: build_smc_signal_from_trade_context (SELL, SANDBOX)")
print("=" * 60)
print(msg2)

print("\n✅ All tests passed!")
#!/usr/bin/env python3
"""Quick smoke test for smc_signal_template"""
from smc_signal_template import format_smc_fire_signal, build_smc_signal_from_trade_context

# Test 1: format_smc_fire_signal
msg = format_smc_fire_signal(
    symbol="BTCUSDT",
    price=84500.1234,
    signal_type="BUY",
    entry=84500.0,
    stop_loss=83800.0,
    tp1=85900.0,
    tp2=86950.0,
    leverage=20,
    risk_pct=2.0,
    rr_ratio=2.0,
    atr=350.5,
    trade_id="SIM_123456",
)
print("=" * 60)
print("TEST 1: format_smc_fire_signal (BUY)")
print("=" * 60)
print(msg)

# Test 2: build_smc_signal_from_trade_context
msg2 = build_smc_signal_from_trade_context(
    symbol="ETHUSDT",
    signal_type="SELL",
    price=3200.55,
    position_info={"quantity": 0.5, "leverage": 20, "kelly_factor": 1.1, "allocated_capital": 100},
    atr=45.0,
    adx=32.5,
    stop_loss_price=3290.0,
    trade_id="TRADE_999",
    signal_message="做空信号：MACD死叉+动能确认",
    is_sandbox=True,
)
print("\n" + "=" * 60)
print("TEST 2: build_smc_signal_from_trade_context (SELL, SANDBOX)")
print("=" * 60)
print(msg2)

print("\n✅ All tests passed!")
