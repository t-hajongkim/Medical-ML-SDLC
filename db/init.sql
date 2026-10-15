BEGIN;

CREATE SCHEMA prepared;

CREATE TABLE prepared.metadata (
    row_key text PRIMARY KEY,
    patient_key text NOT NULL,
    split text NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    target smallint NOT NULL CHECK (target IN (0, 1)),
    race text,
    gender text,
    age_group text,
    race_original text,
    race_was_missing boolean NOT NULL,
    UNIQUE (row_key, split),
    CHECK (
        (race_was_missing AND race_original IS NULL AND race = 'Caucasian')
        OR (NOT race_was_missing AND race_original IS NOT NULL AND race_original = race)
    )
);

CREATE TABLE prepared.features (
    row_key text PRIMARY KEY,
    split text NOT NULL CHECK (split IN ('train', 'validation', 'test')),
    row_position integer NOT NULL CHECK (row_position > 0),
    feature_values jsonb NOT NULL CHECK (jsonb_typeof(feature_values) = 'array'),
    FOREIGN KEY (row_key, split) REFERENCES prepared.metadata(row_key, split),
    UNIQUE (split, row_position)
);

CREATE TABLE prepared.asset_info (
    data_id text PRIMARY KEY CHECK (data_id ~ '^[0-9a-f]{64}$'),
    expected_rows integer NOT NULL CHECK (expected_rows > 0),
    feature_count integer NOT NULL CHECK (feature_count > 0),
    train_rows integer NOT NULL CHECK (train_rows > 0),
    validation_rows integer NOT NULL CHECK (validation_rows > 0),
    test_rows integer NOT NULL CHECK (test_rows > 0),
    train_validation_overlap integer NOT NULL CHECK (train_validation_overlap >= 0),
    train_test_overlap integer NOT NULL CHECK (train_test_overlap >= 0),
    validation_test_overlap integer NOT NULL CHECK (validation_test_overlap >= 0),
    train_race_missing integer NOT NULL CHECK (train_race_missing >= 0),
    validation_race_missing integer NOT NULL CHECK (validation_race_missing >= 0),
    test_race_missing integer NOT NULL CHECK (test_race_missing >= 0),
    CHECK (expected_rows = train_rows + validation_rows + test_rows)
);

COPY prepared.metadata
FROM '/opt/workshop/assets/metadata.csv'
WITH (FORMAT csv, HEADER true, NULL '');

COPY prepared.features
FROM '/opt/workshop/assets/db_features.csv'
WITH (FORMAT csv, HEADER true);

COPY prepared.asset_info
FROM '/opt/workshop/assets/asset_info.csv'
WITH (FORMAT csv, HEADER true);

DO $$
BEGIN
    IF (SELECT count(*) FROM prepared.asset_info) <> 1 THEN
        RAISE EXCEPTION 'Exactly one data-only asset contract is required.';
    END IF;
    IF (SELECT count(*) FROM prepared.metadata) <> (SELECT expected_rows FROM prepared.asset_info)
       OR (SELECT count(*) FROM prepared.features) <> (SELECT expected_rows FROM prepared.asset_info) THEN
        RAISE EXCEPTION 'Prepared row counts differ from the asset contract.';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM prepared.asset_info i
        CROSS JOIN LATERAL (
            VALUES ('train', i.train_rows, i.train_race_missing),
                   ('validation', i.validation_rows, i.validation_race_missing),
                   ('test', i.test_rows, i.test_race_missing)
        ) AS expected(split, rows, race_missing)
        WHERE expected.rows <> (SELECT count(*) FROM prepared.metadata m WHERE m.split = expected.split)
           OR expected.race_missing <> (SELECT count(*) FROM prepared.metadata m WHERE m.split = expected.split AND m.race_was_missing)
           OR expected.rows <> (SELECT max(row_position) FROM prepared.features f WHERE f.split = expected.split)
    ) THEN
        RAISE EXCEPTION 'Split row counts, order or original missingness differ from the data contract.';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM prepared.asset_info i
        CROSS JOIN LATERAL (
            VALUES ('train', 'validation', i.train_validation_overlap),
                   ('train', 'test', i.train_test_overlap),
                   ('validation', 'test', i.validation_test_overlap)
        ) AS expected(left_split, right_split, overlap)
        WHERE expected.overlap <> (
            SELECT count(*) FROM (
                SELECT patient_key FROM prepared.metadata WHERE split = expected.left_split
                INTERSECT
                SELECT patient_key FROM prepared.metadata WHERE split = expected.right_split
            ) AS shared
        )
    ) THEN
        RAISE EXCEPTION 'Patient overlap differs from the recorded public split limitation.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM prepared.features AS f
        JOIN prepared.metadata AS m USING (row_key)
        WHERE f.split <> m.split
           OR jsonb_array_length(f.feature_values) <> (SELECT feature_count FROM prepared.asset_info)
           OR EXISTS (
               SELECT 1 FROM jsonb_array_elements(f.feature_values) AS element(value)
               WHERE jsonb_typeof(element.value) <> 'number'
           )
    ) THEN
        RAISE EXCEPTION 'Prepared feature schema or split assignment mismatch.';
    END IF;
END;
$$;

COMMIT;
