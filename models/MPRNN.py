
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
from models.GRNN import *

class MP_THIN(nn.Module):
	def __init__(self, hsize):
		super(MP_THIN, self).__init__()

		self.msg_op = nn.Linear(hsize*2, hsize)
		self.upd_op = nn.Linear(hsize*2, hsize)

	def msg(self, hself, hother):
		hcat = torch.cat([hself, hother], -1)
		return self.msg_op(hcat)

	def upd(self, hself, msg):
		hcat = torch.cat([hself, msg], -1)
		return self.upd_op(hcat)

class MP_DENSE(MP_THIN):
	def __init__(self, hsize):
		super(MP_DENSE, self).__init__(hsize)

		self.msg_op = nn.Sequential(
			nn.Linear(hsize*2, hsize),
			nn.ReLU(),
			nn.Linear(hsize, hsize),
		)
		self.upd_op = nn.Sequential(
			nn.Linear(hsize*2, hsize),
			nn.ReLU(),
			nn.Linear(hsize, hsize),
		)

class MPRNN(GRNN):
	'''
	Instantiates one RNN per location in input graph.
	Additionally, instantiates a message passing layer per node.

	MPNs are specified by the given adjacency matrix (list?).
	'''

	def __init__(self,
		nodes,
		adj,
		hidden_size=256,
		rnnmdl=RNN_MIN,
		mpnmdl=MP_THIN,
		verbose=False):
		super(MPRNN, self).__init__(len(nodes), hidden_size, rnnmdl)

		self.adj = adj
		self.nodes = nodes

		mpns = []
		self.mpn_ind = {}
		for nname, adjnames in adj.items():
			if len(adjnames):
				self.mpn_ind[nname] = len(mpns)
				mpns.append(mpnmdl(hsize=hidden_size))
		self.mpns = nn.ModuleList(mpns)

		if verbose:
			print('MPRNN')
			print(' [*] Defined over: %d nodes' % len(nodes))
			print(' [*] Contains    : %d adjs' % len(adj))

	def clear_stats(self):
		self.msgcount = [0 for _ in self.nodes]
		self.updcount = [0 for _ in self.nodes]

	def print_stats(self):
		for ni, nname in enumerate(self.nodes):
			print('Node:', nname)
			print('  * Msgs received: %d' % self.msgcount[ni])
			print('  * Msgs updated : %d' % self.updcount[ni])

	def eval_hidden(self, ti, nodes, hidden):
		hevals = []
		for ni, (node_series, rnn, hdn) in enumerate(zip(nodes, self.rnns, hidden)):
			value_t = node_series[ti]

			hin = rnn.inp(value_t).unsqueeze(0)
			hout, hdn = rnn.rnn(hin, hdn)
			hout = hout.squeeze(0)

			hevals.append(hout)
			hidden[ni] = hdn # replace previous lstm params
		return hevals

	def eval_message(self, hevals):
		msgs = []
		for ni, (hval, nname) in enumerate(zip(hevals, self.nodes)):
			# only defined over nodes w/ adj
			if nname not in self.mpn_ind:
				msgs.append(None)
				continue

			mpn = self.mpns[self.mpn_ind[nname]]
			many = []
			ninds = [self.nodes.index(neighb) for neighb in self.adj[nname]]
			assert len(ninds)
			for neighbor_i in ninds:
				many.append(mpn.msg(hval, hevals[neighbor_i]))
			many = torch.stack(many, -1)
			msg = torch.sum(many, -1)
			msgs.append(msg)

			self.msgcount[ni] += 1
		return msgs

	def eval_update(self, hevals, msgs):
		for ni, (hval, msg, nname) in enumerate(zip(hevals, msgs, self.nodes)):
			# only defined over nodes w/ adj
			if msg is None: continue
			mpn = self.mpns[self.mpn_ind[nname]]

			# replaces hvalues before update
			hevals[ni] = mpn.upd(hval, msg)

			self.updcount[ni] += 1

	def eval_readout(self, hevals):
		values_t = []
		for ni, (hval, rnn) in enumerate(zip(hevals, self.rnns)):
			values_t.append(rnn.out(hval))
		return values_t

	def forward(self, series, hidden=None, dump=False):
		# print(len(series), len(series[0]), series[0][0].size())
		assert len(self.rnns) == len(series)

		# lstm params
		if hidden is None:
			hidden = [None] * len(series)

		# defined over input timesteps
		tsteps = len(series[0])
		outs_bynode = [list() for _ in series]
		for ti in range(tsteps):

			# eval up to latent layer for each node
			hevals = self.eval_hidden(ti, series, hidden)

			# message passing
			msgs = self.eval_message(hevals)

			# updating hidden
			self.eval_update(hevals, msgs)

			# read out values from hidden
			values_t = self.eval_readout(hevals)

			for node_series, value in zip(outs_bynode, values_t):
				node_series.append(value)

		# print(len(outs_bynode), len(outs_bynode[0]), outs_bynode[0][0].size())
		out = list(map(lambda tens: torch.cat(tens, dim=1), outs_bynode))
		out = torch.stack(out, dim=-1)

		if dump:
			return out, hidden
		else:
			return out

	def params(self, lr=0.001):
		criterion = nn.MSELoss().cuda()
		opt = optim.SGD(self.parameters(), lr=lr)
		sch = optim.lr_scheduler.StepLR(opt, step_size=25, gamma=0.5)
		return criterion, opt, sch

