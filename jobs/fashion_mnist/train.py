"""Small real PyTorch CPU job: Fashion-MNIST MLP. The dataset is baked into the image
(jobs/Dockerfile.torch-cpu) because job containers run with network disabled."""
import json
import os

import torch
from torch import nn
from torchvision import datasets, transforms

p = json.loads(os.environ.get("PARAMS", "{}"))
lr, epochs, batch = float(p.get("lr", 1e-3)), int(p.get("epochs", 2)), int(p.get("batch_size", 128))
hidden, subset = int(p.get("hidden", 128)), int(p.get("train_subset", 20000))
torch.manual_seed(int(p.get("seed", 0)))

tf = transforms.ToTensor()
train = datasets.FashionMNIST("/data", train=True, download=False, transform=tf)
test = datasets.FashionMNIST("/data", train=False, download=False, transform=tf)
train = torch.utils.data.Subset(train, range(min(subset, len(train))))
dl = torch.utils.data.DataLoader(train, batch_size=batch, shuffle=True)
tl = torch.utils.data.DataLoader(test, batch_size=1024)

model = nn.Sequential(nn.Flatten(), nn.Linear(784, hidden), nn.ReLU(), nn.Linear(hidden, 10))
opt = torch.optim.Adam(model.parameters(), lr=lr)
loss_fn = nn.CrossEntropyLoss()

for epoch in range(1, epochs + 1):
    model.train()
    total = 0.0
    for x, y in dl:
        opt.zero_grad()
        loss = loss_fn(model(x), y)
        loss.backward()
        opt.step()
        total += loss.item() * len(x)
    model.eval()
    with torch.no_grad():
        correct = sum((model(x).argmax(1) == y).sum().item() for x, y in tl)
    acc = correct / len(test)
    print("METRIC " + json.dumps({"step": epoch, "train_loss": total / len(train), "test_acc": acc}), flush=True)

torch.save(model.state_dict(), os.path.join(os.environ.get("OUT_DIR", "."), "model.pt"))
