

import os, sys
import dgl
sys.path.append('.')

from glob import glob
from configs import *
from tqdm import tqdm
from utils import *
import numpy as np
# from dataset import *k
from days import *
from time import time
tqdm.monitor_interval = 0
import torch
import json
from time import time
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import matplotlib.pyplot as plt
from random import shuffle
import argparse
torch.manual_seed(0)
torch.cuda.manual_seed(0)
np.random.seed(0)


parser = argparse.ArgumentParser(description='Process some integers.')
parser.add_argument('graph', type=str)
parser.add_argument('--epochs', type=int, default=2)
args = parser.parse_args()


# graph_file = '/beegfs/ua349/archive/data/graphs/400861-400948_n2.json'
segs, adjlist = read_graph(args.graph, verbose=False, named_adj=True)
rootseg = segs[0]

TAG = 'mprnn'
save_path = '%s/%s/%s.pth' % (CKPT_STORAGE, TAG, fileName(sys.argv[1]))
print('Saving to:')
print(save_path)

n_lag = 24
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

# Dataset
dset = DayHistory(segs, 'train', bsize=32)#.generator()
valset = DayHistory(segs, 'test', bsize=32)#.generator()

from models.MPRNN import MPRNN

comp_segs, comp_adj = complete_graph(segs, adjlist)
mprnn = MPRNN(comp_segs, comp_adj, hidden_size=128, verbose=True).cuda()
dev = torch.device('cuda:0')
mprnn.device = dev
criterion, opt, sch = mprnn.params(lr=0.001)

# Train
n2t = lambda arr: torch.from_numpy(np.array(arr)).cuda().float()
def format_batch(inds, data, squeeze=True):
    batch = []
    labels = []

    for di, hi in inds:
        X = data[di][hi-n_lag:hi].copy()
        X[np.isnan(X)] = -1
        X = X.T
        Y = data[di][hi-n_lag+1:hi+1].copy()
        batch.append([[n2t(step).unsqueeze(0).unsqueeze(0)
                       for step in node] for node in X])
        labels.append(Y)

    batch = batch[0]
#     print(len(batch), len(batch[0]))
#     assert False


    labels = np.array(labels)
    labels = n2t(labels)

    lmasks = ((~torch.isnan(labels))).type(torch.bool).cuda()

    return batch, labels, lmasks

inds = trainable_inds(dset.data)
eval_inds = trainable_inds(valset.data)

print('Pre-evaluate:')
evf = lambda: evaluate_v2(
    eval_inds, valset, mprnn,
    lambda preds, label, mask: criterion(preds[-1][mask[-1]], label[-1][mask[-1]]),
    format_batch)
best_eval = evf()

print('Train:')
eval_mse = [best_eval]
for epoch in range(args.epochs):
	mprnn.train()
	shuffle(inds)
	for ii, (di, hi) in enumerate(inds):
		# forward
		batch, labels, lmasks = format_batch([(di, hi)], dset.data)
		preds = mprnn(batch)

		loss = criterion(preds[lmasks], labels[lmasks])

		opt.zero_grad()
		loss.backward()
		opt.step()

		sys.stdout.write('\r[%d/%d]: %d/%d    ' % (
			epoch+1, args.epochs,
			ii, len(inds)))
	sys.stdout.write('\n')
	sys.stdout.flush()

	last_eval = evf()
	if last_eval < best_eval:
		torch.save(mprnn.state_dict(), save_path)
		best_eval = last_eval
	eval_mse.append(last_eval)


logfl = '%s/%s/%s_log.json' % (LOG_PATH, TAG, fileName(sys.argv[1]))
print('Log:', logfl)
with open(logfl, 'w') as fl:
	json.dump([
		eval_mse,
		best_eval,
	], fl, indent=4)
