from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os
import pprint
import shutil
import sys

import torch
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import torchvision.datasets as datasets
import torchvision.transforms as transforms
from tensorboardX import SummaryWriter

import models
import dataset
from config import config
from config import update_config
from core.function import train
from core.function import validate
from core.loss import Loss
from utils.modelsummary import get_model_summary
from utils.utils import get_optimizer
# from utils.utils import save_checkpoint
# from utils.utils import create_logger


def parse_args():
    parser = argparse.ArgumentParser(description='Train classification network')

    parser.add_argument('--cfg',
                        help='experiment configure file name',
                        type=str,
                        default='./nnb_adam_lr5e-2_bs32.yaml')

    parser.add_argument('--modelDir',
                        help='model directory',
                        type=str,
                        default='')
    parser.add_argument('--logDir',
                        help='log directory',
                        type=str,
                        default='')
    parser.add_argument('--dataDir',
                        help='data directory',
                        type=str,
                        default='')
    parser.add_argument('--testModel',
                        help='testModel',
                        type=str,
                        default='./hrnet_w32-36af842e.pth')

    args = parser.parse_args()
    update_config(config, args)

    return args


def main():
    args = parse_args()

    # cudnn related setting
    cudnn.benchmark = config.CUDNN.BENCHMARK
    torch.backends.cudnn.deterministic = config.CUDNN.DETERMINISTIC
    torch.backends.cudnn.enabled = config.CUDNN.ENABLED

    model = eval('models.' + config.MODEL.NAME + '.get_nnb')(
        config)

    writer_dict = {
        'writer': SummaryWriter(log_dir='./output/facexray'),
        'train_global_steps': 0,
        'valid_global_steps': 0,
    }

    gpus = list(config.GPUS)
    model = torch.nn.DataParallel(model)

    # define loss function (criterion) and optimizer
    criterion = Loss()

    optimizer = get_optimizer(config, model)

    last_epoch = config.TRAIN.BEGIN_EPOCH

    if isinstance(config.TRAIN.LR_STEP, list):
        lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(
            optimizer, config.TRAIN.LR_STEP, config.TRAIN.LR_FACTOR,
            last_epoch - 1
        )
    else:
        lr_scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer, config.TRAIN.LR_STEP, config.TRAIN.LR_FACTOR,
            last_epoch - 1
        )

    # Data loading code
    # list_name没有单独标注在.yaml文件
    # transform还没能适用于其他规格，应做成[256, 256, 3]
    train_dataset = eval('dataset.' + config.DATASET.DATASET + '.' + config.DATASET.DATASET)(
        config.DATASET.ROOT, config.DATASET.TRAIN_SET, None,
        transforms.Compose([
            transforms.ToTensor()
        ])
    )

    valid_dataset = eval('dataset.' + config.DATASET.DATASET + '.' + config.DATASET.DATASET)(
        config.DATASET.ROOT, config.DATASET.TEST_SET, None,
        transforms.Compose([
            transforms.ToTensor()
        ])
    )

    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.TRAIN.BATCH_SIZE_PER_GPU,
        shuffle=config.TRAIN.SHUFFLE,
        num_workers=config.WORKERS,
        pin_memory=config.PIN_MEMORY
    )

    valid_loader = torch.utils.data.DataLoader(
        valid_dataset,
        batch_size=config.TEST.BATCH_SIZE_PER_GPU,
        shuffle=False,
        num_workers=config.WORKERS,
        pin_memory=config.PIN_MEMORY
    )

    for epoch in range(last_epoch, config.TRAIN.END_EPOCH):
        lr_scheduler.step()

        # 前50000次迭代锁定原hrnet层参数训练，后面的迭代训练所有参数
        if epoch == 150000:
            for k, v in model.named_parameters():
                v.requires_grad = True

        # train for one epoch
        train(config, train_loader, model, criterion, optimizer, epoch, writer_dict)
        # evaluate on validation set
        validate(config, valid_loader, model, criterion, writer_dict)

    torch.save(model.module.state_dict(), './output/BI_dataset/faceXray.pth')
    writer_dict['writer'].close()


if __name__ == '__main__':
    main()
