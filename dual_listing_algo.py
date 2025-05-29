import datetime as dt
import time
import random
import logging

from optibook.synchronous_client import Exchange
import numpy as np

exchange = Exchange()
exchange.connect()

logging.getLogger('client').setLevel('ERROR')


def trade_would_breach_position_limit(instrument_id, volume, side, breach, position_limit=100):
    positions = exchange.get_positions()
    position_instrument = positions[instrument_id]

    if breach == False:
        return False
    elif side == 'bid':
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

stock_pair_list = [('ASML', 'ASML_DUAL'), ('SAP', 'SAP_DUAL')]
stock_list = [stock for pair in stock_pair_list for stock in pair]

while True:
    print(f'')
    print(f'-----------------------------------------------------------------')
    print(f'TRADE LOOP ITERATION ENTERED AT {str(dt.datetime.now()):18s} UTC.')
    print(f'-----------------------------------------------------------------')

    positions = exchange.get_positions()
    print_positions_and_pnl(always_display=[stock for pair in stock_pair_list for stock in pair])
    print(f'')

    stock_id_breach = "NIL"
    stock_id_dual_breach = "NIL"

    ### Check for near-limit positions & reduce those positions
    for instrument_id in positions:
        if (positions[instrument_id] >= 94 and exchange.get_last_price_book(instrument_id).bids):
            print(f'Reducing position in {instrument_id} as about to breach position limit.')
            exchange.insert_order(instrument_id, price=exchange.get_last_price_book(instrument_id).bids[0].price, volume=10, side='ask', order_type='ioc')
            #if instrument_id in stock_list:
            #    stock_list.remove(instrument_id)
        elif (positions[instrument_id] <= -94 and exchange.get_last_price_book(instrument_id).asks):
            print(f'Reducing position in {instrument_id} as about to breach position limit.')
            exchange.insert_order(instrument_id, price=exchange.get_last_price_book(instrument_id).asks[0].price, volume=10, side='bid', order_type='ioc')
            #if instrument_id in stock_list:
            #    stock_list.remove(instrument_id)
    
    print_positions_and_pnl(always_display=[stock for pair in stock_pair_list for stock in pair])
    print(f'')

    ########################################
    ####### (1) Dual Listing Trading #######
    ########################################
    for stock_id, stock_id_dual in stock_pair_list:

        stock_order_pri_book = exchange.get_last_price_book(stock_id)
        stock_order_sec_book = exchange.get_last_price_book(stock_id_dual)
        if (stock_order_pri_book and stock_order_pri_book.bids and stock_order_pri_book.asks and stock_order_sec_book and stock_order_sec_book.bids and stock_order_sec_book.asks):
            # Obtain best bid and ask prices from order books
            best_pri_bid_price = stock_order_pri_book.bids[0].price
            best_pri_ask_price = stock_order_pri_book.asks[0].price
            best_sec_bid_price = stock_order_sec_book.bids[0].price
            best_sec_ask_price = stock_order_sec_book.asks[0].price
            print(f'Top level prices for {stock_id}: {best_pri_bid_price:.2f} :: {best_pri_ask_price:.2f}')
            print(f'Top level prices for {stock_id_dual}: {best_sec_bid_price:.2f} :: {best_sec_ask_price:.2f}')
        else:    
            print(f'Order book for {stock_id} or {stock_id_dual} does not have bids or offers. Skipping iteration.')
            continue

        # Decide whether to buy or sell
        # (1) Active Arb Strat (No overlap in spread)
        if best_sec_bid_price > best_pri_ask_price:
            strat = 'active'
            pri_side = 'bid'
            sec_side = 'ask'
            pri_price = best_pri_ask_price
            sec_price = best_sec_bid_price
        elif best_pri_bid_price > best_sec_ask_price:
            strat = 'active'
            pri_side = 'ask'
            sec_side = 'bid'
            pri_price = best_pri_bid_price
            sec_price = best_sec_ask_price
        
        # (2) Passive Arb Strat (Have overlap in spread)
        elif best_pri_ask_price > best_sec_ask_price:
            strat = 'passive'
            pri_side = 'ask'
            pri_price = best_pri_ask_price - 0.01
            sec_side = 'bid'
            sec_price = best_sec_bid_price + 0.01
        elif best_sec_ask_price > best_pri_ask_price:
            strat = 'passive'
            pri_side = 'bid'
            pri_price = best_pri_bid_price + 0.01
            sec_side = 'ask'
            sec_price = best_sec_ask_price - 0.01
        else:
            strat = 'do nothing'
        

        if strat == 'do nothing':
            print(f'''Skipping as {stock_id} bid-ask is {best_pri_bid_price:.0f}::{best_pri_ask_price:.0f} & {stock_id_dual} bid-ask is {best_sec_bid_price:.0f}::{best_sec_ask_price:.0f}''')
            continue
        
        if strat == 'active':
            pri_volume = 4
            sec_volume = 4
            if (positions[stock_id] >= 0 and pri_side == 'ask') or (positions[stock_id] <= 0 and pri_side == 'bid'):
                pri_volume += 26
            if (positions[stock_id_dual] >= 0 and sec_side == 'ask') or (positions[stock_id_dual] <= 0 and sec_side == 'bid'):
                sec_volume += 26
            if not (trade_would_breach_position_limit(stock_id, pri_volume, pri_side, stock_id_breach) or trade_would_breach_position_limit(stock_id_dual, sec_volume, sec_side, stock_id_dual_breach) or is_self_trade(stock_id, pri_side, pri_price) or is_self_trade(stock_id_dual, sec_side, sec_price)):
                print(f'''Inserting {pri_side} for {stock_id}: {pri_volume:.0f} lot(s) at price {pri_price:.2f}.''')
                print(f'''Inserting {sec_side} for {stock_id_dual}: {sec_volume:.0f} lot(s) at price {sec_price:.2f}.''')
                exchange.insert_order(
                    instrument_id=stock_id,
                    price=pri_price,
                    volume=pri_volume,
                    side=pri_side,
                    order_type='ioc')
                exchange.insert_order(
                    instrument_id=stock_id_dual,
                    price=sec_price,
                    volume=sec_volume,
                    side=sec_side,
                    order_type='ioc')
            else:
                print(f'''Not inserting {pri_volume:.0f} lot {pri_side} for {stock_id} to avoid position-limit breach.''')
                print(f'''Not inserting {sec_volume:.0f} lot {sec_side} for {stock_id_dual} to avoid position-limit breach.''')

        # Insert limit orders for passive arb strategy
        elif strat == 'passive':
            pri_volume = 4
            sec_volume = 4
            if (positions[stock_id] >= 0 and pri_side == 'ask') or (positions[stock_id] <= 0 and pri_side == 'bid'):
                pri_volume += 16
                stock_id_breach = False
            if (positions[stock_id_dual] >= 0 and sec_side == 'ask') or (positions[stock_id_dual] <= 0 and sec_side == 'bid'):
                sec_volume += 16
                stock_id_dual_breach = False
            if not (trade_would_breach_position_limit(stock_id, pri_volume, pri_side, stock_id_breach) or trade_would_breach_position_limit(stock_id_dual, sec_volume, sec_side, stock_id_dual_breach) or is_self_trade(stock_id, pri_side, pri_price) or is_self_trade(stock_id_dual, sec_side, sec_price)):
                exchange.delete_orders(stock_id)
                exchange.delete_orders(stock_id_dual)
                print(f'''Inserting {pri_side} for {stock_id}: {pri_volume:.0f} lot(s) at price {pri_price:.2f}.''')
                print(f'''Inserting {sec_side} for {stock_id_dual}: {sec_volume:.0f} lot(s) at price {sec_price:.2f}.''')
                exchange.insert_order(
                    instrument_id=stock_id,
                    price=pri_price,
                    volume=pri_volume,
                    side=pri_side,
                    order_type='limit')
                exchange.insert_order(
                    instrument_id=stock_id_dual,
                    price=sec_price,
                    volume=sec_volume,
                    side=sec_side,
                    order_type='limit')
                time.sleep(2.5)
            else:
                print(f'''Not inserting {pri_volume:.0f} lot {pri_side} for {stock_id} to avoid position-limit breach.''')
                print(f'''Not inserting {sec_volume:.0f} lot {sec_side} for {stock_id_dual} to avoid position-limit breach.''')

    print(f'\nSleeping for 1 seconds.')
    time.sleep(0.5)
