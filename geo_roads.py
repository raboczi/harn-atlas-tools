#!/usr/bin/python
"""
Connects all roads with either endpoints or the next closest road
if within reasonable distance.
"""
import sys
import argparse
import psycopg2

EPSG = 0.005 # gap to bridge
EPSL = 0.001 # Corner measure for corners to cut

def make_valid(cursor, merge):
    """Removes the smallest one-ended segments."""
    sql_array = "'" + "'::geometry, '".join(merge) + "'::geometry"
    cursor.execute(f"""
        SELECT ST_Union(tl.geo) FROM (
          SELECT (g.pt).geom, array_agg((g.pt).path) FROM (
            SELECT ST_DumpPoints(ST_LineMerge(ST_Union(ARRAY[{sql_array}]))) AS pt)
          AS g
          GROUP BY (g.pt).geom)
        AS tl (geo, ord)
        WHERE array_length(tl.ord, 1) > 1""")
    merge = cursor.fetchall()[0][0]
    cursor.execute(f"""
        SELECT tl.line FROM (SELECT (
          ST_Dump(ST_LineMerge(ST_Union(ARRAY[{sql_array}])))).geom)
        AS tl (line)
        WHERE ST_Length(tl.line) > {EPSG} OR
          (ST_Distance(ST_StartPoint(tl.line), '{merge}'::geometry) = 0 AND
           ST_Distance(ST_EndPoint(tl.line), '{merge}'::geometry) = 0)""")
    merge = [m[0] for m in cursor.fetchall()]
    print(f"Keep {len(merge)} connected or long segments")
    return "'" + "'::geometry, '".join(merge) + "'::geometry"

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Create roads from postgis database.')
    parser.add_argument(
        '-d', '--database', dest='db', required=True,
        help='db to connect to user:password@dbname:host')
    parser.add_argument(
        '-t', '--table', dest='table', required=True,
        help='table prefix; _pts and _lines will be added')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='verbose', required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(
        user=f"{args.db.split('@')[0].split(':')[0]}",
        password=f"{args.db.split('@')[0].split(':')[1]}",
        database=f"{args.db.split('@')[1].split(':')[0]}",
        host=f"{args.db.split('@')[1].split(':')[1]}")
    cursor = conn.cursor()

    # Initialize
    cursor.execute(f"""
        CREATE TEMP SEQUENCE IF NOT EXISTS serial START 200000;
        ALTER TABLE {args.table}_lines ALTER id SET NOT NULL;
        DELETE FROM {args.table}_lines WHERE type = 'ROUTE';
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%ROADS%'""")
    print(f"Identifying lines: {cursor.fetchall()[0][0]}")

    cursor.execute(f"""
        INSERT INTO {args.table}_lines (id, name, type, wkb_geometry, style)
        SELECT nextval('serial'), '-', 'PREPROUTE', geo, '-' FROM (
          SELECT (ST_Dump(ST_LineMerge(ST_Union(wkb_geometry)))).geom
          FROM {args.table}_lines
          WHERE type LIKE '%ROADS%')
        AS lines(geo)""")

    print(f"Connect start points of roads to roads")
    cursor.execute(f"""
        SELECT tl.id, tr.id, ST_ClosestPoint(tl.wkb_geometry, ST_StartPoint(tr.geo))
        FROM {args.table}_lines AS tl INNER JOIN LATERAL (
          SELECT ts.id, ts.wkb_geometry FROM {args.table}_lines AS ts
          WHERE ts.id <> tl.id AND ts.type = 'PREPROUTE' AND
            ST_Distance(tl.wkb_geometry, ST_StartPoint(ts.wkb_geometry)) < {EPSG})
        AS tr (id, geo) ON TRUE
        WHERE tl.type = 'PREPROUTE'""")
    pt_lines = cursor.fetchall()
    print(f"Shift {len(pt_lines)} road-starts onto roads")
    for pt_line in pt_lines:
        if args.verbose:
            print(f"- start {pt_line[1]} on {pt_line[0]}")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_Snap(wkb_geometry, '{pt_line[2]}'::geometry, 0)
            WHERE id = {pt_line[0]}""")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_SetPoint(wkb_geometry, 1, '{pt_line[2]}'::geometry)
            WHERE id = {pt_line[1]}""")

    print(f"Connect end points of roads to roads")
    cursor.execute(f"""
        SELECT tl.id, tr.id, ST_ClosestPoint(tl.wkb_geometry, ST_EndPoint(tr.geo))
        FROM {args.table}_lines AS tl INNER JOIN LATERAL (
          SELECT ts.id, ts.wkb_geometry FROM {args.table}_lines AS ts
          WHERE ts.id <> tl.id AND ts.type = 'PREPROUTE' AND
            ST_Distance(tl.wkb_geometry, ST_EndPoint(ts.wkb_geometry)) < {EPSG})
        AS tr (id, geo) ON TRUE
        WHERE tl.type = 'PREPROUTE'""")
    pt_lines = cursor.fetchall()
    print(f"Shift {len(pt_lines)} road-end onto roads")
    for pt_line in pt_lines:
        if args.verbose:
            print(f"- end {pt_line[1]} on {pt_line[0]}")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_Snap(wkb_geometry, '{pt_line[2]}'::geometry, 0)
            WHERE id = {pt_line[0]}""")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_SetPoint(wkb_geometry, -1, '{pt_line[2]}'::geometry)
            WHERE id = {pt_line[1]}""")

    cursor.execute(f"""
        SELECT wkb_geometry FROM {args.table}_lines WHERE type = 'PREPROUTE'""")
    merge = [m[0] for m in cursor.fetchall()]
    sql_array = make_valid(cursor, merge)
    cursor.execute(f"""
        INSERT INTO {args.table}_lines (id, name, type, wkb_geometry, style)
        SELECT nextval('serial'), '-', 'ROUTE', tr.geo, '-' FROM (
          SELECT ST_SimplifyVW(tl.geo, {EPSL*EPSL}) FROM (
            SELECT (ST_Dump(ST_LineMerge(ST_Union(ARRAY[{sql_array}])))).geom)
          AS tl (geo))
        AS tr (geo)""")

    # Find nearby towns
    print("Find locations")
    # '%00%','%Abbey%','%BRIDGE%','%Battle Site%','%Castle%',
    # '%Chapter House%','%City%','%Ferry%','%Ford%','%Fort%','%Gargun%',
    # '%Keep%','%Manor%','%Mine%','%PEAK%','%Quarry%','%ROAD%' <- Tollhouse
    # '%Rapids%','%Ruin%','%SEA%','%SWAMP%','%Salt%','%Special %',
    # '%special %','%Swamp%','%TOWNS%','%Tribal%','%Waterfall%'
    sql_locs = "type LIKE '%Abbey%' OR \
          type LIKE '%BRIDGE%' OR \
          type LIKE '%Chapter House%' OR \
          type LIKE '%City' OR \
          type LIKE '%Ferry%' OR \
          type LIKE '%Ford%' OR \
          type LIKE '%Fort%' OR \
          type LIKE '%Gargun%' OR \
          type LIKE '%Keep%' OR \
          type LIKE '%Manor%' OR \
          type LIKE '%Mine%' OR \
          type LIKE '%Quarry%' OR \
          type LIKE '%ROAD%' OR \
          type LIKE '%Salt%' OR \
          type LIKE '%Special%' OR \
          type LIKE '%special%' OR \
          type LIKE '%TOWNS%' OR \
          type LIKE '%Tribal%' OR \
          type LIKE '%Castle%'"

    cursor.execute(f"""
        SELECT tl.id, tr.id, tr.geo
        FROM {args.table}_pts AS tl INNER JOIN LATERAL (
          SELECT id, wkb_geometry FROM {args.table}_lines
          WHERE type = 'ROUTE' AND
            ST_Distance(wkb_geometry, tl.wkb_geometry) < {EPSG} AND
            ST_Distance(wkb_geometry, tl.wkb_geometry) <> 0
          ORDER BY ST_Distance(wkb_geometry, tl.wkb_geometry) ASC
          LIMIT 1)
        AS tr (id, geo) ON TRUE
        WHERE {sql_locs}""")
    pt_lines = cursor.fetchall()
    print(f"Shift {len(pt_lines)} locations onto roads")
    for pt_line in pt_lines:
        if args.verbose:
            print(f"- shift {pt_line[0]} onto {pt_line[1]}")
        cursor.execute(f"""
            UPDATE {args.table}_pts
            SET wkb_geometry = ST_ClosestPoint('{pt_line[2]}'::geometry, wkb_geometry)
            WHERE id = {pt_line[0]}""")
        cursor.execute(f"""
            UPDATE {args.table}_lines
            SET wkb_geometry = ST_Snap(wkb_geometry, tr.geo, 0)
            FROM (
              SELECT wkb_geometry FROM {args.table}_pts WHERE id = {pt_line[0]}) AS tr (geo)
            WHERE id = {pt_line[1]}""")

    print(f"Cleanup")
    cursor.execute(f"""
        DELETE FROM {args.table}_lines WHERE type = 'PREPROUTE'""")
    conn.commit()

if __name__ == '__main__':
    main()
