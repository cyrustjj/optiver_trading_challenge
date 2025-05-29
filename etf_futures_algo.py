import datetime as dt
import time
import random
import logging

from optibook.synchronous_client import Exchange
import numpy as np

exchange = Exchange()
exchange.connect()

logging.getLogger('client').setLevel('ERROR')


def trade_would_breach_position_limit(instrument_id, volume, side, position_limit=100):
    positions = exchange.get_positions()
    position_instrument = positions[instrument_id]

    if side == 'bid':
        return position_instrument + volume > position_limit
    elif side == 'ask':
        return position_instrument - volume < -position_limit
    else:
        raise Exception(f'''Invalid side provided: {side}, expecting 'bid' or 'ask'.''')

def amt_to_reduce_position(instrument_id, volume, side, position_limit=100):
    positions = exchange.get_positions()
    position_instrument = positions[instrument_id]

    if side == 'bid':
        return 'ask', (position_instrument + volume) - position_limit
    elif side == 'ask':
        return 'bid', (-position_limit) - (position_instrument - volume)

def print_positions_and_pnl(always_display=None):
    positions = exchange.get_positions()
    print('Positions:')
    for instrument_id in positions:
        if not always_display or instrument_id in always_display or positions[instrument_id] != 0:
            print(f'  {instrument_id:20s}: {positions[instrument_id]:4.0f}')

    pnl = exchange.get_pnl()
    if pnl:
        print(f'\nPnL: {pnl:.2f}')

def is_self_trade(instrument_id, side, price):
    """
    Check if placing a limit order at this price would result in a self-trade.

    Args:
        exchange: The optibook exchange object.
        instrument_id: The instrument you're trading.
        side: 'buy' or 'sell' for the incoming order.
        price: Price of the order you're about to place.

    Returns:
        True if placing this order could result in a self-trade, False otherwise.
    """
    outstanding_orders = list(exchange.get_outstanding_orders(instrument_id).values())

    # Finding opposite orders
    my_opposite_orders = [o for o in outstanding_orders if o.side != side]

    # Check if the incoming price would match with any of my opposite orders
    if side == 'bid':
        # Buying at a price >= my own sell (ask)
        for order in my_opposite_orders:
            if (order.side == 'ask' and price >= order.price):
                return True
    elif side == 'ask':
        # Selling at a price <= my own buy (bid)
        for order in my_opposite_orders:
            if (order.side == 'bid' and price <= order.price):
                return True

    return False