class CMPN(MPRNN):
	'''
	Instantiates one RNN per location in input graph.
	Additionally, instantiates a message passing layer per node.

	MPNs are specified by the given adjacency matrix (list?).

	Value of root is predicted (t) given all known values at (t-h).
	(conditional predictions)
	'''

	def __init__(self,
		nodes,
		adj,
		hidden_size=256,
		rnnmdl=RNN_MIN,
		mpnmdl=MP_THIN,
		verbose=False):

		super(CMPN, self).__init__(nodes, adj, hidden_size, rnnmdl, mpnmdl, verbose)

		hsize = hidden_size
		self.ro_agg = nn.Sequential(
			nn.Linear(hsize, hsize),
			nn.ReLU(),
			nn.Linear(hsize, hsize),
		)

		self.readout = nn.Sequential(
			nn.Linear(hsize, 1)
		)

	def eval_readout(self, hevals):
		values_t = []
		for ni, (hval, rnn) in enumerate(zip(hevals, self.rnns)):
			values_t.append(self.ro_agg(hval))
		values_t = torch.stack(values_t, dim=-1)
		agg = torch.sum(values_t, dim=-1)

		root_t = self.readout(agg)
		return root_t

	def forward(self, series, hidden=None, dump=False):
		# print(len(series), len(series[0]), series[0][0].size())
		assert len(self.rnns) == len(series)

		# lstm params
		if hidden is None:
			hidden = [None] * len(series)

		# defined over input timesteps
		tsteps = len(series[0])
		# outs_bynode = [list() for _ in series]
		root_outs = []
		for ti in range(tsteps):

			# eval up to latent layer for each node
			hevals = self.eval_hidden(ti, series, hidden)

			# message passing
			msgs = self.eval_message(hevals)

			# updating hidden
			self.eval_update(hevals, msgs)

			# deduce value of root from graph-wide readout
			root_t = self.eval_readout(hevals)
			root_outs.append(root_t)

		out = torch.stack(root_outs, dim=1)

		if dump:
			return out, hidden
		else:
			return out

	def format_batch(self, data, withold=None):
		all_known = data[:, :, :]

		bystop = torch.split(all_known, 1, 2)
		allXs, allYs = [], []
		for known in bystop:
			# raw   : batch x timelen x seqlen
			data = torch.transpose(known, 1, 0)
			# fmt   : timelen x batch x seqlen

			bytime = list(torch.split(data, 1, dim=0))
			seq = list(map(lambda tens: tens.to(self.device).float().squeeze(0), bytime))

			Xs = seq[:-1]
			Ys = seq[1:] # predict immediately following values

			Ys = torch.stack(Ys, dim=1) # restack Ys by temporal
			allXs.append(Xs)
			allYs.append(Ys)

		rootY = allYs[0]
		# allYs = torch.cat(allYs, dim=2)

		return allXs, rootY
