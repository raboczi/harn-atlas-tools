#!/usr/bin/python
"""
Evaluates the lakes on the map. Assumes elevations to have been
done.
"""
import sys
import argparse
import psycopg2

EPS = 0.01

def label_rings(verbose, table, cursor, line):
    """Label all unlabeled rings."""
    if verbose:
        print(f"- ring {line[0]}")
    cursor.execute(f"""
        SELECT id, type FROM {table}
        WHERE (type LIKE '%CONTOURS%' OR type LIKE '%00%') AND
          CASE WHEN ST_IsClosed(wkb_geometry) THEN
            ST_Covers(ST_MakePolygon(wkb_geometry), '{line[1]}'::geometry)
          END
        ORDER BY ST_Distance(wkb_geometry, '{line[1]}'::geometry) ASC""")
    rings = list(enumerate(cursor.fetchall()))
    for idx_r, ring in rings:
        if "00" not in ring[1]:
            continue
        if verbose:
            print(f"- - found fixed {ring}")
        for idx_c, check in rings:
            elev = int(ring[1]) + 500*(idx_r - idx_c)
            if f"{elev}" != check[1]:
                if "CONTOURS" not in check[1]:
                    print(f"- - - errorneous fix {check} with {elev}")
                else:
                    if verbose:
                        print(f"- - - fix {check} with {elev}")
                    cursor.execute(f"""
                        UPDATE {table}
                        SET type = '{elev}'
                        WHERE id = {check[0]}""")

def main():
    """Main method."""
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description='Load GEOJSON elevation lines and points with elevation labels. ' +
        'Tries to assign the correct labels to the elevation lines')
    parser.add_argument(
        '-d', '--database', dest='db', required=True,
        help='db to connect to user:password@dbname')
    parser.add_argument(
        '-t', '--table', dest='table', required=True,
        help='table prefix; _pts and _lines will be added')
    parser.add_argument(
        '-v', '--verbose', action='store_true',
        help='verbose', required=False)
    args = parser.parse_args()

    conn = psycopg2.connect(
        database=f"{args.db.split('@')[1]}",
        user=f"{args.db.split('@')[0].split(':')[0]}",
        password=f"{args.db.split('@')[0].split(':')[1]}",)
    cursor = conn.cursor()

    # Initialize
    cursor.execute(f"""
        SELECT id, wkb_geometry FROM {args.table}_lines WHERE type LIKE '%LAKES%'""")
    print(f"Identifying lines: {cursor.fetchall()[0][0]}")

    # Remove pathological lines
    print("Remove lines of length 3")
    cursor.execute(f"""
        DELETE FROM {args.table}_lines
        WHERE type LIKE '%LAKES%' AND ST_NumPoints(wkb_geometry) < 4
        OR ST_Length(wkb_geometry) < {EPS}""")

    # All colored closed lines are lakes
    print("Elevate all lakes")
    cursor.execute(f"""
        SELECT id, wkb_geometry, ST_MaxDistance(wkb_geometry, wkb_geometry)
        FROM {args.table}_lines
        WHERE ST_IsClosed(wkb_geometry) AND type LIKE '%LAKES%' AND style LIKE '%fill: #d4effc%'""")
    lakes = cursor.fetchall()
    for lake in lakes:
        if args.verbose:
            print(f"- {lake[0]}")
        cursor.execute(f"""
            SELECT id, type, wkb_geometry, ST_Distance(wkb_geometry, '{lake[1]}'::geometry)
            FROM {args.table}_lines
            WHERE (type LIKE '%00%' OR type = '0') AND ST_IsClosed(wkb_geometry) AND
              type NOT LIKE '%LAKE%'
            ORDER BY ST_Distance(wkb_geometry, '{lake[1]}'::geometry) ASC
            LIMIT 1""")
        elev1 = cursor.fetchall()[0]
        cursor.execute(f"""
            SELECT id, type, wkb_geometry, ST_Distance(wkb_geometry, '{lake[1]}'::geometry)
            FROM {args.table}_lines
            WHERE (type LIKE '%00%' OR type = '0') AND ST_IsClosed(wkb_geometry) AND
              type NOT LIKE '%LAKE%' AND
              type <> '{elev1[1]}' AND
              NOT ST_Covers(ST_MakePolygon('{elev1[2]}'::geometry), wkb_geometry)
            ORDER BY ST_Distance(wkb_geometry, '{lake[1]}'::geometry) ASC
            LIMIT 1""")
        elev2 = cursor.fetchall()[0]
        if abs(int(elev1[1]) - int(elev2[1])) != 500:
            print(f"Sanity check failed: Lake {lake[0]} between {elev1[:2]} and {elev2[:2]}")
            cursor.execute(f"""
                UPDATE {args.table}_lines SET type = 'BROKENLAKE' WHERE id = {lake[0]}""")
        else:
            cursor.execute(f"""
                UPDATE {args.table}_lines SET type = 'LAKE' WHERE id = {lake[0]}""")

    print("Revisit unlabeled rings: without lakes in them")
    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%CONTOURS%'""")
    print(f"Previously remaining lines: {cursor.fetchall()[0][0]}")
    cursor.execute(f"""
        SELECT topring.id, topring.wkb_geometry FROM {args.table}_lines AS topring
        WHERE ST_IsClosed(topring.wkb_geometry) AND
          (topring.type LIKE '%CONTOURS%' OR topring.type LIKE '%00%') AND
          NOT EXISTS (
            SELECT * FROM {args.table}_lines AS lakes
            WHERE lakes.type LIKE '%LAKE%' AND
              CASE WHEN ST_IsClosed(topring.wkb_geometry) THEN
                ST_Covers(ST_MakePolygon(topring.wkb_geometry), lakes.wkb_geometry) END) AND
          NOT EXISTS (
            SELECT * FROM {args.table}_lines AS covers
            WHERE (covers.type LIKE '%00%' OR covers.type LIKE '%CONTOURS%') AND
              topring.id <> covers.id AND
              CASE WHEN ST_IsClosed(topring.wkb_geometry) THEN
                ST_Covers(ST_MakePolygon(topring.wkb_geometry), covers.wkb_geometry) END)""")
    lines = cursor.fetchall()
    for line in lines:
        label_rings(args.verbose, f"{args.table}_lines", cursor, line)

    # Rest
    cursor.execute(f"""
        SELECT count(*) FROM {args.table}_lines WHERE type LIKE '%CONTOURS%'""")
    print(f"Remaining (contour) lines: {cursor.fetchall()[0][0]}")
    conn.commit()

if __name__ == '__main__':
    main()
