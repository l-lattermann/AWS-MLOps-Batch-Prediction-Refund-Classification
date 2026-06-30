"""Integration tests verifying that training and prediction metadata is stored correctly in RDS."""

from storage.rds_connection import get_connection


def one(cur, query, params=None):
    """Execute a query returning a single scalar value."""
    cur.execute(query, params or ())
    return cur.fetchone()[0]


# Verify that all required tables contain data.
def test_all_core_tables_populated():
    """Verify that every core database table contains at least one record."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            assert one(cur, "SELECT COUNT(*) FROM datasets") > 0
            assert one(cur, "SELECT COUNT(*) FROM models") > 0
            assert one(cur, "SELECT COUNT(*) FROM training_runs") > 0
            assert one(cur, "SELECT COUNT(*) FROM images") > 0
            assert one(cur, "SELECT COUNT(*) FROM prediction_runs") > 0
            assert one(cur, "SELECT COUNT(*) FROM predictions") > 0
            assert one(cur, "SELECT COUNT(*) FROM prediction_class_stats") > 0
            assert one(cur, "SELECT COUNT(*) FROM configs WHERE config_type='TRAIN'") > 0
            assert one(cur, "SELECT COUNT(*) FROM configs WHERE config_type='PREDICTION'") > 0


# Verify that model metadata is internally consistent.
def test_models_are_valid():
    """Verify that models reference existing datasets and exactly one model is active."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            assert one(cur, """
                SELECT COUNT(*)
                FROM models m
                JOIN datasets d
                  ON m.dataset_id = d.dataset_id
                 AND m.dataset_version = d.dataset_version
                WHERE m.architecture IS NOT NULL
                  AND m.s3_model_path IS NOT NULL
                  AND m.created_at IS NOT NULL
            """) > 0

            assert one(cur, "SELECT COUNT(*) FROM models WHERE active = TRUE") == 1


# Verify that the latest training run contains all expected metadata.
def test_latest_training_run_complete():
    """Verify that the latest training run completed successfully."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, train_loss, validation_loss, train_accuracy,
                       validation_accuracy, test_accuracy, epochs, batch_size,
                       learning_rate, training_duration_seconds,
                       started_at, finished_at, error_message
                FROM training_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            r = cur.fetchone()

    assert r is not None
    assert r[0] == "SUCCESS"
    assert r[1] is not None
    assert r[2] is not None
    assert r[3] is not None
    assert r[4] is not None
    assert r[5] is not None
    assert r[6] > 0
    assert r[7] > 0
    assert r[8] > 0
    assert r[9] is not None
    assert r[10] is not None
    assert r[11] is not None
    assert r[12] is None


# Verify that aggregated prediction statistics match the stored predictions.
def test_latest_prediction_run_complete():
    """Verify that the latest prediction run statistics are internally consistent."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT run_id, status, images_processed, images_failed,
                       confidence_mean, confidence_min, confidence_max,
                       confidence_p05, confidence_p50, confidence_p95,
                       low_confidence_count, low_confidence_rate,
                       started_at, finished_at, error_message
                FROM prediction_runs
                ORDER BY started_at DESC
                LIMIT 1
            """)
            r = cur.fetchone()

            # Aggregate statistics directly from stored predictions.
            cur.execute("""
                SELECT COUNT(*), MIN(confidence), MAX(confidence), AVG(confidence)
                FROM predictions
                WHERE run_id = %s
            """, (r[0],))
            p = cur.fetchone()

            # Aggregate class distribution statistics.
            cur.execute("""
                SELECT SUM(prediction_count), SUM(prediction_share)
                FROM prediction_class_stats
                WHERE run_id = %s
            """, (r[0],))
            s = cur.fetchone()

    assert r is not None
    assert r[1] == "SUCCESS"
    assert r[2] > 0
    assert r[3] == 0
    assert r[4] is not None
    assert r[5] is not None
    assert r[6] is not None
    assert r[7] is not None
    assert r[8] is not None
    assert r[9] is not None
    assert r[10] >= 0
    assert 0 <= r[11] <= 1
    assert r[12] is not None
    assert r[13] is not None
    assert r[14] is None

    # Stored summary statistics must equal the raw prediction data.
    assert p[0] == r[2]
    assert abs(float(p[1]) - float(r[5])) < 0.0001
    assert abs(float(p[2]) - float(r[6])) < 0.0001
    assert abs(float(p[3]) - float(r[4])) < 0.0001

    # Class shares should sum to 100%.
    assert s[0] == r[2]
    assert abs(float(s[1]) - 1.0) < 0.0001


# Verify referential integrity of prediction data.
def test_images_and_predictions_consistent():
    """Verify that predictions reference valid images, runs and models."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            assert one(cur, """
                SELECT COUNT(*)
                FROM predictions p
                JOIN images i ON p.image_id = i.image_id
                JOIN prediction_runs pr ON p.run_id = pr.run_id
                JOIN models m ON p.model_version = m.model_version
                WHERE p.predicted_class IS NOT NULL
                  AND p.confidence BETWEEN 0 AND 1
                  AND p.predicted_at IS NOT NULL
                  AND i.status = 'PREDICTED'
            """) > 0

            # Confidence values must always be valid probabilities.
            assert one(cur, """
                SELECT COUNT(*)
                FROM predictions
                WHERE confidence < 0 OR confidence > 1
            """) == 0