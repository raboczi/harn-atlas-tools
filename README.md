# harn-atlas-tools

A script collection to extract GIS data from Hârn Atlas Map exports.
All scripts take the -v flag.  Scripts that do not completely digest
all entries usually print the number of remaining lines at the end.
They will be considered in a later step or, if that proves impossible,
they need to be eyeballed.  (For a semi-automated step in that
direction, look at the "named lake" section.

## Extraction

The following is the rough procedure to follow:

    python svg2geo.py -i ~/Downloads/HarnAtlas-Clean-01.56EXPORT.svg -o xyz.json

This will create points, polygons and lines in separate files, called
xyz_<type>.json, respecively. Because Shape files cannot have
different geometries in one file, they are separated.  Only Shape
files and GeoJson are supported.  Of course, `xyz` can be exchanged
with any other name (avoid blanks).  This step takes less than a
minute on an export from Kaldor & Orbaal.

Make sure you have a postgres database set up, so the following will
work.  Include the postgis extensions.  I had a bug in the geometry
containment section, but that automatically got resolved after a
fruitless try of an upgrade and a reboot of my machine.  Just make
sure your versions are fairly recent.

The script now also evaluates style information to be considered in
heuristics later.

## DB preparation

    ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_lines.json -nln xyz_lines
    ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_pts.json -nln xyz_pts

Replace `dbname`, `user`, `password`, `xyz` with whatever makes sense
for you. This will dump the lines into the table `xyz_lines`. After
this, we can make use of the index on the geo-coordinates. This step
only takes about 10 seconds on the same map export as above.

You can also use ogr2ogr to convert db data into Shapefiles and
GeoJson or a lot of other things. A great tool from a great toolset.

## Elevation

    python geo_elevation.py -t xyz -d user:password@dbname

The next step extracts the elevation lines and assigns height labels
to the based on the following heuristics:

* Any label satisfying the regex \[\^1-9\]\(\[1-9\]\[05\]\|5\)00 is a height label.
  If you are into this, don't copy this from markdown.
* the largest number of close (EPSP) labels wins
* connect all endpoints of lines within EPSL
* All unlabeled rings around peaks go in 500ft steps to the outermost labeled ring

This step takes about 4-5 minutes.  The type field in the table
contains the elevation.  About 200 lines have no label at this point.
This heuristic improves with the number of closed elevation lines.
With Harn being an island this will eventually decrease when all lines
will be closed.

## Coast line

Effectively, this is the 0 elevation line and this is how it will be
treated in later steps.

    python geo_coast.py -t xyz -d user:password@dbname

will detect all closed coastlines (including inland islands) and
remove rivers by a simple heuristic.  The coasts are not considered by
`geo_elevation.py` yet. This will also find the big lakes that are
connected to the coastline; Arain & Tontury currently.

## Lakes

This determines all lakes by looking at the fill color.  Elevation of
lakes is not created, calculations are too complex at this point.

    python ~/bin/geo_lakes.py -t xyz -d user:password@dbname

## Roads

This extracts roads as they were intended from the SVG.  There are
"cleaner" ways of doing this with *CG_StraightSkeleton*, but they seem
to take an exorbitant amount of time and crashed my system.  At this
point the runtime is about a minute, but it still produces some small
artifacts.  I.e. some roads spring up that would be covered by a drawn
road.  Cleaning this up requires another algorithm step.

    python geo_roads.py -t xyz -d user:password@dbname

will connect towns (and such) and roads by modifying both lines and
points tables.  The corrected roads appear as *type = 'ROUTE'*.  The
algorithm:

* connect all road end-points within some distance to the road
  network.

* Simplify the road network a bit ([Visvalingam Whyatt](https://en.wikipedia.org/wiki/Visvalingam%E2%80%93Whyatt_algorithm))

* Remove short end segments from the road network

* Shift close locations onto road network
