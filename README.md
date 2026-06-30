# Refund Classification

## Project Description

This project implements a complete cloud-based MLOps pipeline for automated image classification of returned products.

The system consists of three independent services:

- **REST API** for uploading images and triggering prediction jobs
- **Training service** for training and registering new image classification models
- **Batch prediction service** for classifying uploaded refund images

The entire infrastructure is deployed automatically on AWS using Terraform. All services run as Docker containers on Amazon ECS Fargate, store datasets and models in Amazon S3, persist metadata in Amazon RDS PostgreSQL, and write execution logs to Amazon CloudWatch.

The pipeline supports both manual execution and scheduled execution using Amazon EventBridge.

---

# System Workflow

The complete system can be deployed fully automatically using the provided deployment scripts. Terraform provisions all required AWS resources, including Amazon S3 for object storage, Amazon RDS PostgreSQL for metadata storage, Amazon ECS Fargate for container execution, Amazon ECR for Docker images, Amazon CloudWatch for logging, an Application Load Balancer (ALB) for the REST API, and Amazon EventBridge for scheduled execution.

After the infrastructure has been created, Docker images for the API, training service, and batch prediction service are built locally, pushed to Amazon ECR, and registered as ECS task definitions. Initialization scripts then create the database schema, upload datasets and configuration files to Amazon S3, and populate the database with the corresponding metadata.

