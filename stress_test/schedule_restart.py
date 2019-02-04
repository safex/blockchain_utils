#!/bin/python3.6
'''
Script for restarting seed.py script.
Due to some issues with extensive IO in subprocess module, this script kills seed.py on fixed time interval
and start it again. This will ensure that median wont drop to minimum value because of crash of seed.py or blocking read
of child process.
'''

import subprocess
import sys
from time import sleep

args = ['python3.6', 'seed.py']
args2 = ['./kill.sh']
p = subprocess.Popen(args, stdout=sys.stdout)

while True:
    sleep(800)
    p.kill() # Kill seed.py script
    print("Killing process")
    subprocess.run(args2);
    p = subprocess.Popen(args, stdout=sys.stdout, stderr=sys.stderr)