while True:
    print(f'')
    print(f'-----------------------------------------------------------------')
    print(f'TRADE LOOP ITERATION ENTERED AT {str(dt.datetime.now()):18s} UTC.')
    print(f'-----------------------------------------------------------------')

    etf_id = 'OB5X_ETF'
    fut_id = 'OB5X_202509_F'

    positions = exchange.get_positions()
    print(f'')

    ### Check for near-limit positions & reduce those positions
    for instrument_id in positions:
        if (positions[instrument_id] >= 96 and exchange.get_last_price_book(instrument_id).bids):
            print(f'Position in {instrument_id} about to breach position limit, removing any trades on it.')
            if instrument_id == etf_id:
                etf_id = 'NIL'
        elif (positions[instrument_id] <= -96 and exchange.get_last_price_book(instrument_id).asks):
            print(f'Position in {instrument_id} about to breach position limit, removing any trades on it.')
            if instrument_id == etf_id:
                etf_id = 'NIL'
    
    print(f'')
    
    ########################################
    ######### (2) ETF FUT Trading ##########
    ########################################
    if etf_id == 'NIL':
        continue

    etf_book = exchange.get_last_price_book(etf_id)
    fut_book = exchange.get_last_price_book(fut_id)
    if (etf_book and fut_book and etf_book.bids and etf_book.asks and fut_book.bids and fut_book.asks):
        # Obtain best bid and ask prices from order books
        best_fut_bid = fut_book.bids[0].price
        best_fut_ask = fut_book.asks[0].price
        best_etf_bid = etf_book.bids[0].price
        best_etf_ask = etf_book.asks[0].price

        ind_fair_bid = best_fut_bid*np.exp(-0.03*0.04)
        ind_fair_ask = best_fut_ask*np.exp(-0.03*0.04)
        etf_fair_bid = float(round((ind_fair_bid*0.25) + 2.5 - 0.01, 2))
        etf_fair_ask = float(round((ind_fair_ask*0.25) + 2.5 + 0.01, 2))
    else:
        print(f'Order book for {etf_id} or {fut_id} does not have bids or offers. Skipping iteration.')
        continue
    
    # Decide whether to buy or sell
    # (1) Active Arb Strat (No overlap in spread)
    # (a) if best_etf_ask < etf_fair_bid => ETF is cheap, buy ETF, sell futures at bid
    if etf_fair_bid > best_etf_ask:
        strat = 'active'
        etf_side = 'bid'
        fut_side = 'ask'
        etf_price = best_etf_ask
        fut_price = best_fut_bid
    
    # (b) # etf_fair_ask < if best_etf_bid => ETF is expensive, sell ETF, buy futures at ask
    elif etf_fair_ask < best_etf_bid:
        strat = 'active'
        etf_side = 'ask'
        fut_side = 'bid'
        etf_price = best_etf_bid
        fut_price = best_fut_ask
        
    # (2) Passive Arb Strat (Have overlap in spread)
    elif best_etf_ask > etf_fair_ask:
        strat = 'passive'
        etf_side = 'ask'
        etf_price = best_etf_ask - 0.01
        fut_side = 'bid'
        fut_price = best_fut_ask
    elif etf_fair_ask > best_etf_ask:
        strat = 'passive'
        etf_side = 'bid'
        etf_price = best_etf_ask
        fut_side = 'ask'
        fut_price = best_fut_ask - 0.01
    else:
        strat = 'do nothing'
        
    if strat == 'do nothing':
        print(f'''Skipping as {etf_id} bid-ask is {best_etf_bid:.0f}::{best_etf_ask:.0f} & {fut_id} bid-ask is {best_fut_bid:.0f}::{best_fut_ask:.0f}''')
        continue

    # Insert IOC orders for active arb strategy
    if strat == 'active':
        etf_volume = 3
        fut_volume = 3
        if (positions[etf_id] >= 0 and etf_side == 'ask') or (positions[etf_id] <= 0 and etf_side == 'bid'):
            etf_volume += 27
        if (positions[fut_id] >= 0 and fut_side == 'ask') or (positions[fut_id] <= 0 and fut_side == 'bid'):
            fut_volume += 27
        if not (trade_would_breach_position_limit(etf_id, etf_volume, etf_side) or trade_would_breach_position_limit(fut_id, fut_volume, fut_side) or is_self_trade(etf_id, etf_side, etf_price) or is_self_trade(fut_id, fut_side, fut_price)):
            print(f'''Inserting {etf_side} for {etf_id}: {etf_volume:.0f} lot(s) at price {etf_price:.2f}.''')
            print(f'''Inserting {fut_side} for {fut_id}: {fut_volume:.0f} lot(s) at price {fut_price:.2f}.''')
            exchange.insert_order(
                instrument_id=etf_id,
                price=etf_price,
                volume=etf_volume,
                side=etf_side,
                order_type='limit')
            exchange.insert_order(
                instrument_id=fut_id,
                price=fut_price,
                volume=fut_volume,
                side=fut_side,
                order_type='limit')
        else:
            print(f'''Not inserting {etf_volume:.0f} lot {etf_side} for {etf_id} to avoid position-limit breach.''')
            print(f'''Not inserting {fut_volume:.0f} lot {fut_side} for {fut_id} to avoid position-limit breach.''')

    # Insert limit orders for passive arb strategy
    elif strat == 'passive':
        etf_volume = 3
        fut_volume = 3
        if (positions[etf_id] >= 0 and etf_side == 'ask') or (positions[etf_id] <= 0 and etf_side == 'bid'):
            etf_volume += 27
        if (positions[fut_id] >= 0 and fut_side == 'ask') or (positions[fut_id] <= 0 and fut_side == 'bid'):
            fut_volume += 27
        if not (trade_would_breach_position_limit(etf_id, etf_volume, etf_side) or trade_would_breach_position_limit(fut_id, fut_volume, fut_side) or is_self_trade(etf_id, etf_side, etf_price) or is_self_trade(fut_id, fut_side, fut_price)):
            #exchange.delete_orders(stock_id)
            #exchange.delete_orders(stock_id_dual)
            print(f'''Inserting {etf_side} for {etf_id}: {etf_volume:.0f} lot(s) at price {etf_price:.2f}.''')
            print(f'''Inserting {fut_side} for {fut_id}: {fut_volume:.0f} lot(s) at price {fut_price:.2f}.''')
            exchange.insert_order(
                instrument_id=etf_id,
                price=etf_price,
                volume=etf_volume,
                side=etf_side,
                order_type='limit')
            exchange.insert_order(
                instrument_id=fut_id,
                price=fut_price,
                volume=fut_volume,
                side=fut_side,
                order_type='limit')
        else:
            print(f'''Not inserting {etf_volume:.0f} lot {etf_side} for {etf_id} to avoid position-limit breach.''')
            print(f'''Not inserting {fut_volume:.0f} lot {fut_side} for {fut_id} to avoid position-limit breach.''')

    print(f'\nSleeping for 3 seconds.')
    time.sleep(3)