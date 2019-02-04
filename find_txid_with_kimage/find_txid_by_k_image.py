#!/usr/bin/python3.6

import ujson
import requests
import os.path
import sqlite3
import sys
import argparse

''' 
DB will have two main tables for now.
txid_k_images - id, txid, type, k_images
state - key, value
'''

config = {"db-path":"", "daemon-url":""}

# Initial data store capabilities for tool(s)
class DB:
    def __init__(self):
        self.__db_path = config['db-path']
        exists = os.path.exists(self.__db_path)
        self.__db_conn = sqlite3.connect(self.__db_path)
        self.__cursor = self.__db_conn.cursor()
        if not exists:
            self.__recreateSchemaDB()

    def __recreateSchemaDB(self):
        # Create txid_k_images
        self.__cursor.execute("CREATE TABLE txid_k_images (id integer, txid text, type text, k_images text)")
        self.__cursor.execute("CREATE TABLE state (id integer, key text, value text)")
        self.__cursor.execute("INSERT INTO state(key, value) VALUES('last_block_scanned', '0')")
        self.__db_conn.commit()

    def getLastScannedBlockHeight(self):
        return self.getStateValue('last_block_scanned')

    def updateState(self, key='', value=''):
        if key == '' or value == '':
            raise ValueError('Empty key or value! NOT PERMITTED!')

        self.__cursor.execute("SELECT value FROM state WHERE key='"+key+"'")
        res = self.__cursor.fetchone()

        if res == None:
            self.__cursor.execute("INSERT INTO state (key, value) VALUES(?,?)", [key, value])
        else:
            self.__cursor.execute("UPDATE state set value = '"+str(value)+"' where key='"+key+"'")

        self.__db_conn.commit()

    def getStateValue(self, key=''):
        if key == '':
            raise ValueError('Empty key! NOT PERMITTED!')
        self.__cursor.execute("SELECT value FROM state WHERE key='" + str(key) + "'")
        res = self.__cursor.fetchone()
        if res == None:
            raise ValueError('There is no key' +str(key)+ ' in state!')
        else:
            return res[0]

    def updateTx2KImage(self, txid='', type='', k_images=[]):
        if txid == '' or type == '' or k_images == []:
            raise ValueError('Some of input data is empty!')
        insert_sql_query = "INSERT INTO txid_k_images (txid, type, k_images) VALUES(?,?,?)"
        self.__cursor.execute("SELECT value FROM txid_k_images WHERE txid='" + str(txid) + "'")
        res = self.__cursor.fetchone()

        if res == None:
            self.__cursor.execute(insert_sql_query, [txid, type, str(k_images)])
        else:
            raise OverflowError
        self.__db_conn.commit()

    def updateTx2KImageMany(self, data=[]):
        insert_sql_query = "INSERT INTO txid_k_images (txid, type, k_images) VALUES(?,?,?)"
        self.__cursor.executemany(insert_sql_query, data)
        self.__db_conn.commit()

    def findTxByKImage(self, k_image=""):
        self.__cursor.execute("SELECT txid FROM txid_k_images WHERE k_images like '%"+str(k_image)+"%'")
        res = self.__cursor.fetchone()
        if res == None:
            return 0
        else:
            return res[0]

class BlockchainInfo:
    def __init__(self):
        self.url = config['daemon-url'] +"/"
        self.__info = self.__getBlockchainInfo()
        self.__data_store = DB()

####### PUBLIC API #########

    def getBlockchainHeight(self):
        return self.__info["height"]

    def getDataFromBlockchain(self):
        last_block_scanned = int(self.__data_store.getLastScannedBlockHeight()) + 1
        curr_height = int(self.getBlockchainHeight())-1

        if last_block_scanned >= curr_height:
            return

        # As miner txs don't have k_image field there is no need to include them in search
        # @todo Check which interval boundrary is included.
        print("Getting block heights")
        block_heights = self.__getBlockHeightsWithTxs(last_block_scanned, curr_height)
        print("Block heights acquired. Total {} blocks to load".format(len(block_heights)))
        print("Loading blocks and getting txids")
        txids = self.__getTxIds(block_heights)
        n = len(txids)
        if n == 0:
            self.__data_store.updateState(key='last_block_scanned', value=curr_height)
            return
        i = 0
        step = 500
        next = step
        print('Acquiring tx data, total txs to load: {}'.format(n))
        while i < n:
            tx_buffer =[]
            block_height = 0
            fragment = self.__getTxData(txids[i:next])
            for tx in fragment['txs']:
                if int(tx['block_height']) > block_height:
                    block_height = int(tx['block_height'])
                tx_buffer.append(self.__processTx(tx))
            i = next + 1
            next = next + step
            if next > n:
                next = n

            self.__saveCurrentState(block_height=block_height, data=tx_buffer)
            if n < step:
                print("Processed {} of {} txs".format(n, n))
            else:
                sys.stdout.write("Processed %d of %d txs\r" % (i-1, n))
                sys.stdout.flush()

    def getBlock(self, height=0):
        return self.__sendJSONRPCRequest(method="get_block", params={"height": height})

    def getUpdatedBlockHeight(self):
        return self.__data_store.getLastScannedBlockHeight()

    def getTxByKimage(self, k_image=''):
        txid = self.__data_store.findTxByKImage(k_image=k_image)
        if txid == 0:
            print("There is no tx with given key image up to {} block".format(self.__data_store.getLastScannedBlockHeight()))
        else:
            print("Transaction id containing key image is: txid = {}".format(txid))
