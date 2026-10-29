-- Runs only once, when the Postgres volume is created.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE DATABASE mlflow;        -- MLflow backend (module 4)
CREATE DATABASE keycloak;      -- reserved
