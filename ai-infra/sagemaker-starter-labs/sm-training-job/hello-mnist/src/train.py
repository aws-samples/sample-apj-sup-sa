"""MNIST training script — runs *inside* the SageMaker container.

This file does not run on your local PC; it runs inside the AWS Deep Learning Container
launched by SageMaker. So you do not need torch / torchvision installed locally (the container already has them).

You only need to know two environment variables that SageMaker sets:
  SM_MODEL_DIR    Save the model here and it is uploaded to S3 (model.tar.gz) automatically.
  SM_NUM_GPUS     Number of GPUs allocated.
Hyperparameters (epochs, etc.) are passed as command-line arguments such as "--epochs 3".
"""

import argparse
import os

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import datasets, transforms


class Net(nn.Module):
    """A simple CNN."""

    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 32, 3, 1)
        self.conv2 = nn.Conv2d(32, 64, 3, 1)
        self.fc1 = nn.Linear(9216, 128)
        self.fc2 = nn.Linear(128, 10)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.relu(self.conv2(x))
        x = F.max_pool2d(x, 2)
        x = torch.flatten(x, 1)
        x = F.relu(self.fc1(x))
        return self.fc2(x)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=3)  # passed from launch.py
    # Model output path set by SageMaker (falls back to a local default)
    parser.add_argument("--model-dir", default=os.environ.get("SM_MODEL_DIR", "./model"))
    args = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device={device}", flush=True)  # print output goes straight to CloudWatch Logs

    # Load MNIST (the container downloads it from the internet)
    tfm = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.1307,), (0.3081,))])
    train_loader = DataLoader(
        datasets.MNIST("./data", train=True, download=True, transform=tfm),
        batch_size=128, shuffle=True,
    )
    test_loader = DataLoader(
        datasets.MNIST("./data", train=False, download=True, transform=tfm),
        batch_size=1000,
    )

    model = Net().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

    for epoch in range(1, args.epochs + 1):
        # --- Train ---
        model.train()
        for data, target in train_loader:
            data, target = data.to(device), target.to(device)
            optimizer.zero_grad()
            loss = F.cross_entropy(model(data), target)
            loss.backward()
            optimizer.step()

        # --- Evaluate ---
        model.eval()
        correct = 0
        with torch.no_grad():
            for data, target in test_loader:
                data, target = data.to(device), target.to(device)
                correct += model(data).argmax(1).eq(target).sum().item()
        acc = correct / len(test_loader.dataset)
        # The launch.py regex parses this "test_accuracy=" format into a console graph
        print(f"epoch {epoch}: test_accuracy={acc:.4f}", flush=True)

    # Save to SM_MODEL_DIR → SageMaker uploads it to S3 as model.tar.gz automatically
    os.makedirs(args.model_dir, exist_ok=True)
    torch.save(model.state_dict(), os.path.join(args.model_dir, "model.pth"))
    print("saved model", flush=True)


if __name__ == "__main__":
    main()