####### END PUBLIC API #########

####### PRIVATE STUFF #########

    def __saveCurrentState(self, block_height=0, data=[]):
        self.__data_store.updateTx2KImageMany(data)
        self.__data_store.updateState(key='last_block_scanned',value=block_height)

    def __processTx(self, tx=None):
        txid = tx['tx_hash']
        type = 'plain'
        k_images = []
        as_json = ujson.loads(tx['as_json'])
        for vin in as_json['vin']:
            if 'migration' in vin.keys():
                type = 'migration'
                k_images.append(vin['migration']['k_image'])
            if 'key' in vin.keys():
                k_images.append(vin['key']['k_image'])

        return (txid, type,str(k_images))

    #todo Introduce error checking and raise BlockchainError
    def __getBlockchainInfo(self):
        res = requests.get(self.url+"getinfo")
        return ujson.loads(res.text)

    # Every JSON request sent to daemon has corresponding input.
    # @method - name of targeted method
    # @params - parameters for given method.
    # @return - result field of response.
    # todo Introduce error checking and raise BlockchainError
    def __sendJSONRPCRequest(self, method="", params=None):
        data = {
            "jsonrpc": "2.0",
            "id": "0",
            "method": method,
            "params": params
        }

        res = requests.post(self.url+"json_rpc", data=ujson.dumps(data))
        return ujson.loads(res.text)["result"]

    def __sendPlainRequest(self, method="", body=None):
        res = requests.post(self.url+method, data=ujson.dumps(body))
        return ujson.loads(res.text)

    def __getBlockHeightsWithTxs(self, start_height=0, end_height=0):
        res = self.__sendJSONRPCRequest(method="get_block_headers_range", params={"start_height": start_height,
                                                                               "end_height": end_height})
        block_heights = []
        for header in res["headers"]:
            if header["num_txes"] != 0:
                block_heights.append(header["height"])
        return block_heights

    def __getTxIds(self, block_heights=[]):
        txids = []
        i = 0
        for height in block_heights:
            if i % 1000 == 0:
                sys.stdout.write("Blocks loaded %d of %d \r" % (i, len(block_heights)))
                sys.stdout.flush()
            i = i+1
            res = self.getBlock(height=height)
            for txid in res["tx_hashes"]:
                txids.append(txid)
        return txids

    def __getTxData(self, txs_hashes=[]):
        return self.__sendPlainRequest(method="get_transactions",body={"txs_hashes":txs_hashes,
                                                                       "decode_as_json": True})

def handleCLIArguments():
    # Read command line arguments regarding transaction emission.
    parser = argparse.ArgumentParser(
        description='Tool for analyzing Safex Blockchain and binding key_images with txids and vice versa')

    parser.add_argument('--db-path', help="Path to Database file",
                        required=False, type=str, default="./main.db")
    parser.add_argument('--daemon-rpc-url', help="Url of the Safex daemon RPC",
                        required=False, type=str, default="http://localhost:17402")
    parser.add_argument('--key-image', help="Targeted key image", required=True)

    args = vars(parser.parse_args())

    config['daemon-url'] = args['daemon_rpc_url']
    config['db-path'] = args['db_path']

    return args['key_image']


def main():
    k_image = handleCLIArguments()

    bc = BlockchainInfo()
    bc.getDataFromBlockchain()
    print("Local DB is up to date with {} block!".format(bc.getUpdatedBlockHeight()))
    print("   ")
    print("----------------------------------------------------------")
    bc.getTxByKimage(k_image)
    print("----------------------------------------------------------")

main()