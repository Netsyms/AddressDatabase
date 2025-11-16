#!/usr/bin/python3

import gzip
import shutil
from argparse import ArgumentParser

import requests, tempfile, os, pathlib

sourceList = {}

def getSourceList():
    global sourceList
    if sourceList == {}:
        print("Fetching sources list")
        json = requests.get(
            "https://batch.openaddresses.io/api/data"
        ).json()
        for s in json:
            if s["layer"] != "addresses":
                continue
            if s["source"] in sourceList:
                if s["updated"] > sourceList[s["source"]]["updated"]:
                    sourceList[s["source"]] = s
            else:
                sourceList[s["source"]] = s
    return sourceList


def downloadSources(id, outfolder):
    for sourceName in getSourceList():
        s = getSourceList()[sourceName]
        if s["source"].startswith(id):
            outfilename = outfolder + "/" + s["source"] + "-addresses-" + s["name"] + ".geojson"
            outfoldername = os.path.dirname(outfilename)
            if os.path.isfile(outfilename):
                print("Skipping " + s["source"] + ", already on disk.")
                continue
            print("Downloading " + s["source"])
            gzdl = requests.get("https://v2.openaddresses.io/batch-prod/job/" + str(s["job"]) + "/source.geojson.gz", stream=True)
            tmp = tempfile.NamedTemporaryFile()
            with open(tmp.name, 'wb') as tf:
                for chunk in gzdl.iter_content(chunk_size=16*1024):
                    tf.write(chunk)
            pathlib.Path(outfoldername).mkdir(parents=True, exist_ok=True)
            with gzip.open(tmp.name) as gzf, open(outfilename, 'wb') as outf:
                shutil.copyfileobj(gzf, outf)

parser = ArgumentParser(
    description="Download address data from OpenAddresses.io"
)
parser.add_argument(
    "source",
    help="Source dataset ID, or partial ID. For example: us/al/ will download all Alabama datasets, us/mt/statewide will download the Montana statewide dataset.",
)

parser.add_argument(
    "outfolder",
    help="Output folder",
)

if __name__ == "__main__":
    args = parser.parse_args()
    downloadSources(args.source, args.outfolder)
