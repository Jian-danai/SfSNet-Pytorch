# coding=utf-8
from __future__ import absolute_import, division, print_function

import torch
from src import *
from torch.utils.data import DataLoader
from config import SFSNET_DATASET_DIR, SFSNET_DATASET_DIR_NPY
import os
import time
from config import PROJECT_DIR, M
import multiprocessing
import pickle

if __name__ == '__main__':
    pass
    # model = SfSNet()
    # with open('data/temp_1554633573.72.pth', 'r') as f:
    #     model.load_state_dict(torch.load(f))
    # exit()


def weight_init(layer):
    if isinstance(layer, torch.nn.Conv2d) or isinstance(layer, torch.nn.Linear):
        torch.nn.init.xavier_uniform_(layer.weight.data)
        torch.nn.init.constant_(layer.bias.data, 0.)
    elif isinstance(layer, torch.nn.ConvTranspose2d):
        torch.nn.init.xavier_uniform_(layer.weight.data)


def load_train_config():
    try:
        with open(os.path.join(PROJECT_DIR, 'data/train.config.pkl'), 'rb') as f:
            train_config = pickle.load(f)
            return train_config
    except IOError as e:
        train_config = {'epoch': 0, 'learning_rate': 0.01, 'weight': ''}
        return train_config


def train():
    data_dir = os.path.join(PROJECT_DIR, 'data')
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    t = time.strftime('%Y.%m.%d_%H.%M.%S', time.localtime(time.time()))
    sta = Statistic('data/temp_%s.pth.csv' % t, True, 'epoch', 'step', 'total_step', 'learning_rate', 'loss')
    train_config = load_train_config()
    print(train_config)

    # define batch size
    batch_size = 32
    # define net
    # model = SfSNet()
    model = SfSNet()
    # init weights
    model.apply(weight_init)
    # load last trained weight
    if train_config['weight'] != '':
        with open(train_config['weight'], 'rb') as f:
            model.load_state_dict(torch.load(f))
    if torch.cuda.device_count() > 1:
        print("Let's use", torch.cuda.device_count(), "GPUs!")
        # dim = 0 [62, ...] -> [32, ...], [32, ...] on 2 GPUs
        model = torch.nn.DataParallel(model).cuda()
        # set batch size to 64
        batch_size = 64
    if torch.cuda.is_available():
        model = model.cuda()

    # set to train mode
    model.train()

    # define dataset
    # train_dset, test_dset = prepare_dataset(SFSNET_DATASET_DIR)
    train_dset, test_dset = prepare_processed_dataset(SFSNET_DATASET_DIR_NPY, size=M)

    # define dataloader
    dloader = DataLoader(train_dset, batch_size=batch_size, shuffle=True, num_workers=multiprocessing.cpu_count())

    # define optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=train_config['learning_rate'], weight_decay=0.0005)

    # learning rate scheduler
    lr_sch = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=[5000, 10000, 15000, 20000, 25000, 30000], gamma=0.5)
    # lr_sch = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.5, patience=500, verbose=True)
    # lr_sch = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, 1000, 1e-4)

    l2_layer = L2LossLayerWt(0.1, 0.1)
    l1_layer = L1LossLayerWt(0.5, 0.5)
    normal_layer = NormLayer()
    change_form_layer = ChangeFormLayer()
    if torch.cuda.is_available():
        shading_layer = ShadingLayer(gpu=True)
    else:
        shading_layer = ShadingLayer(gpu=False)

    step_size = int(len(train_dset)/batch_size)
    try:
        for epoch in range(train_config['epoch'], 500, 1):
            # fc_light_gt = label
            # label3 = label1 = label2
            print('*' * 80)
            print("epoch: ", epoch)
            train_config['epoch'] = epoch
            for step, (data, mask, normal, albedo, fc_light_gt, label) in enumerate(dloader):
                lr_sch.step(epoch * step_size + step)
                if torch.cuda.is_available():
                    data = data.cuda()
                    mask = mask.cuda()
                    normal = normal.cuda()
                    albedo = albedo.cuda()
                    fc_light_gt = fc_light_gt.cuda()
                    label = label.cuda()
                # forward net
                Nconv0, Acov0, fc_light = model(data)
                # ---------compute reconloss------------
                # normalize
                recnormal = normal_layer(Nconv0)
                # change channel of normal
                recnormalch = change_form_layer(recnormal)
                # compute shading
                shading = shading_layer(recnormalch, fc_light)
                # change channel od albedo
                albedoch = change_form_layer(Acov0)
                # get recon images
                recon = albedoch * shading
                # change channel format
                maskch = change_form_layer(mask)
                # compute mask with recon
                mask_recon = recon * maskch

                datach = change_form_layer(data)
                mask_data = datach * maskch

                reconloss = l1_layer(mask_recon, mask_data, label)
                # -------------aloss----------
                arec = Acov0 * mask
                albedo_m = albedo * mask
                aloss = l1_layer(arec, albedo_m, label)
                # -----------loss--------------
                nrec = Nconv0 * mask
                normal_m = normal * mask
                loss = l1_layer(nrec, normal_m, label)
                # ------------
                lloss = l2_layer(fc_light, fc_light_gt, label)

                total_loss = reconloss + aloss + loss + lloss
                # backward
                optimizer.zero_grad()
                total_loss.backward()
                optimizer.step()

                loss_ = total_loss.cpu().detach().numpy()
                # lr_sch.step(loss_)
                # save train log
                record = [epoch, step, epoch * step_size + step, optimizer.param_groups[0]['lr'], loss_]
                train_config['learning_rate'] = record[3]
                sta.add(*record)
                print(*record)

    except KeyboardInterrupt as e:
        print("用户主动退出...")
        pass
    except IOError as r:
        print("其它异常...")
        raise
    finally:
        sta.save()
        # save weights
        with open('data/temp_%s.pth' % t, 'wb') as f:
            if torch.cuda.device_count() > 1:
                torch.save(model.module.cpu().state_dict(), f)
            else:
                torch.save(model.cpu().state_dict(), f)
        # save train config
        train_config['weight'] = 'data/temp_%s.pth' % t
        with open(os.path.join(PROJECT_DIR, 'data/train.config.pkl'), 'wb') as f:
            pickle.dump(train_config, f, protocol=2)


if __name__ == '__main__':
    train()
