# blockchain_utils
Various utility scripts for Safex Blockchain.

There are currently three utility scripts
- deposit_system_example
- find_txid_with_kimage
- stress_test

All scripts are tested and run with python3.6 on Ubuntu-18.04

## deposit_system_example
Example script how to implement deposit payment system. This is used at exchanges.

## find_txid_with_kimage
Script used to analyze Safex Blockchain and retrieve txid with given key image, if that transaction exists.

## stress_test
Script used to generate big load of transactions to see how network behaves with bigger load and to test dynamic blocksize growth
