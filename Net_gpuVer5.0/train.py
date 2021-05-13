from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os
import time
import pprint
import shutil
import sys

import torch
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim
import torch.utils.data
import torch.utils.data.distributed
import models
import dataset
import torchvision.datasets as datasets
import torchvision.transforms as transforms

from tensorboardX import SummaryWriter
from datetime import datetime
from config import config
from config import update_config
from core.function import train, validate, test
from core.loss import Loss
from utils.modelsummary import get_model_summary
from utils.utils import get_optimizer
from utils.utils import init_log
# from utils.utils import save_checkpoint
# from utils.utils import create_logger
import cv2 as cv
import numpy as np
datapath='/home/mist/Face_Xray/data/'
class mydataset(torch.utils.data.Dataset):
    def __init__(self,blendpath,originpath):
        self.imglist=[]
        self.xraylist=[]
        self.label=[]
        self.trans_image =dataset.aug_trans.aug_trans.data_transform(normalize=True)
        self.trans_xray = dataset.aug_trans.aug_trans.data_transform(normalize=False)
        # for path in os.listdir('D:/Face_Xray/venv/facexray/Face_Xray/data/generatorBlendedRandomGaussian'):
        #     self.imglist.append('D:/Face_Xray/venv/facexray/Face_Xray/data/generatorBlendedRandomGaussian'+'/'+path)
        i=0
        for path in os.listdir(blendpath):
            if i%2==0:
                self.imglist.append(blendpath+'/'+path)
                self.label.append(1)
            else:
                self.xraylist.append(blendpath+'/'+path)
            i+=1
        for path in os.listdir(originpath):
            self.imglist.append(originpath+'/'+path)
            self.label.append(0)
            self.xraylist.append('0')
            i+=1
    def __getitem__(self, index):
        cls = self.label[index]
        img=cv.imread(self.imglist[index])
        img=cv.cvtColor(img,cv.COLOR_BGR2RGB)
        if cls==0:
            xray=np.zeros_like(img)
        else:
            xray=cv.imread(self.xraylist[index])
        xray=cv.cvtColor(xray,cv.COLOR_BGR2GRAY)
        origin_size=img.size
        img = self.trans_image(image=img)['image']
        xray = self.trans_xray(image=xray)['image']
        xray=xray.unsqueeze(0)
        name=self.imglist[index]
        return img,xray,cls,np.array(origin_size),name
    def __len__(self):
        return len(self.label)

def parse_args():
    parser = argparse.ArgumentParser(description='Train classification network')

    parser.add_argument('--cfg',
                        help='experiment configure file name',
                        type=str,
                        default='./hrw18_adam_lr5e-2_bs32.yaml')

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
    parser.add_argument('--testNNB',
                        help='testNNB',
                        type=str,
                        default='/home/mist/Face_Xray/data/hrnetv2_w18_imagenet_pretrained.pth')
    parser.add_argument('--testNNC',
                        help='testNNC',
                        type=str,
                        default='')

    args = parser.parse_args()
    update_config(config, args)

    return args


