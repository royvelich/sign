# Copyright 2020 Twitter, Inc.
# SPDX-License-Identifier: Apache-2.0

import argparse
from tqdm import tqdm

import torch
import torch.nn.functional as F
from torch_sparse import SparseTensor
from torch_geometric.utils import to_undirected, dropout_adj

from ogb.nodeproppred import PygNodePropPredDataset

import numpy as np
from ppr import topk_ppr_matrix

def get_adj(row, col, N, asymm_norm=False, set_diag=True, remove_diag=False):
    
    adj = SparseTensor(row=row, col=col, sparse_sizes=(N, N))
    if set_diag:
        print('... setting diagonal entries')
        adj = adj.set_diag()
    elif remove_diag:
        print('... removing diagonal entries')
        adj = adj.remove_diag()
    else:
        print('... keeping diag elements as they are')
    return adj.to_scipy(layout='csr')
    if not asymm_norm:
        print('... performing symmetric normalization')
        deg = adj.sum(dim=1).to(torch.float)
        deg_inv_sqrt = deg.pow(-0.5)
        deg_inv_sqrt[deg_inv_sqrt == float('inf')] = 0
        adj = deg_inv_sqrt.view(-1, 1) * adj * deg_inv_sqrt.view(1, -1)
    else:
        print('... performing asymmetric normalization')
        deg = adj.sum(dim=1).to(torch.float)
        deg_inv = deg.pow(-1.0)
        deg_inv[deg_inv == float('inf')] = 0
        adj = deg_inv.view(-1, 1) * adj

    adj = adj.to_scipy(layout='csr')
    
    return adj