Once deployed, users interact with the system through the REST API. Instead of uploading images individually, a local folder containing refund images is provided. The API uploads every image into the **incoming-images/** directory in Amazon S3 and creates one corresponding database entry in the **images** table with status **PENDING**.

Batch prediction can either be triggered manually through the REST API or automatically every night by Amazon EventBridge. When the prediction task starts, ECS launches the batch prediction container. The service retrieves the newest prediction configuration, loads the currently active model from Amazon RDS, downloads the corresponding model artifact from Amazon S3, and processes every image whose status is **PENDING**.

For every image, the predicted class and confidence score are stored in Amazon RDS. Besides individual predictions, the system also stores aggregated monitoring information for the prediction run, including:

- confidence mean, minimum and maximum
- confidence percentiles (5%, 50%, 95%)
- number and percentage of low-confidence predictions
- predicted class distribution

Successfully processed images are marked as **PREDICTED**, while failed images receive status **FAILED**.

Model training follows a similar workflow. Training can be started manually or automatically once per month through Amazon EventBridge. The training service retrieves the newest training configuration and dataset metadata, downloads the corresponding dataset from Amazon S3, and trains a new image classification model.

After training, the model is evaluated on the training, validation and test datasets. The trained model and its metadata are uploaded to Amazon S3, while Amazon RDS stores a complete record of the training run.

The stored training metrics include:

- training loss
- validation loss
- training accuracy
- validation accuracy
- test accuracy
- training duration
- learning rate
- batch size
- number of epochs
- dataset version
- model version

Together with the prediction statistics, these metrics provide complete traceability of every model version and enable continuous monitoring of model quality over time.

Operational monitoring is integrated throughout the system. API requests, training jobs and prediction jobs continuously write logs to Amazon CloudWatch, while Amazon RDS serves as the central metadata store for all datasets, models, prediction runs and training runs.

---

# Deployment & Testing

The entire project can be deployed automatically.

Terraform provisions the complete AWS infrastructure, after which deployment scripts build the Docker images, push them to Amazon ECR and configure the ECS services.

Following deployment, a fully automated integration test suite validates the complete system.

The tests verify:

- AWS infrastructure is reachable and correctly deployed
- REST API is available
- Batch prediction jobs execute successfully
- Training jobs execute successfully
- Prediction metadata is correctly written to Amazon RDS
- Training metadata is correctly written to Amazon RDS
- CloudWatch logs are generated
- Database consistency across all metadata tables

The prediction integration test launches a complete batch prediction task and verifies that predictions, confidence statistics and class distribution statistics are stored correctly.

The training integration test launches a complete training job and verifies that a new model is trained, uploaded to Amazon S3 and registered correctly inside Amazon RDS together with all recorded training metrics.

Finally, database consistency tests validate all foreign-key relationships and verify that the metadata generated by previous tests has been written correctly.

---

# Architecture Overview

```
                                   Developer
                                       │
                                       │ build_images.sh / push_images.sh
                                       ▼
                               Amazon ECR (Docker Images)
                                       │
                                       ▼
                            Amazon ECS Fargate Cluster
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
    API Service                  Training Service             Batch Prediction
        │                              │                              │
        ▼                              ▼                              ▼
Application Load Balancer      Downloads dataset           Downloads pending images
        │                      from Amazon S3              from Amazon S3
        ▼                              │                              │
 REST API Requests                     ▼                              ▼
        │                     Trains new model              Loads active model
        │                              │                              │
        ▼                              ▼                              ▼
Uploads images               Uploads model & metadata      Stores predictions
to Amazon S3                 to Amazon S3                  in Amazon RDS
        │                              │                              │
        └───────────────► Amazon RDS PostgreSQL ◄─────────────────────┘
                             │
                             ├── datasets
                             ├── models
                             ├── training_runs
                             ├── images
                             ├── prediction_runs
                             ├── predictions
                             ├── prediction_class_stats
                             └── configs

Amazon EventBridge
    ├── Monthly → Training ECS Task
    └── Nightly → Batch Prediction ECS Task

Amazon CloudWatch
    └── API, training and prediction logs
```

---

# Repository Structure

```text
refund-classification/
├── app/                     # FastAPI REST API
├── config/
│   ├── pred/                # Prediction configurations
│   └── train/               # Training configurations
├── data/
│   └── clothing-dataset-small/
│       ├── train/
│       ├── validation/
│       └── test/
├── docker/                  # Dockerfiles
├── ml/                      # Training, prediction and model utilities
├── scripts/                 # Deployment and setup scripts
├── sql/                     # PostgreSQL schema
├── storage/                 # AWS and database utilities
├── terraform/               # Infrastructure as code
└── tests/                   # Integration tests
```

---

# Setup

The project supports two installation methods.

- **Automatic setup** performs the complete infrastructure deployment, application deployment, database initialization, data upload, and validation with a single command.
- **Manual setup** exposes every individual deployment stage, making it easier to understand how the complete MLOps pipeline is assembled.

---

## Option 1 – Automatic setup (recommended)

The entire system can be deployed using

```bash
./scripts/setup.sh
```

The setup script automatically performs the complete deployment pipeline:

1. Creates all AWS infrastructure using Terraform
2. Builds all Docker images
3. Pushes the images to Amazon ECR
4. Exports Terraform outputs into a local `.env`
5. Initializes the PostgreSQL database
6. Uploads datasets, configuration files and model artifacts to Amazon S3
7. Registers datasets and models in Amazon RDS
8. Executes the complete integration test suite

After the script finishes successfully, the entire system is operational and ready to receive prediction requests.

> **Warning**
>
> The setup script executes
>
> ```bash
> terraform apply -auto-approve
> ```
>
> automatically.
>
> Running the script therefore creates or updates AWS infrastructure without requiring additional confirmation. Only execute it in an AWS account where creating infrastructure is intended.

---

## Option 2 – Manual setup

Running the deployment manually illustrates how the individual stages of a typical cloud-native MLOps deployment build upon one another.

### 1. Create the Python environment

Create a virtual environment and install all required Python dependencies.

```bash
python -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

### 2. Deploy the AWS infrastructure

Provision all cloud resources using Terraform.

```bash
./scripts/deploy_aws_resources.sh
```

The deployment creates:

- Amazon ECS cluster
- Amazon ECR repository
- Amazon S3 bucket
- Amazon RDS PostgreSQL instance
- Application Load Balancer
- CloudWatch log group
- EventBridge schedules
- IAM roles and permissions
- Security groups and networking

Terraform outputs are exported to

```
terraform/infrastructure.json
```

which is used by subsequent setup scripts.

---

### 3. Build Docker images

Build the Docker images for all services.

```bash
./scripts/build_images.sh
```

Images are built for

- REST API
- Training service
- Batch prediction service

---

### 4. Push Docker images

Upload the Docker images to Amazon ECR.

```bash
./scripts/push_images.sh
```

These images are referenced by the ECS task definitions created through Terraform.

---

### 5. Export infrastructure outputs

Generate the local environment file from the Terraform outputs.

```bash
python -m scripts.export_envs
```

This step automatically extracts information such as

- RDS endpoint
- S3 bucket name
- ECS cluster name
- Task definitions
- Load balancer URL
- Security groups
- Subnets

and writes them into `.env`.

---

### 6. Load the environment

```bash
set -a
source .env
set +a
```

---

### 7. Wait for Amazon RDS

The PostgreSQL instance requires approximately one minute before accepting incoming connections.

```bash
echo "Waiting for RDS..."
sleep 60
```

---

### 8. Initialize the database

Create the PostgreSQL schema.

```bash
python -m scripts.db_init
```

This script

- retrieves the database credentials from AWS Secrets Manager
- connects to Amazon RDS
- creates the complete relational schema
- configures indexes and constraints

---

### 9. Bootstrap the system

Upload all initial project assets.

```bash
python -m scripts.bootstrap_aws
```

This step

- uploads training and prediction configuration files to Amazon S3
- uploads datasets to Amazon S3
- uploads existing model artifacts
- registers datasets inside Amazon RDS
- registers model metadata
- uploads example production images
- creates metadata entries for uploaded images

After completion, the deployed system is fully initialized and ready for inference.

---

# Validation

The repository contains a comprehensive integration test suite that validates both the deployed infrastructure and the complete machine learning workflows.

Unlike traditional unit tests, these tests execute real AWS services and perform complete end-to-end workflows.

---

## 1. Infrastructure validation

```bash
python -m pytest tests/test_aws_infra.py
```

This test verifies that the deployed infrastructure is operational.

It checks

- Amazon S3 bucket availability
- required S3 directory structure
- Amazon RDS connectivity
- required database tables
- configuration records
- active model registration
- ECS cluster availability
- ECS task definitions
- API availability
- API health endpoint
- active model endpoint
- ECS API service status

---

## 2. Batch prediction validation

```bash
python -m pytest tests/test_pred.py
```

Approximate runtime:

**~5 minutes**

This test launches a real ECS batch prediction task and validates the complete prediction pipeline.

The test verifies

- ECS task execution
- successful task completion
- prediction metadata generation
- prediction run creation
- prediction records
- confidence scores
- confidence statistics
- prediction percentiles
- class distribution statistics
- image status updates
- CloudWatch log generation
- prediction metadata consistency

No mocks are used—the deployed production infrastructure is exercised directly.

---

## 3. Training validation

```bash
python -m pytest tests/test_train.py
```

Approximate runtime:

**~30 minutes**

This test launches a complete model training job on ECS.

It validates

- dataset download
- training execution
- validation and test evaluation
- model artifact generation
- metadata generation
- model upload to Amazon S3
- model registration in Amazon RDS
- training metrics
- hyperparameter persistence
- CloudWatch log generation
- active model registration

Training metrics verified include

- training loss
- validation loss
- training accuracy
- validation accuracy
- test accuracy
- training duration
- batch size
- learning rate
- epochs

---

## 4. Database consistency validation

```bash
python -m pytest tests/test_db_upserts.py
```

The final validation step ensures that the metadata generated by previous workflows has been persisted correctly.

Among others, it verifies

- populated metadata tables
- foreign-key consistency
- dataset-model relationships
- prediction-run relationships
- confidence value ranges
- exactly one active model
- valid class statistics
- consistency between aggregated statistics and raw prediction records

---

Passing the complete test suite demonstrates that the infrastructure, deployment pipeline, training workflow, prediction workflow, metadata tracking, monitoring, and database persistence operate correctly as a fully integrated cloud-native MLOps system.