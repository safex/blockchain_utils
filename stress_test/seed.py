#!/bin/python
'''
This script is intended to work with config.json file for configuring basic parameters for script execution.

Script is creating one advanced wallet and n wallet processes and communicating with them in order to seed testnet with
transactions. Transactions are performed in cycles, w1 -> w2, w2 -> w3 ... wn -> w1, where wx is wallet.

Please keep in mind if there is no enough unlocked amount of cash (or token) transactions will not be executed. So it
will effect to total number of transactions executed.

Printout of tx ids and attempts of cash value txs are left on purpose to indicate execution of script.

Script requires at least Python3+ version.

config.json explained

  "num_of_tx" - Number of calls for perform_tx method. Total number of transaction is num_of_tx + num_of_tx/3. For
                every 3  cash transaction there is one additional token transaction.
  "num_of_mtx" - Number of migration transactions per every migration_period_coeff cycle. E.g. if migration_period_coeff
                 is 3, after 3*(num_of_tx) will be exactly num_of_mtx migration transactions.
  "lower_cash" - Lower boundary for cash amount.
  "higher_cash" - Higher boundary for cash amount.
  "lower_token" - Lower boundary for token amount.
  "higher_token" - Higher boundary for token amount.
  "ring_size" - Ring-size to be applied on transactions.
  "sleep_tx" - Timeout between two regular transactions.
  "sleep_mtx" - Timeout between two migration transactions. @note This is applied per cycle, its actual time between two
                consequent migration transaction when num_of_mtx is >= 1.
  "migration_period_coeff" - Number of cycles when migration transactions are performed. Cycle size is num_of_tx.
  "advanced_wallet_path_cli" - Path to executable of advanced_wallet.
  "advanced_wallet_path_file" - Path where files of advanced_wallet are stored.
  "simple_wallet_path_cli" - Path to regular wallet cli executable.
  "wallet_electrum_seeds" - Array of electrum seeds for wallets involved. This will directly set size of wallet ring.
  "wallets_daemon_host" - Host value for safexd daemon where wallet will connect
  "wallets_daemon_port" - Port value for safexd daemon where wallet will connect.
  "wallet_files_path" - Directory where wallet files will be or are stored.
  "wallet_log_path": - Directory for log files to be stored.

'''


import argparse
import sys
import json
import subprocess
import os.path
import hashlib
import time
import random
import atexit
from time import sleep
from queue import Queue, Empty

# Generate advanced wallet process
def create_genesis_wallet_process(config):
    # Generate advanced wallet
    if not os.path.isfile(config['advanced_wallet_path_file']):
        args = [config['advanced_wallet_path_cli'], '--testnet', '--generate-from-keys',
                config['advanced_wallet_path_file'], '--password', ""]
        subprocess.run(args)
    args = [config['advanced_wallet_path_cli'], '--testnet', "--wallet-file", config['advanced_wallet_path_file'], '--password', ""]
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    process.stdin.write("\n\n".encode())
    process.stdin.flush()
    return process

# Generate simple wallet processes to be used later
def create_wallet_processes(config):
    wallet_processes = []
    index = 0
    for seed in config['wallet_electrum_seeds']:
        wallet_file_name = config['wallet_files_path'] + 'wallet_' + str(index) + '.bin'
        print('Creating wallet @{}'.format(wallet_file_name))
        args_wallet = []
        if not os.path.isfile(wallet_file_name):
            args_wallet = [config['simple_wallet_path_cli'],
                           '--testnet',
                           '--generate-new-wallet',
                           wallet_file_name,
                           '--restore-deterministic-wallet',
                           '--electrum-seed={}'.format(seed),
                           '--daemon-host',
                           config['wallets_daemon_host'],
                           '--password',
                           "",
                           '--log-file',
                           config['wallet_files_path'] + "log_" + str(index) + '.log'
                           ]
            print(subprocess.list2cmdline(args_wallet))
            wallet_processes.append(subprocess.Popen(args_wallet, stdin=subprocess.PIPE, stdout=subprocess.PIPE))
            sleep(5) # give some time to wallet to initialize
            wallet_processes[-1].stdin.write("\n".encode())
            wallet_processes[-1].stdin.flush()
            wallet_processes[-1].stdin.write("0\n".encode())
            wallet_processes[-1].stdin.flush()
        else:
            args_wallet = [config['simple_wallet_path_cli'],
                           '--testnet',
                           '--wallet-file',
                           wallet_file_name,
                           '--daemon-host',
                           config['wallets_daemon_host'],
                           '--log-file',
                           config['wallet_files_path'] + "log_" + str(index) + '.log',
                           '--password',
                           ""
                           ]
            print(subprocess.list2cmdline(args_wallet))
            wallet_processes.append(subprocess.Popen(args_wallet, stdin=subprocess.PIPE, stdout=subprocess.PIPE))
        index = index + 1
    return wallet_processes


