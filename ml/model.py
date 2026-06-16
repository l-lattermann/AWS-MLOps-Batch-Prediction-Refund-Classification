import logging
from pathlib import Path

import torch
from torch import nn
from torchvision.models import (
    EfficientNet_B0_Weights,
    MobileNet_V3_Large_Weights,
    efficientnet_b0,
    mobilenet_v3_large,
)

logger = logging.getLogger(__name__)

DEFAULT_ARCHITECTURE = "mobilenet_v3_large"
IMAGE_SIZE = 224


def create_model(
    num_classes: int,
    architecture: str = DEFAULT_ARCHITECTURE,
    pretrained: bool = True,
) -> nn.Module:
    if architecture == "mobilenet_v3_large":
        weights = MobileNet_V3_Large_Weights.DEFAULT if pretrained else None
        model = mobilenet_v3_large(weights=weights)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_features, num_classes)

    elif architecture == "efficientnet_b0":
        weights = EfficientNet_B0_Weights.DEFAULT if pretrained else None
        model = efficientnet_b0(weights=weights)
        in_features = model.classifier[-1].in_features
        model.classifier[-1] = nn.Linear(in_features, num_classes)

    else:
        raise ValueError(f"Unsupported architecture: {architecture}")

    logger.info("Created model | architecture=%s classes=%s pretrained=%s", architecture, num_classes, pretrained)
    return model


def save_model(
    model: nn.Module,
    path: str | Path,
    class_names: list[str],
    architecture: str = DEFAULT_ARCHITECTURE,
    image_size: int = IMAGE_SIZE,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "architecture": architecture,
            "class_names": class_names,
            "num_classes": len(class_names),
            "image_size": image_size,
            "torch_version": torch.__version__,
        },
        path,
    )

    logger.info("Saved model | path=%s architecture=%s", path, architecture)


def load_model(
    path: str | Path,
    device: str = "cpu",
) -> tuple[nn.Module, list[str], dict]:
    checkpoint = torch.load(path, map_location=device)

    architecture = checkpoint["architecture"]
    class_names = checkpoint["class_names"]

    model = create_model(
        num_classes=len(class_names),
        architecture=architecture,
        pretrained=False,
    )

    model.load_state_dict(checkpoint["model_state_dict"])
    model.to(device)
    model.eval()

    logger.info("Loaded model | path=%s architecture=%s classes=%s", path, architecture, len(class_names))

    return model, class_names, checkpoint