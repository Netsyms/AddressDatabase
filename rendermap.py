#!/usr/bin/python3

from PIL import Image, ImageDraw
from argparse import ArgumentParser
import sqlite3

Image.MAX_IMAGE_PIXELS = 648000000 # 100 pixels per degree

def render(filename, outfile, ppd):
    print("Creating map overlay")
    pixelsperdegree = ppd
    width = 360 * pixelsperdegree
    height = 180 * pixelsperdegree
    img = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    print("Connecting to database")
    connection = sqlite3.connect(filename)
    c = connection.cursor()

    print("Drawing map overlay")
    c.execute('SELECT longitude, latitude FROM addresses')
    count = 0
    try:
        for (x,y) in c:
            try:
                if float(y) < -90.0 or float(y) > 90.0:
                    x, y = y, x
                x = round((x + 180) * pixelsperdegree)
                y = height - round((y + 90) * pixelsperdegree)
                draw.point((x, y), fill=(0, 255, 0))
            except:
                pass
            count = count + 1
            if count % 1000 == 0:
                print("  " + str(count) + "          ", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nKeyboardInterrupt: Stopping draw and saving image early")
        pass
    print("\nSaving overlay image")
    img.save(outfile, format="PNG")
    print("Rendering map image")
    if (pixelsperdegree > 50):
        basemap = Image.open("basemap-100.png")
    else:
        basemap = Image.open("basemap-50.png")
    Image.alpha_composite(basemap.resize((width, height)), img).save(outfile + ".map.png", format="PNG")
    img.close()
    basemap.close()
    print("Done! Saved map to " + outfile)

parser = ArgumentParser(description='Draw a map of a database\'s address points.')

parser.add_argument('src_db', help='Input SQLite database with "addresses" table containing "latitude" and "longitude" columns')
parser.add_argument('png_filename', help='Output PNG filename.')
parser.set_defaults(ppd=50)
parser.add_argument('ppd', help='Pixels per degree of latitude/longitude.', type=int)

if __name__ == "__main__":
    args = parser.parse_args()
    render(args.src_db, args.png_filename, args.ppd)