# Class implementing basic operations with wallet needed for seeding testnet.
class Wallet:
    Config = 0

    def __init__(self, process, genesis=False):
        self.genesis = genesis
        self.process = process
        self.not_connected = False
        self.__process_initial_information()
        self.token_amount = 0
        self.cash_amount = 0
        self.get_balance()

    # Processing initial info about wallet
    # Getting error statuses regarding connection etc
    def __process_initial_information(self):
        line = str(self.process.stdout.readline())
        while True:
            if line.find("wallet: SFX") != -1:
               self.address =  line.split(": ",1)[1][:-3] # Get key and remove \n' from the end.
            if line.find("Background refresh") != -1:
                break
            if line.find("wallet failed to connect to daemon") != -1 :
                self.not_connected = True
            line = str(self.process.stdout.readline())
        d = 'd'

    # Get balance
    def get_balance(self):
        self.process.stdin.write("balance\n".encode())
        self.process.stdin.flush()
        line = str(self.process.stdout.readline)
        while line.find("unlocked cash balance") == -1:
            line = str(self.process.stdout.readline())
        cash = line.split("unlocked cash balance: ",1)[1][:-3]
        while line.find("unlocked token balance") == -1:
            line = str(self.process.stdout.readline())
        token = line.split("unlocked token balance: ",1)[1][:-3]

        self.token_amount = float(token)
        self.cash_amount = float(cash)

        return self.cash_amount, self.token_amount

    # Performing tx. If amount value is 0 that transaction is ignored.
    # Its capable to make two transactions if both values are different from 0
    # If transaction is successful transaction id is printed on stdout.
    # @return bool pair indicating if txs are successful.
    def perform_tx(self, address, cash_amount, token_amount = 0):
        cash_tx_ok = False
        token_tx_ok = False
        if cash_amount > 0:
            cash_transfer_cmd = "transfer_cash " + str(Wallet.Config['ring_size']) + " " + address + " " + str(cash_amount) + "\n"
            self.process.stdin.write(cash_transfer_cmd.encode())
            self.process.stdin.flush()
            self.process.stdin.write("\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            line = str(self.process.stdout.readline())
            while line.find("Error: ") == -1 and line.find("Transaction successfully") == -1 :
                line = str(self.process.stdout.readline())
            if line.find("Transaction successfully") != -1:
                cash_tx_ok = True
                print("Cash tx:{}".format(line.split("transaction <", 1)[1][:-4]))
        if token_amount > 0:
            cash_transfer_cmd = "transfer_token " + str(Wallet.Config['ring_size']) + " "  + address + " " + str(token_amount) + "\n"
            self.process.stdin.write(cash_transfer_cmd.encode())
            self.process.stdin.flush()
            self.process.stdin.write("\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            self.process.stdin.write("y\n".encode())
            self.process.stdin.flush()
            line = str(self.process.stdout.readline())
            while line.find("Error: ") == -1 and line.find("Transaction successfully") == -1:
                line = str(self.process.stdout.readline())
            if line.find("Transaction successfully") != -1:
                print("Token tx:{}".format(line.split("transaction <", 1)[1][:-4]))
                token_tx_ok = True

        return cash_tx_ok, token_tx_ok

    # Only possible if wallet is genesis wallet. If not exception is risen.
    # @return bool indicating if tx is successful.
    def migration_tx(self, address, amount):
        self.process.stdin.flush()
        if self.genesis == False:
            raise Exception("This is not genesis wallet process, you cant execute migration tx!")

        success = False
        cmd = "migrate " + address + " " + hashlib.sha256(str(time.time()).encode()).hexdigest() + " " + str(amount) + "\n"
        self.process.stdin.write(cmd.encode())
        self.process.stdin.flush()
        sleep(1)
        self.process.stdin.write("\n".encode())
        self.process.stdin.flush()
        self.process.stdin.write("y\n".encode())
        self.process.stdin.flush()

        line = str(self.process.stdout.readline())
        while line.find("Transaction successfully submitted") == -1 and line.find("Error:") == -1:
            line = str(self.process.stdout.readline())
        if line.find("Transaction successfully submitted") != -1:
            print("Migration tx:{}".format(line.split("transaction <", 1)[1][:-4]))
            success = True
        return success


# Read command line arguments regarding transaction emission.
parser = argparse.ArgumentParser(description='Fill testnet with transactions. @Safex')

parser.add_argument('--config', help="Path to config file",
                    required=False, type=str, default="./config.json")

args = vars(parser.parse_args())
config_path  = args['config']

file_config = open(config_path)
config = json.loads(file_config.read())

# Set Wallet class "static" variable for accessing configuration parameters.
Wallet.Config = config

# Create children processes for wallets
genesis_wallet_process = create_genesis_wallet_process(config)
wallet_processes = create_wallet_processes(config)

# Create class from process.
genesis_wallet = Wallet(genesis_wallet_process, genesis=True)
cash, token = genesis_wallet.get_balance()
print("Genesis wallet balance cash={0} token={1}".format(cash, token))

def kill_child_processes():
    for wallet in wallet_processes:
        wallet.kill()
    genesis_wallet_process.kill()

# Create Wallet objects and test for connection errors.
wallets = []
not_connected_error = False
for process in wallet_processes:
    wallets.append(Wallet(process))
    if wallets[-1].not_connected:
        not_connected_error = True
        break
    cash, token = wallets[-1].get_balance()
    print(wallets[-1].address)
    print("Cash balance: {} Token balance: {}".format(cash, token))
    sleep(5)

if not_connected_error:
    print("There are wallets which are not connected to the network! Please check configuration!")
    exit(1)

# Schedule
cycles = 0
txs = 0
n = len(wallets)
atexit.register(kill_child_processes)
print("Generating txs: ")
while 1 :
    if cycles % config["migration_period_coeff"] == 0:
        for i in range(config['num_of_mtx']):
            token_amount = random.randint(config['lower_token'], config['higher_token'])
            print("Attempting to migrate {} tokens".format(token_amount))
            genesis_wallet.migration_tx(wallets[txs % n].address, token_amount)
            sleep(config['sleep_mtx'])
            txs = txs + 1

    for i in range(config['num_of_tx']):

        cash_amount = random.randint(config['lower_cash'], config['higher_cash'])
        token_amount = random.randint(config['lower_token'], config['higher_token'])
        print("Attempting to transfer {} cash ".format(cash_amount))
        wallets[txs % n].perform_tx(wallets[(txs+1) % n].address, cash_amount, token_amount if i % 3 else 0)
        sleep(config['sleep_tx'])
        txs = txs + 1

    cycles = cycles + 1
    if txs > 10000:
        txs = 0
