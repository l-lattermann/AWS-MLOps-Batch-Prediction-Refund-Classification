-- Allowed image processing states.
CREATE TYPE image_status AS ENUM (
    'PENDING',
    'PROCESSING',
    'PREDICTED',
    'FAILED'
);

-- Allowed execution states for training and prediction runs.
CREATE TYPE run_status AS ENUM (
    'RUNNING',
    'SUCCESS',
    'FAILED'
);

-- Supported configuration categories.
CREATE TYPE config_type AS ENUM (
    'TRAIN',
    'PREDICTION'
);

-- Stores all available datasets and their versions.
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    s3_prefix TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (dataset_id, dataset_version)
);

-- Stores all trained models and their S3 locations.
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

-- Stores metadata for every model training run.
CREATE TABLE IF NOT EXISTS training_runs (
    run_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    status run_status NOT NULL DEFAULT 'RUNNING',

    -- Training metrics.
    train_loss DOUBLE PRECISION,
    validation_loss DOUBLE PRECISION,
    train_accuracy DOUBLE PRECISION,
    validation_accuracy DOUBLE PRECISION,
    test_accuracy DOUBLE PRECISION,

    -- Training hyperparameters.
    epochs INTEGER NOT NULL,
    batch_size INTEGER NOT NULL,
    learning_rate DOUBLE PRECISION NOT NULL,

    training_duration_seconds DOUBLE PRECISION,
    git_commit_hash TEXT,

    -- CloudWatch log location for debugging.
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

-- Stores uploaded images awaiting or having completed inference.
CREATE TABLE IF NOT EXISTS images (
    image_id TEXT PRIMARY KEY,
    s3_key TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    status image_status NOT NULL DEFAULT 'PENDING',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Stores metadata and monitoring statistics for each prediction batch.
CREATE TABLE IF NOT EXISTS prediction_runs (
    run_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    status run_status NOT NULL DEFAULT 'RUNNING',

    images_processed INTEGER DEFAULT 0,
    images_failed INTEGER DEFAULT 0,

    -- Confidence distribution used for model monitoring.
    confidence_mean DOUBLE PRECISION,
    confidence_min DOUBLE PRECISION,
    confidence_max DOUBLE PRECISION,
    confidence_p05 DOUBLE PRECISION,
    confidence_p50 DOUBLE PRECISION,
    confidence_p95 DOUBLE PRECISION,

    -- Predictions below the configured confidence threshold.
    low_confidence_count INTEGER DEFAULT 0,
    low_confidence_rate DOUBLE PRECISION DEFAULT 0,

    -- CloudWatch log location for debugging.
    cloudwatch_log_group TEXT,
    cloudwatch_log_stream TEXT,
    error_message TEXT,

    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,

    FOREIGN KEY (model_version)
        REFERENCES models(model_version)
);

-- Stores one prediction per processed image.
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

-- Stores the predicted class distribution of each batch.
CREATE TABLE IF NOT EXISTS prediction_class_stats (
    -- Enables monitoring of prediction drift over time.
    run_id TEXT NOT NULL,
    predicted_class TEXT NOT NULL,
    prediction_count INTEGER NOT NULL,
    prediction_share DOUBLE PRECISION NOT NULL,

    PRIMARY KEY (run_id, predicted_class),

    FOREIGN KEY (run_id)
        REFERENCES prediction_runs(run_id)
);

-- Stores training and prediction configuration files.
CREATE TABLE IF NOT EXISTS configs (
    config_id TEXT PRIMARY KEY,
    config_type config_type NOT NULL,
    s3_key TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Speeds up retrieval of the latest configuration.
CREATE INDEX IF NOT EXISTS idx_configs_created_at
ON configs(config_type, created_at DESC);

-- Speeds up filtering images by processing status.
CREATE INDEX IF NOT EXISTS idx_images_status
ON images(status);

-- Speeds up filtering training runs by status.
CREATE INDEX IF NOT EXISTS idx_training_runs_status
ON training_runs(status);

-- Speeds up filtering prediction runs by status.
CREATE INDEX IF NOT EXISTS idx_prediction_runs_status
ON prediction_runs(status);

-- Speeds up lookup of predictions for an image.
CREATE INDEX IF NOT EXISTS idx_predictions_image_id
ON predictions(image_id);

-- Speeds up lookup of predictions for a model.
CREATE INDEX IF NOT EXISTS idx_predictions_model_version
ON predictions(model_version);

-- Speeds up lookup of training runs for a model.
CREATE INDEX IF NOT EXISTS idx_training_runs_model_version
ON training_runs(model_version);

-- Speeds up retrieval of class statistics for a prediction run.
CREATE INDEX IF NOT EXISTS idx_prediction_class_stats_run_id
ON prediction_class_stats(run_id);