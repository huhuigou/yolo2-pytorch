"""
Copyright (C) 2017, 申瑞珉 (Ruimin Shen)

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

import numpy as np
import torch
import torch.nn as nn
import torch.autograd

import model


def reorg(x, stride_h=2, stride_w=2):
    batch_size, channels, height, width = x.size()
    _height, _width = height // stride_h, width // stride_w
    if 1:
        x = x.view(batch_size, channels, _height, stride_h, _width, stride_w).transpose(3, 4).contiguous()
        x = x.view(batch_size, channels, _height * _width, stride_h * stride_w).transpose(2, 3).contiguous()
        x = x.view(batch_size, channels, stride_h * stride_w, _height, _width).transpose(1, 2).contiguous()
        x = x.view(batch_size, -1, _height, _width)
    else:
        x = x.view(batch_size, channels, _height, stride_h, _width, stride_w)
        x = x.permute(0, 1, 3, 5, 2, 4) # batch_size, channels, stride, stride, _height, _width
        x = x.contiguous()
        x = x.view(batch_size, -1, _height, _width)
    return x


class Conv2d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, act=True, same_padding=False):
        nn.Module.__init__(self)
        padding = int((kernel_size - 1) / 2) if same_padding else 0
        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding=padding)
        self.act = nn.LeakyReLU(0.1, inplace=True) if act else None

    def forward(self, x):
        x = self.conv(x)
        if self.act is not None:
            x = self.act(x)
        return x


class Conv2d_BatchNorm(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, stride=1, act=True, same_padding=False):
        nn.Module.__init__(self)
        padding = int((kernel_size - 1) / 2) if same_padding else 0

        self.conv = nn.Conv2d(in_channels, out_channels, kernel_size, stride, padding=padding, bias=False)
        self.bn = nn.BatchNorm2d(out_channels, momentum=0.01)
        self.act = nn.LeakyReLU(0.1, inplace=True) if act else None

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        if self.act is not None:
            x = self.act(x)
        return x


class Darknet(nn.Module):
    def __init__(self, config, anchors, num_cls, stride=2):
        nn.Module.__init__(self)
        self.stride = stride
        channels_in = 3
        channels_out = 32
        layers = []

        # layers1
        for _ in range(2):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(nn.MaxPool2d(kernel_size=2))
            channels_out *= 2
        # down 4
        for _ in range(2):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(Conv2d_BatchNorm(channels_in, channels_out // 2, 1))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(nn.MaxPool2d(kernel_size=2))
            channels_out *= 2
        # down 16
        for _ in range(2):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(Conv2d_BatchNorm(channels_in, channels_out // 2, 1))
            channels_in = layers[-1].conv.weight.size(0)
        layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
        channels_in = layers[-1].conv.weight.size(0)
        self.layers1 = nn.Sequential(*layers)

        # layers2
        layers = []
        layers.append(nn.MaxPool2d(kernel_size=2))
        channels_out *= 2
        # down 32
        for _ in range(2):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(Conv2d_BatchNorm(channels_in, channels_out // 2, 1))
            channels_in = layers[-1].conv.weight.size(0)
        for _ in range(3):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
        self.layers2 = nn.Sequential(*layers)

        self.passthrough = Conv2d_BatchNorm(self.layers1[-1].conv.weight.size(0), 64, 1)

        # layers3
        layers = []
        channels_in += self.passthrough.conv.weight.size(0) * self.stride * self.stride # reorg
        layers.append(Conv2d_BatchNorm(channels_in, 1024, 3, same_padding=True))
        channels_in = layers[-1].conv.weight.size(0)
        layers.append(Conv2d(channels_in, model.output_channels(len(anchors), num_cls), 1, act=False))
        self.layers3 = nn.Sequential(*layers)

        # init
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                m.weight = nn.init.xavier_normal(m.weight)

    def forward(self, x):
        x = self.layers1(x)
        _x = reorg(self.passthrough(x), self.stride)
        x = self.layers2(x)
        x = torch.cat([_x, x], 1)
        return self.layers3(x)


class Tiny(nn.Module):
    def __init__(self, config, anchors, num_cls):
        nn.Module.__init__(self)
        channels_in = 3
        channels_out = 16
        layers = []

        for _ in range(5):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
            layers.append(nn.MaxPool2d(kernel_size=2))
            channels_out *= 2
        layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
        channels_in = layers[-1].conv.weight.size(0)
        layers.append(nn.ConstantPad2d((0, 1, 0, 1), float(np.finfo(np.float32).min)))
        layers.append(nn.MaxPool2d(kernel_size=2, stride=1))
        channels_out *= 2
        for _ in range(2):
            layers.append(Conv2d_BatchNorm(channels_in, channels_out, 3, same_padding=True))
            channels_in = layers[-1].conv.weight.size(0)
        layers.append(Conv2d(channels_in, model.output_channels(len(anchors), num_cls), 1, act=False))
        self.layers = nn.Sequential(*layers)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                m.weight = nn.init.xavier_normal(m.weight)

    def forward(self, x):
        return self.layers(x)
