
import ujson
import requests
import os.path
import sqlite3
import binascii
import time

''' 
DB will have next tables for now.
pid_txid (id integer, pid text, txid text, block_height)
state (id integer, key text, value text)
user (id integer, username text, pid text, cash integer, token integer, integrated_address text)
'''

# Class for handling saving data. Its sqlite3 database.
class DB:
    def __init__(self):
        self.__db_path = 'main.db'
        exists = os.path.exists(self.__db_path)
        self.__db_conn = sqlite3.connect(self.__db_path)
        self.__cursor = self.__db_conn.cursor()
        if not exists:
            self.__recreateSchemaDB()

    # Recreating db tables in case that db doesnt exists
    def __recreateSchemaDB(self):
        self.__cursor.execute("CREATE TABLE pid_txid (id integer, pid text, txid text, block_height)")
        self.__cursor.execute("CREATE TABLE state (id integer, key text, value text)")
        self.__cursor.execute("CREATE TABLE user (id integer, username text, pid VARCHAR(64), cash integer, token integer, integrated_address text)")
        self.__cursor.execute("INSERT INTO state(key, value) VALUES('last_block_scanned', '0')")
        self.__db_conn.commit()

    # Creating user entry in user table.
    def createUser(self, username='', pid='', integrated_address=''):
        self.__cursor.execute("INSERT INTO user (username, pid, cash, token, integrated_address) VALUES(?,?,?,?,?)",
                              [username, pid, 0, 0, integrated_address])
        self.__db_conn.commit()

    # Updating balance of user in table.
    def updateUserBalance(self, pid='', cash=0, token=0):
        self.__cursor.execute("SELECT * FROM user WHERE pid = ?", [pid])
        res = self.__cursor.fetchone()
        if res is None:
            return

        token_val = res[4] + token
        cash_val = res[3] + cash

        self.__cursor.execute("UPDATE user set token=?, cash=? where pid=?", [token_val, cash_val, pid])
        self.__db_conn.commit()

    # Getting paymentID for given username
    def getPaymentID(self, username=''):
        self.__cursor.execute("SELECT pid FROM user WHERE username=?", [username])
        res = self.__cursor.fetchone()
        return res[0]

    # Getting paymentID for given username
    def getIntegratedAddress(self, username=''):
        self.__cursor.execute("SELECT integrated_address FROM user WHERE username=?", [username])
        res = self.__cursor.fetchone()
        return res[0]

    # Getting current number of users.
    def getNumberOfUsers(self):
        self.__cursor.execute("SELECT count(*) FROM user")
        res = self.__cursor.fetchone()
        if res == None:
            raise ValueError('There is error!!')
        else:
            return res[0]

    # Last scanned block height. Idea is to store in db everything needed so it can be continued without any problems
    # after possible shutdown of system.
    def getLastScannedBlockHeight(self):
        return self.getStateValue('last_block_scanned')

    # Updating state  table.
    def updateState(self, key='', value=''):
        if key == '' or value == '':
            raise ValueError('Empty key or value! NOT PERMITTED!')

        self.__cursor.execute("SELECT value FROM state WHERE key='"+key+"'")
        res = self.__cursor.fetchone()

        if res is None:
            self.__cursor.execute("INSERT INTO state (key, value) VALUES(?,?)", [key, value])
        else:
            self.__cursor.execute("UPDATE state set value = ? where key=?", [value, key])

        self.__db_conn.commit()

    # Retrieving value from state table
    def getStateValue(self, key=''):
        if key == '':
            raise ValueError('Empty key! NOT PERMITTED!')
        self.__cursor.execute("SELECT value FROM state WHERE key='" + str(key) + "'")
        res = self.__cursor.fetchone()
        if res is None:
            raise ValueError('There is no key' + str(key) + ' in state!')
        else:
            return res[0]

    # Save connection between PID and TXID, just for case.
    def updatePID2TXID(self, pid='', txid='', block_height=0):
        if txid == '' or type == '':
            raise ValueError('Some of input data is empty!')
        insert_sql_query = "INSERT INTO pid_txid (pid, txid, block_height) VALUES(?,?,?)"
        self.__cursor.execute("SELECT * FROM pid_txid WHERE txid='" + str(txid) + "'")
        res = self.__cursor.fetchone()

        if res is None:
            self.__cursor.execute(insert_sql_query, [pid, txid, block_height])
        else:
            raise ValueError

        self.__db_conn.commit()

    def printUsers(self):
        self.__cursor.execute("SELECT * FROM user")
        for row in self.__cursor:
            print(row)

    def printPID2TX(self):
        self.__cursor.execute("SELECT * FROM pid_txid")
        for row in self.__cursor:
            print(row)