def main():
    parser = argparse.ArgumentParser(description='OGBN-papers100M (SIGN)')
    parser.add_argument('--file_name', type=str, default="test")
    parser.add_argument('--undirected_num_propagations', type=int, default=3)
    parser.add_argument('--directed_num_propagations', type=int, default=3)
    parser.add_argument('--undirected_dropedge_rate', type=float, default=0)#.4)
    parser.add_argument('--directed_dropedge_rate', type=float, default=0)#.2)
    parser.add_argument('--undirected', action='store_true')
    parser.add_argument('--directed', action='store_true')
    parser.add_argument('--undirected_asymm_norm', action='store_true')
    parser.add_argument('--directed_asymm_norm', action='store_true')
    parser.add_argument('--undirected_remove_diag', action='store_true')
    parser.add_argument('--undirected_set_diag', action='store_true')
    parser.add_argument('--directed_remove_diag', action='store_true')
    parser.add_argument('--directed_set_diag', action='store_true')
    args = parser.parse_args()

    if not args.directed and not args.undirected:
        raise ValueError('Please specify whether you want to use undirected or directed operators (or both).')
    
    # pre-processing ######################################################

    #dataset = PygNodePropPredDataset('ogbn-papers100M')
    dataset = PygNodePropPredDataset('ogbn-arxiv')
    split_idx = dataset.get_idx_split()
    data = dataset[0]

    x = data.x.numpy()
    N = data.num_nodes

    train_idx, valid_idx, test_idx = split_idx['train'], split_idx['valid'], split_idx['test']
    all_idx = torch.cat([train_idx, valid_idx, test_idx])
    mapped_train_idx = torch.arange(len(train_idx))
    mapped_valid_idx = torch.arange(len(train_idx), len(train_idx) + len(valid_idx))
    mapped_test_idx = torch.arange(len(train_idx) + len(valid_idx), len(train_idx) + len(valid_idx) + len(test_idx))

    op_dict = {}
    op_dict['label'] = data.y.data[all_idx].to(torch.long)
    op_dict['split_idx'] = {'train': mapped_train_idx, 'valid': mapped_valid_idx, 'test': mapped_test_idx}

    op_dict['op_embedding'] = []
    op_dict['op_embedding'].append(torch.from_numpy(x[all_idx]).to(torch.float))

    print('Start processing')

    alpha = 0.2
    eps = 1e-4
    topk = 100000
    ppr_normalization = 'row'
    
    if args.undirected:  # preprocess undirected operators
        
        print('Preparing undirected operators...')
        # subsample operator
        print('Subsampling (dropping {} %)'.format(100 * args.undirected_dropedge_rate))
        edge_index, _ = dropout_adj(data.edge_index, p=args.undirected_dropedge_rate, num_nodes=data.num_nodes)
        
        # to undirected
        print('Making the graph undirected')
        edge_index = to_undirected(edge_index, data.num_nodes)
        row, col = edge_index
        
        # get adj
        print('Getting adj matrix')
        adj = get_adj(row, col, N, asymm_norm=args.undirected_asymm_norm, set_diag=args.undirected_set_diag, remove_diag=args.undirected_remove_diag)
        
        idx = np.arange(adj.shape[0])
        ppr = topk_ppr_matrix(adj, alpha, eps, idx, topk, ppr_normalization)
        #ppr = ppr + 0.8*ppr.dot(ppr)

        '''for l in range(125):
            
            row_adj = adj[l].toarray()[0]
            row_ppr = ppr[l].toarray()[0]

            #a = len([1 for i in range(169343) if (row_adj[i]==0 and row_ppr[i]>0)])
            print(len([1 for i in range(169343) if (row_ppr[i]==0 and row_adj[i]>0)]))
            print(len([1 for i in range(169343) if (row_ppr[i]>0 and row_adj[i]==0)]))
            print(len([1 for i in range(169343) if (row_ppr[i]>0 and row_adj[i]>0)]))

            t = adj.multiply(ppr)
            row_t= t[l].toarray()[0]
            print(len([1 for i in range(169343) if row_t[i]>0]))
            import pdb; pdb.set_trace()

            if b>0:
                print(l, b)
                print([i for i in range(169343) if row_adj[i]>0])
                print([i for i in range(169343) if row_ppr[i]>0])

        import pdb; pdb.set_trace()'''


        '''np.random.seed(seed)
        rnd = np.random.permutation(n)

        train_idx = np.sort(rnd[:n_train])'''
        
        # preprocessing of features
        print('Diffusing node features')
        #ppr = ppr.multiply(adj)
        x = data.x.numpy()
        for _ in tqdm(range(args.undirected_num_propagations)):
            #x = adj @ x
            x = ppr @ x
            op_dict['op_embedding'].append(torch.from_numpy(x[all_idx]).to(torch.float))

        #import pdb; pdb.set_trace()
    
    if args.directed:  # preprocess directed operators
        
        print('Preparing directed operators...')
        # subsample operator
        print('Subsampling (dropping {} %)'.format(100 * args.directed_dropedge_rate))
        edge_index, _ = dropout_adj(data.edge_index, p=args.directed_dropedge_rate, num_nodes=data.num_nodes)
        row, col = edge_index
        
        # get adj
        print('Getting adj matrix')
        adj = get_adj(row, col, N, asymm_norm=args.directed_asymm_norm, set_diag=args.directed_set_diag, remove_diag=args.directed_remove_diag)

        idx = np.arange(adj.shape[0])
        ppr = topk_ppr_matrix(adj, alpha, eps, idx, topk, ppr_normalization)
        #ppr = ppr + 0.8*ppr.dot(ppr)
        
        
        # preprocessing of features
        print('Diffusing node features')
        #ppr = ppr.multiply(adj)
        x = data.x.numpy()
        for _ in tqdm(range(args.directed_num_propagations)):
            #x = adj @ x
            x = ppr @ x
            op_dict['op_embedding'].append(torch.from_numpy(x[all_idx]).to(torch.float))
            
        # get adj
        print('Getting transpose adj matrix')
        adj = get_adj(col, row, N, asymm_norm=args.directed_asymm_norm, set_diag=args.directed_set_diag, remove_diag=args.directed_remove_diag)
        
        idx = np.arange(adj.shape[0])
        ppr = topk_ppr_matrix(adj, alpha, eps, idx, topk, ppr_normalization)
        #ppr = ppr + 0.8*ppr.dot(ppr)

        # preprocessing of features
        print('Diffusing node features')
        #ppr = ppr.multiply(adj)
        x = data.x.numpy()
        for _ in tqdm(range(args.directed_num_propagations)):
            #x = adj @ x
            x = ppr @ x
            op_dict['op_embedding'].append(torch.from_numpy(x[all_idx]).to(torch.float))

    torch.save(op_dict, '{}.pt'.format(args.file_name))


main()
