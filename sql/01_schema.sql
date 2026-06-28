CREATE TYPE image_status AS ENUM (
    'PENDING',
    'PROCESSING',
    'PREDICTED',
    'FAILED'
);

CREATE TYPE run_status AS ENUM (
    'RUNNING',
    'SUCCESS',
    'FAILED'
);

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    s3_prefix TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS models (
    model_version TEXT PRIMARY KEY,
    architecture TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    s3_model_path TEXT NOT NULL UNIQUE,
    s3_metadata_path TEXT,
    active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES datasets(dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS training_runs (
    run_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,

    status run_status NOT NULL DEFAULT 'RUNNING',

    train_loss DOUBLE PRECISION,
    validation_loss DOUBLE PRECISION,
    train_accuracy DOUBLE PRECISION,
    validation_accuracy DOUBLE PRECISION,
    test_accuracy DOUBLE PRECISION,

    epochs INTEGER NOT NULL,
    batch_size INTEGER NOT NULL,
    learning_rate DOUBLE PRECISION NOT NULL,

    training_duration_seconds DOUBLE PRECISION,
    git_commit_hash TEXT,

    cloudwatch_log_group TEXT,
    cloudwatch_log_stream TEXT,
    error_message TEXT,

    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (model_version)
        REFERENCES models(model_version),

    FOREIGN KEY (dataset_id, dataset_version)
        REFERENCES datasets(dataset_id, dataset_version)
);

CREATE TABLE IF NOT EXISTS images (
    image_id TEXT PRIMARY KEY,
    s3_key TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    status image_status NOT NULL DEFAULT 'PENDING',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    run_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,

    status run_status NOT NULL DEFAULT 'RUNNING',

    images_processed INTEGER DEFAULT 0,
    images_failed INTEGER DEFAULT 0,

    cloudwatch_log_group TEXT,
    cloudwatch_log_stream TEXT,
    error_message TEXT,

    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,

    FOREIGN KEY (model_version)
        REFERENCES models(model_version)
);

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY,
    image_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    model_version TEXT NOT NULL,

    predicted_class TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,

    predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (image_id)
        REFERENCES images(image_id),

    FOREIGN KEY (run_id)
        REFERENCES prediction_runs(run_id),

    FOREIGN KEY (model_version)
        REFERENCES models(model_version)
);

CREATE INDEX IF NOT EXISTS idx_images_status
ON images(status);

CREATE INDEX IF NOT EXISTS idx_training_runs_status
ON training_runs(status);

CREATE INDEX IF NOT EXISTS idx_prediction_runs_status
ON prediction_runs(status);

CREATE INDEX IF NOT EXISTS idx_predictions_image_id
ON predictions(image_id);

CREATE INDEX IF NOT EXISTS idx_predictions_model_version
ON predictions(model_version);

CREATE INDEX IF NOT EXISTS idx_training_runs_model_version
ON training_runs(model_version);