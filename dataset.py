
from tqdm import tqdm
from glob import glob
import os, sys
import numpy as np
from configs import *
from utils import *
import json
from time import time
from numpy.random import shuffle as npshuff
from torch.utils import data
from scipy.ndimage import gaussian_filter as blur

class Routes(data.Dataset):
	def __init__(self, mode, bsize, minValid=0.7,
		lag=6,
		index_file='min-data_2h.json',
		reserved='reserved_routes.json',
		overlap=True,
		smooth=False,
		device=None):

		self.device = device
		self.bsize = bsize
		self.mode = mode
		self.smooth = smooth
		self.lag = lag

		t0 = time()
		with open(index_file) as fl:
			meta = json.load(fl)

		print('Routes dataset: %s' %mode)
		print(' [*] Loaded routes:', len(meta), '(%.2fs)' % (time() - t0))

		# Filter reserved routes
		with open(reserved) as fl:
			res_metas = json.load(fl)
		res_names = [entry['name'] for entry in res_metas]
		if mode == 'train':
			meta = [entry for entry in meta if entry['name'] not in res_names]
		else:
			meta = [entry for entry in meta if entry['name'] in res_names]
			if not overlap:
				for route in meta:
					route['trainable'] = dedupe(route['trainable'])
		assert len(meta)
		print(' [*] Subset %s: %d (%s)' % (mode, len(meta), reserved))
		self.meta = meta

		t0 = time()
		self.refs = []
		for route in meta:
			for pair in route['trainable']:
				self.refs.append([route['name']] + pair)
		print(' [*] Loaded trainable inds:', len(self.refs), '(%.2fs)' % (time() - t0))
		t0 = time()

		if mode == 'train':
			npshuff(self.refs)
		self.ind = 0

	def size(self):
		return int(len(self.refs) // self.bsize)

	def __len__(self):
		return len(self.refs)

	def __getitem__(self, index):
		ref = self.refs[index]
		rname, ti, si = ref

		# for rname, ti, si in refs:
		mat = np.load('data/history/%s.npy' % (rname))
		hist = mat[ti-self.lag:ti, si-self.stops:si]
		try:
			assert np.count_nonzero(np.isnan(hist)) == 0
		except:
			print(hist)
			raise Exception('Nan found...')
		if self.smooth:
			hist = hist_smooth(hist)
		return hist

	def generator(self, shuffle=True):
		return data.DataLoader(self,
			batch_size=self.bsize,
			shuffle=shuffle,
			num_workers=6)

	def next(self, progress=True, maxval=40):
		raise Exception('Not imp')
		# batch = []
		# refs = self.refs[self.ind:self.ind+self.bsize]
		# for rname, ti, si in refs:
		# 	mat = np.load('data/history/%s.npy' % (rname))
		# 	hist = mat[ti-6:ti, si-10:si]

		# 	batch.append(hist)

		# if progress:
		# 	self.ind += self.bsize
		# 	if self.ind + self.bsize >= len(self.refs):
		# 		self.ind = 0
		# 		if self.mode == 'train':
		# 			npshuff(self.refs)

		# Xs = np.array(batch)
		# Xs[Xs > maxval] = maxval
		# Ys = Xs[:, -1, :] # most recent timestep is Y
		# Xs = Xs[:, :-1, :] # all previous
		# return Xs, Ys

class LocalRoute(Routes):
	def __init__(self,
		local,
		mode, bsize,
		lag=6,
		stops=5,
		local_split=0.8, # 0.2 recent will be used for testing
		meta_path=None,
		min_stride=1,
		# index_file='min-data.json',
		smooth=False,
		device=None):

		self.device = device
		self.bsize = bsize
		self.mode = mode
		self.smooth = smooth
		self.lag = lag
		self.stops = stops

		t0 = time()
		if meta_path is None:
			meta_path = 'metadata/%dh' % (int(lag/6))
		fpath = '%s/%s.json' % (meta_path, local)
		with open(fpath) as fl:
			meta = [json.load(fl)]
		self.meta = meta

		print('Locals dataset: %s (%s)' % (mode, fpath))
		print(' [*] Loaded routes:', len(meta), '(%.2fs)' % (time() - t0))
		print(' [*] Has trainable inds:', len(meta[0]['trainable']))

		self.refs = []
		split_ind = int(13248 * local_split)
		for route in meta:
			last_t = 0
			for pair in route['trainable']:
				# TODO: split train/test here
				ti, si = pair
				if (mode == 'train' and ti < split_ind) \
					or (mode == 'test' and ti >= split_ind):

					if ti >= last_t + min_stride:
						self.refs.append([route['name']] + pair)
						last_t = ti

		assert len(meta)
		print(' [*] Subset %s: %d' % (mode, len(self.refs)))

		if mode == 'train':
			npshuff(self.refs)
		self.ind = 0
		self.mat = np.load('data/history/%s.npy' % (local))
		self.maxval=40

	def __getitem__(self, index):
		ref = self.refs[index]
		rname, ti, si = ref
		hist = self.mat[ti-self.lag:ti, si-self.stops:si]
		assert np.count_nonzero(np.isnan(hist)) == 0
		hist[hist > self.maxval] = self.maxval
		if self.smooth:
			hist = hist_smooth(hist)
		return hist

	def __len__(self):
		return int((len(self.refs) // self.bsize) * self.bsize)

	def yavg(self):
		avg = 0
		for ref in self.refs:
			rname, ti, si = ref
			hist = self.mat[ti-1, si-self.stops:si]
			# hist = self.mat[ti-self.lag:ti, si-self.stops:si]
			assert np.count_nonzero(np.isnan(hist)) == 0
			avg += np.sum(hist) / (self.stops * len(self.refs))
		return avg

class SingleStop(LocalRoute):
	def __init__(self,
		local, stop_ind,
		mode, bsize,
		lag=6,
		stops=5,
		local_split=0.8, # 0.2 recent will be used for testing
		meta_path=None,
		min_stride=1,
		smooth=False,
		device=None):

		super().__init__(
			local,
			mode, bsize,
			lag,
			stops,
			local_split,
			meta_path, smooth, device)

		self.refs = []
		split_ind = int(13248 * local_split)
		for route in self.meta:
			last_t = 0
			for pair in route['trainable']:
				# TODO: split train/test here
				ti, si = pair
				if si != stop_ind:
					continue
				if (mode == 'train' and ti < split_ind) \
					or (mode == 'test' and ti >= split_ind):

					if ti >= last_t + min_stride:
						self.refs.append([route['name']] + pair)
						last_t = ti
					# self.refs.append([route['name']] + pair)

		print(' [*] Subset in Stop-%d: %d' % (stop_ind, len(self.refs)))

		if mode == 'train':
			npshuff(self.refs)
		self.ind = 0

if __name__ == '__main__':
	dset = Routes(bsize=32, index_file='min-data.json')

	# ts = []
	# for _ in tqdm(range(100)):
	# 	t0 = time()
	# 	batch = dset.next()
	# 	dt = time() - t0
	# 	ts.append(dt)
	# print(np.mean(ts))
	# batch = dset.next()
	# print(batch[0].shape)
	# print()