# Class for emulating exchange system.
class System:

    # Initializing database
    def __init__(self):
        self.db = DB()
        self.url = "http://localhost:17405/"
        self.__getAddress()

    # Creating user in database
    def createUser(self, username=""):
        # Following simplest strategy by giving PaymentID by number of users already in DB.
        num_of_users = self.db.getNumberOfUsers() + 1
        #
        pid = binascii.hexlify((num_of_users.to_bytes(32, 'little'))).decode('utf-8')
        self.db.createUser(username=username, pid=pid)

    # Creating user with integrated address and randomly generated paymentID
    # Optionally can be used to generate integrated address based on given payment ID
    def createUserWithIntegratedAddr(self, username=""):
        intAddress, PID = self.getIntegratedAddress()
        self.db.createUser(username=username, pid=PID.ljust(64, '0'), integrated_address=intAddress)

    # Generate integrated address with or without PaymentID
    def getIntegratedAddress(self, paymentID=''):
        res = self.__sendJSONRPCRequest(method="make_integrated_address", params={"payment_id": paymentID,
                                                                                  "standard_address": self.address})
        return res["integrated_address"], res["payment_id"]

    # Scanning for payments
    def scanForPayments(self):
        # Getting from db last block height scanned
        last_block_scanned = self.db.getLastScannedBlockHeight()

        # Retrieve payments
        res = self.__sendJSONRPCRequest(method="get_bulk_payments", params={"min_block_height": last_block_scanned})

        # Get blockchain height from wallet. Its possible that we have lost some block
        # Consider that loading payments can last for example 3 minutes. While response is loading
        # new block can arrive. Thats why we are having linking between PaymentID and TXID and substracting 1 from
        # resulting height.
        height = self.__sendJSONRPCRequest(method="get_height", params={})['height'] - 1

        # If there is no payments, preventing error of accessing non existing data.
        if not res:
            res["payments"] = []

        # Iterate through payments
        for payment in res["payments"]:
            try:
                # If there is already processed transaction id it will raise exception and just skip that payment, as
                # its already processed
                self.db.updatePID2TXID(pid=payment["payment_id"],
                                       txid=payment["tx_hash"],
                                       block_height=payment["block_height"])

                # Update corresponding amount
                # NOTE: Be carefull here, its possible that transaction has some amount value
                # even if its token transaction this is due fees which are paid in Safex Cash.
                if payment['token_transaction']:
                    self.db.updateUserBalance(payment['payment_id'], cash=0, token=payment['token_amount'])
                else:
                    self.db.updateUserBalance(payment['payment_id'], cash=payment['amount'], token=0)
            except:
                # Skip transaction in case that its already processed
                continue

        # Save last block height scanned
        self.db.updateState(key="last_block_scanned", value=str(height))

    # Updating user balance
    def updateUser(self, pid='', token=0, cash=0):
        self.db.updateUserBalance(pid, token, cash)

    # Printing users. For debugging purposes only.
    def printStats(self):
        print("--------------------------------------------------------------------------")
        self.db.printUsers()
        print("--------------------------------------------------------------------------")

    def setWalletRPCURL(self, url=''):
        self.url = url

    def __getAddress(self):
        res = self.__sendJSONRPCRequest(method="get_address", params={})
        self.address = res['address']

    # Private method handling requests to wallet-rpc
    def __sendJSONRPCRequest(self, method="", params=None):
        data = {
            "jsonrpc": "2.0",
            "id": "0",
            "method": method,
            "params": params
        }

        res = requests.post(self.url + "json_rpc", data=ujson.dumps(data))
        return ujson.loads(res.text)["result"]

def main():
    sys = System()
    sys.setWalletRPCURL(url="http://localhost:17405/")
    sys.createUser("t3v4")
    sys.createUser("atan")
    sys.createUserWithIntegratedAddr("Uki")

    sys.printStats()
    print("Starting active check")
    while True:
        sys.scanForPayments()
        sys.printStats()
        time.sleep(10)

main()