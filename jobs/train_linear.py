
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
import numpy as np
import matplotlib.pyplot as plt
from random import shuffle
import argparse
torch.manual_seed(0)
torch.cuda.manual_seed(0)
np.random.seed(0)


parser = argparse.ArgumentParser(description='Process some integers.')
parser.add_argument('graph', type=str)
parser.add_argument('--epochs', type=int, default=10)
args = parser.parse_args()


# graph_file = '/beegfs/ua349/archive/data/graphs/400861-400948_n2.json'
segs, adjlist = read_graph(args.graph, verbose=False, named_adj=True)
rootseg = segs[0]

TAG = 'linear'
save_path = '%s/%s/%s.pth' % (CKPT_STORAGE, TAG, fileName(sys.argv[1]))
print('Saving to:')
print(save_path)

n_lag = 24
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

# Dataset
dset = DayHistory(segs, 'train', bsize=32)#.generator()
valset = DayHistory(segs, 'test', bsize=32)#.generator()

from models.Linear import *

model = Linear(lag=n_lag, stops=len(segs)).to(device)
model.device = device
loss_fcn, optimizer, sch = model.params()

# Train
n2t = lambda arr: torch.from_numpy(np.array(arr)).cuda().float()
def format_batch(inds, data, squeeze=True):
	batch = []
	labels = []
	lmasks = []
	for di, hi in inds:
		X = data[di][hi-n_lag:hi].T.copy()
		X[np.isnan(X)] = -1
		Y = data[di][hi:hi+1].T.copy()
		mask = np.logical_not(np.isnan(Y))

		batch.append(X)
		labels.append(Y)
		lmasks.append(mask)

	batch, labels = [n2t(ls).squeeze(0) for ls in [batch, labels]]
	batch = batch.view(n_lag * len(segs))
	lmasks = (1-torch.isnan(labels)).type(torch.ByteTensor).cuda()

	return batch, labels, lmasks

inds = trainable_inds(dset.data)
eval_inds = trainable_inds(valset.data)

print('Pre-evaluate:')
evf = lambda: evaluate_v2(eval_inds, valset, model, loss_fcn, format_batch)
best_eval = evf()

print('Train:')
eval_mse = [best_eval]
for epoch in range(args.epochs):
	model.train()
	shuffle(inds)
	for ii, (di, hi) in enumerate(inds):
		# forward
		batch, labels, lmasks = format_batch([(di, hi)], dset.data)
		preds = model(batch)

		loss = loss_fcn(preds[lmasks], labels[lmasks])

		optimizer.zero_grad()
		loss.backward()
		optimizer.step()


		sys.stdout.write('[%d/%d]: %d/%d  \r' % (
			epoch+1, args.epochs,
			ii, len(inds)))
	sys.stdout.write('\n')
	sys.stdout.flush()

	last_eval = evf()
	if last_eval < best_eval:
		torch.save(model.state_dict(), save_path)
		best_eval = last_eval
	eval_mse.append(last_eval)


logfl = '%s/%s/%s_log.json' % (LOG_PATH, TAG, fileName(sys.argv[1]))
print('Log:', logfl)
with open(logfl, 'w') as fl:
	json.dump([
		eval_mse,
		best_eval,
	], fl, indent=4)