def main():
    args = parse_args()

    # cudnn related setting
    cudnn.benchmark = config.CUDNN.BENCHMARK
    torch.backends.cudnn.deterministic = config.CUDNN.DETERMINISTIC
    torch.backends.cudnn.enabled = config.CUDNN.ENABLED

    nnb = models.nnb.get_nnb(config)  # 不锁定参数  TODO: optimzer 中途添加参数
    # nnb = models.ae.get_ae()
    # nnb = models.fcn.get_fcn(config)
    # 训练时令nnc的softmax不起作用
    nnc = models.nnc.get_nnc(config)

    writer_dict = {
        'writer': SummaryWriter(log_dir='./output/facexray/tensorboard/tensorboard' + '_' + datetime.now().strftime('%Y%m%d_%H%M%S')),
        'train_global_steps': 0,
        'valid_global_steps': 0,
        'test_global_steps': 0,
    }

    # log init
    save_dir = os.path.join('./output/facexray/log/log' + '_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
    if os.path.exists(save_dir):
        raise NameError('model dir exists!')
    os.makedirs(save_dir)
    logging = init_log(save_dir)
    _print = logging.info

    gpus = list(config.GPUS)
    nnb = torch.nn.DataParallel(nnb, device_ids=[0]).cuda()
    nnc = torch.nn.DataParallel(nnc, device_ids=[0]).cuda()

    # define loss function (criterion) and optimizer
    criterion = Loss()

    # 一些参数
    # 初始化optimzer，训练除nnb的原hrnet参数外的参数
    optimizer = get_optimizer(config, [nnb, nnc])  # TODO: 暂时直接全部初始化
    NNB_GRAD = False
    nnb.module.pretrained_grad(NNB_GRAD)
    last_iter = config.TRAIN.BEGIN_ITER
    best_perf = 0.0

    if isinstance(config.TRAIN.LR_STEP, list):
        lr_scheduler = torch.optim.lr_scheduler.MultiStepLR(
            optimizer, config.TRAIN.LR_STEP, config.TRAIN.LR_FACTOR,
            last_iter - 1
        )
    else:
        lr_scheduler = torch.optim.lr_scheduler.StepLR(
            optimizer, config.TRAIN.LR_STEP, config.TRAIN.LR_FACTOR,
            last_iter - 1
        )

    # Data loading code
    # transform还没能适用于其他规格，应做成[256, 256, 3]
#     train_dataset = eval('dataset.' + config.DATASET.TRAIN_SET + '.' + config.DATASET.TRAIN_SET)(
#         root=config.DATASET.TRAIN_ROOT, list_name=config.DATASET.TRAIN_LIST, mode='train', Transform='simple')

#     valid_dataset = eval('dataset.' + config.DATASET.EVAL_SET + '.' + config.DATASET.EVAL_SET)(
#         root=config.DATASET.VALID_ROOT, list_name=config.DATASET.VALID_LIST, mode='valid', Transform='simple')

#     test_dataset = eval('dataset.' + config.DATASET.EVAL_SET + '.' + config.DATASET.EVAL_SET)(
#         root=config.DATASET.TEST_ROOT, list_name=config.DATASET.TEST_LIST, mode='test', Transform='simple')
    train_dataset=mydataset(datapath+'train15k',datapath+'origin5k')
    valid_dataset=mydataset(datapath+'generatorBlendedRandomGaussian',datapath+'origin')
    test_dataset=mydataset(datapath+'test1k',datapath+'test_o500')
    train_loader = torch.utils.data.DataLoader(
        train_dataset,
        batch_size=config.TRAIN.BATCH_SIZE_PER_GPU*len(gpus),
        shuffle=config.TRAIN.SHUFFLE,
        num_workers=config.WORKERS,
        pin_memory=config.PIN_MEMORY
    )
    def cycle(loader):
        while True:
            for x in loader:
                yield x
            op = getattr(loader.dataset, "generate", None)
            if callable(op):
                op()
    train_generator = iter(cycle(train_loader))

    valid_loader = torch.utils.data.DataLoader(
        valid_dataset,
        batch_size=config.TEST.BATCH_SIZE_PER_GPU*len(gpus),
        shuffle=False,
        num_workers=config.WORKERS,
        pin_memory=config.PIN_MEMORY
    )

    test_loader = torch.utils.data.DataLoader(
        test_dataset,
        batch_size=config.TEST.BATCH_SIZE_PER_GPU*len(gpus),
        shuffle=False,
        num_workers=config.WORKERS,
        pin_memory=config.PIN_MEMORY
    )

    for iteration in range(last_iter, config.TRAIN.END_ITER, config.TRAIN.EVAL_ITER):

        # 前50000次迭代锁定原hrnet层参数训练，后面的迭代训练所有参数
        if not NNB_GRAD and iteration >= 50000:
            if len(gpus) > 0:
                nnb.module.pretrained_grad(True)
            else:
                nnb.pretrained_grad(True)
            NNB_GRAD = True

        # train for one epoch
        train(config, train_generator, nnb, nnc, criterion, optimizer, iteration, writer_dict, _print, lr_scheduler=lr_scheduler)
        # evaluate on validation set
        perf_indicator = validate(config, valid_loader, nnb, nnc, criterion, writer_dict, _print)
        test(config, test_loader, nnb, nnc, criterion, writer_dict, _print)

        # 保存目前准确率最高的模型
        # if perf_indicator > best_perf:
        #    best_perf = perf_indicator
        #    torch.save(model.module.state_dict(), './output/BI_dataset/bestfaceXray_'+str(best_perf)+'.pth')
        #    _print('[Save best model] ./output/BI_dataset/bestfaceXray_'+str(best_perf)+'.pth\t')

        iter_now = iteration+config.TRAIN.EVAL_ITER
        if (iteration // config.TRAIN.EVAL_ITER) % 2 == 0:
            torch.save(nnb.module.state_dict(), './output/BI_dataset2/faceXray_'+str(iter_now)+'.pth')
            torch.save(nnc.module.state_dict(), './output/BI_dataset2/nnc'+str(iter_now)+'.pth')
            _print('[Save model] ./output/BI_dataset2/faceXray_'+str(iter_now)+'.pth\t')
            _print('[Save the last model] ./output/BI_dataset2/nnc'+str(iter_now)+'.pth\t')
        # lr_scheduler.step()

    # 最后的模型
    torch.save(nnb.module.state_dict(), './output/BI_dataset/faceXray.pth')
    torch.save(nnc.module.state_dict(), './output/BI_dataset/nnc.pth')
    _print('[Save the last model] ./output/BI_dataset/faceXray.pth\t')
    _print('[Save the last model] ./output/BI_dataset/nnc.pth\t')
    writer_dict['writer'].close()

if __name__ == '__main__':
    main()
