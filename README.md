# harn-atlas-tools
A script collection to extract GIS data from Hârn Atlas Map exports.

The following is the rough procedure to follow:
```
python ~/bin/svg2geo.py -i ~/Downloads/HarnAtlas-Clean-01.56EXPORT.svg -o xyz.json
```
This will create points, polygons and lines in separate files, called xyz_<type>.json,
respecively. Because Shape files cannot have different geometries in one file, they
are separated.  Only Shape files and GeoJson are supported, also see the readme.
Of course, `xyz` can be exchanged with any other name (avoid blanks).  This step
takes less than a minute on an export from Kaldor & Orbaal.

Make sure you have a postgres database set up, so the following will work.  Include
the postgis extensions.  I had a bug in the geometry containment section, but that
automatically got resolved after a fruitless try of an upgrade and a reboot of my machine.
Just make sure your versions are fairly recent.

```
ogr2ogr -f PostgreSQL PG:"dbname=dbname host=localhost user=user port=5432 password=password" xyz_lines.json -nln xyz_lines
```
Replace `dbname`, `user`, `password`, `xyz` with whatever makes sense for you. This
will dump the lines into the table `xyz_lines`. After this, we can make use of the
index on the geo-coordinates. This step only takes about 10 seconds on the same map
export as above.

You can also use ogr2ogr to convert db data into Shapefiles and GeoJson or a lot
of other things. A great tool from a great toolset.

```
python ~/bin/geo_elevation.py -t xyz -d user:password@dbname
```

The next step extracts the elevation lines and assigns height labels to the based
on the following heuristics:
* Any label satisfying the regex \[\^1-9\]\(\[1-9\]\[05\]\|5\)00 is a height label.
  If you are into this, don't copy this from markdown.
* the largest number of close (EPSP) labels wins
* connect all endpoints of lines within EPSL
* All unlabeled rings around peaks go in 500ft steps to the outermost labeled ring

This step takes about 4-5 minutes.  The type field in the table contains the elevation.
About 200 lines have no label at this point.  This heuristic improves with the number
of closed elevation lines.  With Harn being an island this will eventually decrease
when all lines will be closed.

```
python ~/bin/geo_coast.py -t xyz -d user:password@dbname
```

will detect all closed coastlines (including inland islands) and remove rivers by
a simple heuristic.  The coasts are not considered by `geo_elevation.py` yet.

```
python ~/bin/geo_roads.py -t xyz -d user:password@dbname
```

will connect towns (and such) and roads by modifying the latter. The heuristics are:
* When close enough, move the road to attach to a location
* When road ends are close enough to another road, move the endpoint onto that road.

Note that this still leaves a few criss-crossing roads.
