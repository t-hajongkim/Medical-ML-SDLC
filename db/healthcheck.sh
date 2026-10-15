#!/bin/sh
set -eu

user="${POSTGRES_USER:-postgres}"
database="${POSTGRES_DB:-workshop}"

# The official image's temporary initialization server has no TCP listener.
pg_isready -q -h 127.0.0.1 -U "$user" -d "$database"
ready="$(psql -X -w -qAt -U "$user" -d "$database" -v ON_ERROR_STOP=1 -c \
    'SELECT (SELECT count(*) FROM prepared.asset_info) = 1
        AND (SELECT count(*) FROM prepared.features) = (SELECT expected_rows FROM prepared.asset_info)
        AND (SELECT count(*) FROM prepared.metadata) = (SELECT expected_rows FROM prepared.asset_info)
        AND NOT EXISTS (
            SELECT 1 FROM prepared.asset_info i
            CROSS JOIN LATERAL (
                VALUES ('\''train'\'', i.train_rows),
                       ('\''validation'\'', i.validation_rows),
                       ('\''test'\'', i.test_rows)
            ) AS expected(split, rows)
            WHERE expected.rows <> (SELECT count(*) FROM prepared.metadata m WHERE m.split = expected.split)
        )')"
[ "$ready" = "t" ]
