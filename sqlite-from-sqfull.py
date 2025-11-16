#!/usr/bin/python3

from argparse import ArgumentParser
import sqlite3

def process(filename, outfile):
    print("Connecting to databases")
    connection = sqlite3.connect(filename)
    c = connection.cursor()

    connection2 = sqlite3.connect(outfile)
    c2 = connection2.cursor()

    print("Creating lite database")
    c2.execute("DROP TABLE IF EXISTS `addresses`")
    c2.execute("""CREATE TABLE `addresses` (
	`zipcode`	VARCHAR ( 6 ) NOT NULL,
	`number`	VARCHAR ( 30 ) NOT NULL,
	`street`	VARCHAR ( 200 ) NOT NULL,
	`street2`	VARCHAR ( 20 ),
	`city`	VARCHAR ( 50 ) NOT NULL,
	`state`	CHAR ( 2 ) NOT NULL,
    `plus4`	CHAR ( 4 ),
    `country` CHAR ( 2 ) NOT NULL DEFAULT "US",
    UNIQUE (zipcode, number, street, street2, country)
)""")
    c2.execute("CREATE INDEX `zipcode_number` ON `addresses` (`zipcode`,`number`)")
    c2.execute("CREATE INDEX `number_street_state` ON `addresses` (`number`,`street`,`state`)")

    print("Copying records")
    c.execute('SELECT zipcode, number, street, street2, city, state, plus4, country FROM addresses')

    count = 0
    for (zipcode, number, street, street2, city, state, plus4, country) in c:
        c2.execute("INSERT OR IGNORE INTO addresses(zipcode, number, street, street2, city, state, plus4, country) VALUES (?,?,?,?,?,?,?,?)", (zipcode, number, street, street2, city, state, plus4, country))
        count = count + 1
        if count % 10000 == 0:
            print("  " + str(count) + "          ", end="\r", flush=True)

    print("\nVacuuming...")
    connection2.executescript("VACUUM")
    print("Done! Copied " + str(count) + " rows to " + outfile + ".")

parser = ArgumentParser(description='Draw a map of a database\'s address points.')

parser.add_argument('src_db', help='"Full" SQLite database')
parser.add_argument('dest_db', help='Output database with some columns and indexes removed')

if __name__ == "__main__":
    args = parser.parse_args()
    process(args.src_db, args.dest_db)
