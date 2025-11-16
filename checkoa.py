#!/usr/bin/python3

import os, json, traceback
from argparse import ArgumentParser

rowstocheck = 15000 # Stop reading a file after this many rows, speeds up analysis of many/large address files

oklist = []
emptygeometrylist = []
emptyaddresslist = []
nocitylist = []
noziplist = []
totallyemptylist = []

def checkGeojson(filename):
    filedata = open(filename, 'r')
    linecount = 0
    okcount = 0
    emptygeometrycount = 0
    emptyaddresscount = 0
    emptycitycount = 0
    emptyzipcount = 0
    for line in filedata:
        linecount = linecount + 1
        if linecount > rowstocheck:
            break;
        try:
            data = json.loads(line)
            bad = False
            if not data["properties"]["number"] or not data["properties"]["street"]:
                emptyaddresscount = emptyaddresscount + 1
                bad = True
            if not data["geometry"] or not data["geometry"]["coordinates"][0] or not data["geometry"]["coordinates"][1]:
                emptygeometrycount = emptygeometrycount + 1
                bad = True
            if not data["properties"]["city"] and not data["properties"]["postcode"]: # Flag missing city unless postal code exists, because city can probably be filled from that
                emptycitycount = emptycitycount + 1
                bad = True
            if not data["properties"]["postcode"]:
                emptyzipcount = emptyzipcount + 1
                bad = True
            if bad == False:
                okcount = okcount + 1
        except Exception as e:
            traceback.print_exc()
            print("Error encountered while processing", filename, "at", line)
    print(filename, ": OK:", str(okcount), "Bad: geometry:", str(emptygeometrycount), "address:", str(emptyaddresscount), "city:", str(emptycitycount), "zip:", str(emptyzipcount), "          ", end="\r", flush=True)
    filedata.close()
    bad = False
    if emptygeometrycount / linecount > .25:
        emptygeometrylist.append(filename)
        bad = True
    if emptyaddresscount / linecount > .67:
        emptyaddresslist.append(filename)
        bad = True
    if emptycitycount / linecount > .67:
        nocitylist.append(filename)
        bad = True
    if emptyzipcount / linecount > .75:
        noziplist.append(filename)
        bad = True
    if emptyaddresscount >= (linecount - 10): # Allow a couple not-fully-empty addresses, otherwise some broken ones won't be reported
        totallyemptylist.append(filename)
        bad = True
    if bad == False:
        oklist.append(filename)
    
parser = ArgumentParser(
    description="Check OpenAddresses GeoJSON files and report on any problems found."
)
parser.add_argument(
    "source",
    help="File(s) to check.",
    nargs='+'
)

if __name__ == "__main__":
    args = parser.parse_args()
    print("Checking " + str(len(args.source)) + " OpenAddresses data files.")
    for filename in args.source:
        checkGeojson(filename)
    print("                                                                                                                               ")
    print()
    print("== Report ==")
    print(" Files missing geometry:")
    for filename in emptygeometrylist:
        print("    ", filename)
    print(" Files missing street address:")
    for filename in emptyaddresslist:
        print("    ", filename)
    print(" Files missing city:")
    for filename in nocitylist:
        print("    ", filename)
    print(" Files missing postal code:")
    for filename in noziplist:
        print("    ", filename)
    print(" Files missing all street addresses:")
    for filename in totallyemptylist:
        print("    ", filename)
