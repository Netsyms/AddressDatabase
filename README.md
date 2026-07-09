# Address Database Builder

Builds a street address database from public data.  For US, Canada, and Mexico.

## Installing

1. Python dependencies:  
	`
	pip install pandas postal usaddress-scourgify uszipcode
	`
2. Set up libpostal, then set the environment so the code can find it:  
	`
	export LIBPOSTAL_DATA_DIR=./libpostaldata/libpostal
	`

3. Create ZIP+4 database from the data at https://www.zip-codes.com/zip-plus-4-database.asp:  
`./zipdbgen.py input.zip zip4.sqlite`  
Run twice, once with the 5-digit ZIP .zip file and once with the ZIP+4 .zip file.

4. Edit `src/zipfunctions.py` with the path to the ZIP4 database:
`zip4 = sqlite3.connect("file:/[path-to-folder]/zip4.sqlite?mode=ro&immutable=1", uri=True, check_same_thread=False)`

5. Do whatever stuff I forgot to list here, until `main.py` stops throwing errors.

## Running

Note: The Python scripts will output usage info when run with `--help`.

### Step 1: Ingest and Sanitize Address Data

```bash
export LIBPOSTAL_DATA_DIR=./libpostaldata/libpostal

python3 ./main.py --libpostal --appendplus4 -a --cpu [core count] \
--country [two-letter country] \
--state [two-letter state/province or three-letter Mexican state] \
--filetype [source type] --outputfile ./out.csv \
./in.file
```

You can use name input files; they'll be processed in the order provided. Only one input file will be processed at a time, but processing of each file is chunked and run across multiple CPU cores in parallel.

Filetype is the input file format:

* "oa" for an OpenAddresses.io geojson file.  To download and unpack in bulk, use the `downloadoa.py` script in this repo.
* "nad" for the [US National Address Database](https://www.transportation.gov/gis/national-address-database) comma delimited ASCII TXT.zip file.
* "nar" for the [Canada National Address Register](https://www150.statcan.gc.ca/n1/pub/46-26-0002/462600022022001-eng.htm) zip file.
* "adb" for a CSV that was previously output from this script.
* "osm" for data from the OpenStreetMap Overpass API.  See below for the query to use to get a suitable file for this.

Repeat running `main.py` for each file you're processing.  When done, you'll have all the addresses in `out.csv`.

### Step 2: Deduplicate and Clean

You'll probably have some addresses that are duplicated and others that are missing so much data they're not useful.

```bash
python3 ./main.py --dedup out.csv
```

This will produce `out.csv.dedup.csv`. Use this new file for the rest of the process.

### Step 3: Build Database

Run this in two passes, so addresses with a ZIP+4 will be inserted first. This prevents them from being overwritten later by a duplicate address without a ZIP+4.

```bash
echo "Importing records with ZIP+4..."
./main.py -a --sqlskipno4 --appendplus4 --outputfile database.sqlite --tosqlite out.csv.dedup.csv

echo "Importing records without ZIP+4..."
./main.py -a --outputfile database.sqlite --tosqlite out.csv.dedup.csv

echo "Optimizing database and adding indexes..."
python3 ./main.py --sqloptimize database.sqlite
```
### Step 4: Create Map Visualization (Optional)

Draw all addresses on a world map image, as one green pixel per address.

```bash
python3 ./rendermap.py database.sqlite map.png 50
```
The number at the end is how many pixels per degree of latitude/longitude. 50 and 100 are available.  100 will lock up weaker image viewer software.

## Notes

### OSM Overpass API Query

Replace "Montana" with the region to fetch addresses from.
```
[out:csv(::"lat", ::"lon", "addr:housenumber", "addr:street", "addr:city", "addr:state", "addr:postcode")][timeout:120];
area["name"="Montana"]->.boundaryarea;
node["addr:housenumber"]["addr:street"](area.boundaryarea);
out;
way["addr:housenumber"]["addr:street"](area.boundaryarea);
out center;
relation["addr:housenumber"]["addr:street"](area.boundaryarea);
out center;
```