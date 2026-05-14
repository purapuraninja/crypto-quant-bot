import json

ps = json.load(open('pnl_store.json'))
before = len(ps['closed_trades'])

ps['closed_trades'] = [t for t in ps['closed_trades']
    if not (t['symbol'] == 'SIRENUSDT' and t['close_time'] == '2026-04-11 11:43:47')]

after = len(ps['closed_trades'])
pnls = [t['pnl_usdt'] for t in ps['closed_trades']]
ps['stats']['total_realized'] = round(sum(pnls), 4)
ps['stats']['worst_trade'] = min(pnls) if pnls else 0

json.dump(ps, open('pnl_store.json', 'w'), indent=2)
print(f'Removed: {before - after} record')
print(f'Realized PnL: {ps["stats"]["total_realized"]} USDT')
