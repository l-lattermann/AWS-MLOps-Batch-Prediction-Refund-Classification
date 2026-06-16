"""
Dataset handling.

Responsibilities:
- Load images
- Preprocessing
- Train/validation split
- Data augmentation
"""


import os
import kagglehub


output_dir = "data/clothing-dataset-full"
os.makedirs(output_dir, exist_ok=True)


# Download latest version
path = kagglehub.dataset_download(handle="agrigorev/clothing-dataset-full", output_dir=output_dir)

print("Path to dataset files:", path)

