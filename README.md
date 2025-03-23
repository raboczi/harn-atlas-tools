# harn-atlas-tools

A script collection to extract GIS data from Harn Atlas Map exports.
All scripts take the -v flag.  Scripts that do not completely digest
all entries usually print the number of remaining lines at the end.
They will be considered in a later step or, if that proves impossible,
they need to be eyeballed.

Runtime is an estimate on my PC.

The scripts are not re-entrant, i.e. don't call them a second time on
the modified dataset.

## Extraction

For the current export, add

    <g id="uuid-bb7c1ef1-f291-4da3-b491" data-name="Fake">
      <path class="uuid-6447e0e3-a128-46a8-b849-9533c5245b93" d="M7088,4536L6800,4536L6800,2300L9260,2300L9260,3118"/>
    </g>

just after the COASTLINE group.  This yields a (fake) closed
coastline and benefits vegetation calculation.

The following is the rough procedure to follow:

    python svg2geo.py -i ~/Downloads/HarnAtlas-Clean-01.56EXPORT.svg -o xyz.json

This will create points, polygons and lines in separate files, called
`xyz_<type>.json`, respectively. Because Shape files cannot have
different geometries in one file, they are separated.  Only Shape
files and GeoJson are supported.  Of course, `xyz` can be exchanged
with any other name (avoid blanks).

Make sure you have a postgres database set up, so the following will
work.  Include the postgis extensions.  I had a bug in the geometry
containment section, but that automatically got resolved after a
fruitless try of an upgrade and a reboot of my machine.  Just make
sure your versions are fairly recent.

The script now also evaluates style information to be considered in
heuristics later.

> Runtime: 1 minute

## DB preparation

    ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_lines.json -nln xyz_lines
    ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_pts.json -nln xyz_pts
    ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_polys.json -nln xyz_polys

Replace `dbname`, `user`, `password`, `xyz` with whatever makes sense
for you. This will dump the lines into the table `xyz_lines`. After
this, we can make use of the index on the geo-coordinates.

You can also use ogr2ogr to convert db data into Shapefiles and
GeoJson or a lot of other things. A great tool from a great toolset.

> Runtime: 1 minute total

## Elevation

    python geo_elevation.py -t xyz -d user:password@dbname:host

This step extracts the elevation lines and assigns height labels to
the based on the following heuristics:

* Any label satisfying the regex `\[\^1-9\]\(\[1-9\]\[05\]\|5\)00` is a height label.
  If you are into this, don't copy this from markdown.

* Remove one erroneous line.

* the largest number of close (*EPSP*) labels wins.

* connect all endpoints of lines within *EPSL*.

* All unlabeled rings around peaks go in 500ft steps to the outermost
  labeled ring.

The type field in the table contains the elevation.  About 200 lines
have no label at this point.  This heuristic improves with the number
of closed elevation lines.  With Harn being an island this will
eventually decrease when all lines will be closed.

> Runtime: 4-5 minutes

## Coast line

Effectively, this is the 0 elevation line and this is how it will be
treated in later steps.

    python geo_coast.py -t xyz -d user:password@dbname:host

will detect all closed coastlines (including inland islands) and
remove rivers by a simple heuristic.  The coasts are not considered by
`geo_elevation.py` yet. This will also find the big lakes that are
connected to the coastline; Arain & Tontury currently.

* Uses *EPSL* to bridge shore gaps and *EPSB* to squeeze out rivers.

> Runtime: 1 minute

## Lakes

This determines all lakes by looking at the fill color.  Elevation of
lakes is not created, calculations are too complex at this point.

    python ~/bin/geo_lakes.py -t xyz -d user:password@dbname:host

* Ignore pathological lakes smaller than *EPS*.

> Runtime: 1 minute

## Roads

This extracts roads as they were intended from the SVG.  There are
"cleaner" ways of doing this with *CG_StraightSkeleton*, but they seem
to take an exorbitant amount of time and crashed my system.  At this
point the runtime is about a minute, but it still produces some small
artifacts.  I.e. some roads spring up that would be covered by a drawn
road.  Cleaning this up requires another algorithm step.

    python geo_roads.py -t xyz -d user:password@dbname:host

will connect towns (and such) and roads by modifying both lines and
points tables.  The corrected roads appear as *type = 'ROUTE'*.  The
algorithm:

* connect all road end-points within distance *EPSG* to the road
  network.

* Simplify the road network a bit ([Visvalingam Whyatt](https://en.wikipedia.org/wiki/Visvalingam%E2%80%93Whyatt_algorithm))
  using *EPSL* as corner-measure.

* Remove short end segments from the road network.

* Shift close locations onto road network.

> Runtime: 1 minute

## Vegetation

Turns the WOODLAND, CROPLAND, HEATH, FOREST, NEEDLELEAF, ALPINE,
SNOW_x2F_ICE into multipolygons (in the postgis sense).

    python geo_vegetation.py -t xyz -d user:password@dbname:host

Any set at position *n* in this list is reduced by every multipolygon
at later positions.  I.e. the multipolygons are disjoint.  position
*0* is going to be the default, filling all land area not filled
otherwise.

* The above is called "reduce & normalize" in the script.

* Use *EPS* to grow a bit to cover draw glitches.

* Vegetation is restricted to land.

* Shoal/Reef is restricted to off land.

* The results are in the *xyz_polys* table, type prefixed with `VEG/`.

> Runtime: 3 minutes
