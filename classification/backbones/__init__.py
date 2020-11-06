import torch
import numpy as np
import torchvision.models as models
from .wrn.models.wide_resnet_cifar import WideResNet
from .resnet12 import Resnet12
import torch.nn.functional as F

class GAP(torch.nn.Module):
    def __init__(self):
        super().__init__()
    def forward(self, x):
        return x.mean(3).mean(2)
class Conv4(torch.nn.Module):
    def __init__(self, in_h, in_w, channels, output_size, gap=True):
        super().__init__()
        in_size = min(in_w, in_h)
        ratio = in_size // 4
        if ratio >= 16:
            self.strides = [2, 2, 2, 2]
        elif ratio >= 8:
            self.strides = [1, 2, 2, 2]
        elif ratio >= 4:
            self.strides = [1, 2, 2, 1]
        elif ratio >= 2:
            self.strides = [1, 2, 1, 1]
        else:
            self.strides = [1, 1, 1, 1]
        self.channels = [32, 64, 128, 256]
        in_ch = channels
        for i in range(4):
            setattr(self, "conv%d" %i, torch.nn.Conv2d(in_ch, self.channels[i], 3, self.strides[i], 1, bias=False))
            setattr(self, "bn%d" %i, torch.nn.BatchNorm2d(self.channels[i]))
            in_ch = self.channels[i]
        self.gap = gap
        if gap:
            self.out = torch.nn.Linear(self.channels[-1], output_size)
        else:
            self.out = torch.nn.Linear(self.channels[-1] * 4 * 4, output_size)
        
    def forward(self, x):
        for i in range(4):
            conv = getattr(self, "conv%d" %i)
            bn = getattr(self, "bn%d" %i)
            x = conv(x)
            x = F.leaky_relu(bn(x), inplace=True)
        if self.gap:
            return self.out(x.mean(3).mean(2))
        else:
            return self.out(x.view(x.size(0), -1))

class MLP(torch.nn.Module):
    def __init__(self, ni, no, nhidden, depth):
        super().__init__()
        self.depth = depth
        for i in range(depth):
            if i == 0:
                setattr(self, "linear%d" %i, torch.nn.Linear(ni, nhidden))
            else:
                setattr(self, "linear%d" %i, torch.nn.Linear(nhidden, nhidden))
        if depth == 0:
            nhidden = ni
        self.out = torch.nn.Linear(nhidden, no)

    def forward(self, x):
        x = x.view(x.size(0), -1)
        for i in range(self.depth):
            linear = getattr(self, "linear%d" %i)
            x = F.leaky_relu(linear(x))
        return self.out(x)

def get_backbone(exp_dict):
    nclasses = exp_dict["num_classes"]
    backbone_name = exp_dict["backbone"]["name"].lower()
    if backbone_name == "resnet18":
        backbone = models.resnet18(pretrained=exp_dict["backbone"]["imagenet_pretraining"], progress=True)
        num_ftrs = backbone.fc.in_features
        backbone.fc = torch.nn.Linear(num_ftrs, nclasses) 
        if exp_dict["dataset"]["channels"] != 3:
            assert(not(exp_dict["backbone"]["imagenet_pretraining"]))
            backbone._modules['conv1'] = torch.nn.Conv2d(exp_dict["dataset"]["channels"], 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        return backbone
    elif backbone_name == "resnet50":
        backbone = models.resnet50(pretrained=exp_dict["backbone"]["imagenet_pretraining"], progress=True)
        num_ftrs = backbone.fc.in_features
        backbone.fc = torch.nn.Linear(num_ftrs, nclasses) 
        if exp_dict["dataset"]["channels"] != 3:
            assert(not(exp_dict["backbone"]["imagenet_pretraining"]))
            backbone._modules['conv1'] = torch.nn.Conv2d(exp_dict["dataset"]["channels"], 64, kernel_size=(7, 7), stride=(2, 2), padding=(3, 3), bias=False)
        return backbone
    elif backbone_name == "wrn":
        backbone = WideResNet(exp_dict["backbone"].get("depth", 28), exp_dict["backbone"].get("width", 10), nclasses)
        if exp_dict["dataset"]["channels"] != 3:
            backbone._modules['conv0'] = torch.nn.Conv2d(exp_dict["dataset"]["channels"], 16, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1), bias=False)
        return backbone
    elif backbone_name == "resnet12":
        backbone = Resnet12(1, exp_dict["dataset"]["channels"], nclasses)
        return backbone
    elif backbone_name == "vgg16":
        backbone = models.vgg16_bn(pretrained=exp_dict["backbone"]["imagenet_pretraining"], progress=True)
        if exp_dict["dataset"]["channels"] != 3:
            assert(not(exp_dict["backbone"]["imagenet_pretraining"]))
            backbone._modules['features'][0] = torch.nn.Conv2d(exp_dict["dataset"]["channels"], 64, kernel_size=(3, 3), stride=(1, 1), padding=(1, 1))
        children = list(backbone.children())
        children = children[:-2]
        output = []
        output.append(GAP())
        output.append(torch.nn.Linear(512, 4096))
        output.append(torch.nn.ReLU(True))
        output.append(torch.nn.Linear(4096, 4096))
        output.append(torch.nn.ReLU(True))
        output.append(torch.nn.Linear(4096, nclasses))
        output = torch.nn.Sequential(*output)
        children.append(output)
        return torch.nn.Sequential(*children)
    elif backbone_name == "conv4":
        return Conv4(exp_dict["dataset"]["height"], 
                     exp_dict["dataset"]["width"],
                     exp_dict["dataset"]["channels"],
                     output_size=nclasses,
                     gap=exp_dict["backbone"]["gap"],
                     )
    elif backbone_name == "mlp":
        return MLP(ni=exp_dict["dataset"]["height"] * exp_dict["dataset"]["width"] * exp_dict["dataset"]["channels"],
                   no=nclasses,
                   nhidden=exp_dict["backbone"]["hidden_size"],
                   depth=exp_dict["backbone"]["depth"])
    else:
        raise ValueError

