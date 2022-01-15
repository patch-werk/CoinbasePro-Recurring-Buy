#!/usr/bin/python3

import os, sys
import json
import math
import coinbasepro, schedule, time
import settings
from requests import Request, Session
from requests.exceptions import ConnectionError, Timeout, TooManyRedirects
import alerts as send_alert

if os.path.exists("config.json"):

    general_settings = settings.settings()
    send_alert = send_alert.alert_module()

    api_settings = general_settings.api()
    schedule_settings = general_settings.schedule()

    key = api_settings[0]['Key']
    b64secret = api_settings[0]['Secret']
    passphrase = api_settings[0]['Passphrase']
    apiurl = api_settings[0]['API-URL']

    cmc_key = api_settings[1]['Key']
    cmc_apiurl = api_settings[1]['API-URL']

    for the_schedule in schedule_settings:
        run_day = the_schedule['Day']
        run_time = the_schedule['Time']
        repeat_time = the_schedule['Repeat-Time']
        run_every = the_schedule['Scheduled-Run']

    number_of_coins = general_settings.crypto()[0]['Number-Of-Coins-To-Buy']

    auth_client = coinbasepro.AuthenticatedClient(key, b64secret, passphrase, api_url=apiurl)
    public_API = coinbasepro.PublicClient()

    coinbasepro_pairs = public_API.get_products()
    USD_pairs = []
    
    #get USD tradable pairs from coinbasepro
    for pair in coinbasepro_pairs:
        if pair['quote_currency'] == 'USD':
            USD_pairs.append(pair['base_currency'])   

    #gets a list of top coin pairs by market cap from coinmarketcap API
    def get_cmc_pairs(count):

        url = cmc_apiurl + '/v1/cryptocurrency/listings/latest'
        parameters = {
          'start':'1',
          'limit': count + 40, #get extra coins since there will be stablecoins and assets not listed on COINBASEPRO as well
          'convert':'USD'
        }
        headers = {
          'Accepts': 'application/json',
          'X-CMC_PRO_API_KEY': cmc_key,
        }

        session = Session()
        session.headers.update(headers)
        data = []
        try:
          response = session.get(url, params=parameters)
          data = json.loads(response.text)
          return data
        except (ConnectionError, Timeout, TooManyRedirects) as e:
          print(e)


    def get_top_coins(amount):

        data = get_cmc_pairs(amount)

        #parse and get coins that can be bought in USD on coinbasepro
        crypto_coins = []
        for coin in data['data']:

            include = True
            if coin['symbol'] in USD_pairs:
                if 'stablecoin' in coin['tags']:
                    include = False
            else:
                include = False

            if include:
                crypto_coins.append({"Buy-Pair" : coin['symbol']+'-USD'})

        return json.dumps(crypto_coins)

    def check_funds(currency):
        account_data = auth_client.get_accounts()
        for account in account_data:
            if account['currency'] == currency:
                currency_balance = math.floor(account['balance'])
                return currency_balance

    def get_funding_account(fund_amount, currency, fund_source):
        if fund_source == "default":
            payment_methods = auth_client.get_payment_methods()
            for payment in payment_methods:
                if payment['primary_buy'] == True:
                    payment_id = payment['id']
        elif fund_source == "coinbase":
            payment_methods = auth_client.get_coinbase_accounts()
            for payment in payment_methods:
                if ((payment['currency'] == currency) and (math.floor(payment['balance']) >= fund_amount)):
                    payment_id = payment['id']
                    break
                else:
                    payment_id = "Error"
        else:
            payment_id = "Error"
        return payment_id

    def add_funds(buy_total, current_funds, max_fund, fund_source, currency):
        if buy_total > max_fund:
            error_msg = "Error: Total crypto cost is %s %s but max funding is set to %s %s. Unable to complete purchase.\nPlease check your config file." % (buy_total, currency, max_fund, currency)
            return ("Error", error_msg)
        else:
            fund_amount = buy_total - current_funds
            if fund_amount < 10:
                fund_amount = 10
            fund_msg = "Your balance is %s %s, a deposit of %s %s will be made using your selected payment account." % (current_funds, currency, fund_amount, currency)
            print(fund_msg)
            send_alert.discord(fund_msg)
            payment_id = get_funding_account(fund_amount, currency, fund_source)
            if payment_id == "Error":
                error_msg = "Unable to determine payment method."
                return ("Error", error_msg)
            else:
                if fund_source == "coinbase":
                    # Coinbase Deposit
                    deposit = auth_client.deposit_from_coinbase(amount=fund_amount, currency=currency, coinbase_account_id=payment_id)
                    return ("Success", deposit)
                elif fund_source == "default":
                    # Default Deposit
                    deposit = auth_client.deposit(amount=fund_amount, currency=currency, payment_method_id=payment_id)
                    time.sleep(10)
                    return ("Success", deposit)
                else:
                    error_msg = "Something went wrong attempting to add funds."
                    return ("Error", error_msg)

    # Function to perform the buy
    def init_buy(pairs, currency, buy_total, how_many_to_buy):

        successfully_bought = 0
        buy_amount = math.floor(buy_total/how_many_to_buy)
        for crypto in json.loads(pairs):

            buy_pair = crypto['Buy-Pair']
            print("Initiating buy of %s %s of %s..." % (buy_amount, currency, buy_pair))
            try:
                buy = auth_client.place_market_order(product_id=buy_pair, side="buy", funds=buy_amount)
                successfully_bought += 1
                # Get Order details
                order_id = buy['id']
                order_details = auth_client.get_order(order_id=order_id)
                crypto_bought = order_details['filled_size']
                # buy_completed = order_details['done_at']
                buy_message = "You bought %s of %s" % (crypto_bought, buy_pair)
                print(buy_message)
                send_alert.discord(buy_message)
            except Exception as e:
                print("Could not buy " + buy_pair)
                print(e)
            if successfully_bought == how_many_to_buy:
                break


    def recurring_buy():

        recurring_buy_settings = settings.settings()
        funding_settings = recurring_buy_settings.funding()

        for funding in funding_settings:
            enable_funding = funding['Enable-Funding']
            currency = funding['Currency']
            max_fund = funding['Max-Fund']
            fund_source = funding['Fund-Source']
            buy_total = funding['Total-Buy-Amount']

        pairs = get_top_coins(number_of_coins)
        current_funds = check_funds(currency)

        if current_funds >= buy_total:
            init_buy(pairs, currency, buy_total, number_of_coins)
        elif current_funds < buy_total:
            if enable_funding == True:
                result = add_funds(buy_total, current_funds, max_fund, fund_source, currency)
                if result[0] == "Error":
                    print(result[1])
                    send_alert.discord(result[1])
                elif result[0] == "Success":
                    init_buy(pairs, currency, buy_total, number_of_coins)
                else:
                    fund_msg = "Something went wrong attempting to add funds to your account."
                    print(fund_msg)
                    send_alert.discord(fund_msg)
            elif enable_funding != True:
                funding_msg = "Unable to complete your Coinbase Pro purchase.\n\
Insufficient funds to make purchase and Auto-Funding is not enabled.\n\
Please deposit at least %s %s into your account" % (buy_total, currency)
                print(funding_msg)
                send_alert.discord(funding_msg)
                # print("Please deposit at least %s %s into your account" % (buy_total, currency))    

        print("bought your coins!")   
    
    if run_every == "seconds":
        # Run every X seconds (mainly for testing purposes)
        startupMsg = "Recurring Buy Bot Started!\nSchedule set for every %s seconds" % (repeat_time)
        schedule.every(repeat_time).seconds.do(recurring_buy)
        print(startupMsg)
        send_alert.discord(startupMsg)
    elif run_every == "days":
        # Run every X days at specified run time
        startupMsg = "Recurring Buy Bot Started!\nSchedule set for every %s days at %s" % (repeat_time, run_time)
        schedule.every(repeat_time).days.at(run_time).do(recurring_buy)
        print(startupMsg)
        send_alert.discord(startupMsg)
    elif run_every == "weekday":
        # Run every specified weekday at run time
        startupMsg = "Recurring Buy Bot Started!\nSchedule set for every %s at %s" % (run_day, run_time)
        getattr(schedule.every(), run_day).at(run_time).do(recurring_buy)
        print(startupMsg)
        send_alert.discord(startupMsg)
    else:
        startupMsg = "Unable to determine run type. Please check config..."
        print(startupMsg)
        send_alert.discord(startupMsg)
    

    while True:
        schedule.run_pending()
        time.sleep(1)
else:
    print("No config file found at 'config.json'. Please update your config file.")
