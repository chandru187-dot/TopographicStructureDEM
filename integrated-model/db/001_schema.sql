-- PostgreSQL/PostGIS canonical schema. No project evidence in migrations.
BEGIN;
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE TABLE IF NOT EXISTS source_document (
 source_id TEXT PRIMARY KEY, name TEXT NOT NULL, source_uri TEXT,
 source_date DATE, content_sha256 TEXT NOT NULL, model_version TEXT NOT NULL,
 classification TEXT NOT NULL CHECK (classification IN ('Approved','Current Baseline','Draft','Alternative','Workaround','Superseded','Rejected','Unresolved')),
 metadata JSONB NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS input_record (
 input_id TEXT PRIMARY KEY, source_id TEXT REFERENCES source_document,
 source_locator TEXT, original_value JSONB, transformed_value JSONB,
 transformation TEXT, source_year INT, geography TEXT, unit TEXT NOT NULL,
 status TEXT NOT NULL, model_version TEXT NOT NULL, metadata JSONB NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS zone (zone_id TEXT PRIMARY KEY, name TEXT NOT NULL, geom geometry(MultiPolygon,4326), status TEXT NOT NULL, source_id TEXT REFERENCES source_document);
CREATE TABLE IF NOT EXISTS station (station_id TEXT PRIMARY KEY, name TEXT NOT NULL, sequence INT, geom geometry(Point,4326), status TEXT NOT NULL, source_id TEXT REFERENCES source_document);
CREATE TABLE IF NOT EXISTS network_link (link_id TEXT PRIMARY KEY, from_node TEXT NOT NULL, to_node TEXT NOT NULL, mode TEXT NOT NULL,
 length_km DOUBLE PRECISION CHECK(length_km>=0), freeflow_min DOUBLE PRECISION CHECK(freeflow_min>=0), capacity_hour DOUBLE PRECISION CHECK(capacity_hour>=0), geom geometry(LineString,4326), source_id TEXT REFERENCES source_document, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS survey_response (survey_id TEXT NOT NULL, response_id TEXT NOT NULL, payload JSONB NOT NULL, source_id TEXT REFERENCES source_document, status TEXT NOT NULL, PRIMARY KEY(survey_id,response_id));
CREATE TABLE IF NOT EXISTS od_cell (matrix_id TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, year INT NOT NULL,
 mode TEXT NOT NULL, value DOUBLE PRECISION CHECK(value>=0), unit TEXT NOT NULL, period TEXT NOT NULL,
 source_id TEXT REFERENCES source_document,status TEXT NOT NULL,PRIMARY KEY(matrix_id,origin,destination,year,mode,period));
CREATE TABLE IF NOT EXISTS traffic_count (count_id TEXT PRIMARY KEY, site TEXT NOT NULL,direction TEXT NOT NULL,period TEXT NOT NULL,
 duration_hours DOUBLE PRECISION CHECK(duration_hours>0),vehicle_class TEXT NOT NULL,value DOUBLE PRECISION CHECK(value>=0),unit TEXT NOT NULL,source_id TEXT REFERENCES source_document,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accident (accident_id TEXT PRIMARY KEY, event_date DATE,severity TEXT NOT NULL,geom geometry(Point,4326),source_id TEXT REFERENCES source_document, metadata JSONB NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS landuse (landuse_id TEXT PRIMARY KEY,category TEXT NOT NULL,quantum DOUBLE PRECISION,unit TEXT NOT NULL,year INT,geom geometry(MultiPolygon,4326),source_id TEXT REFERENCES source_document,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assumption (assumption_id TEXT PRIMARY KEY,name TEXT NOT NULL,unit TEXT NOT NULL,value JSONB,status TEXT NOT NULL,source_id TEXT REFERENCES source_document,version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scenario (scenario_id TEXT PRIMARY KEY,name TEXT NOT NULL,configuration JSONB NOT NULL,status TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS model_run (run_id TEXT PRIMARY KEY,created_at TEXT NOT NULL,payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS dataset_version (data_version TEXT PRIMARY KEY,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),payload JSONB NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_output (run_id TEXT REFERENCES model_run,output_key TEXT NOT NULL,value JSONB,unit TEXT NOT NULL,trace JSONB NOT NULL,PRIMARY KEY(run_id,output_key));
CREATE INDEX IF NOT EXISTS station_geom_idx ON station USING gist(geom);
CREATE INDEX IF NOT EXISTS zones_geom_idx ON zone USING gist(geom);
CREATE INDEX IF NOT EXISTS accident_geom_idx ON accident USING gist(geom);
CREATE INDEX IF NOT EXISTS landuse_geom_idx ON landuse USING gist(geom);
COMMIT;
