import random
import networkx as nx
import numpy as np
import pandas as pd
import rdkit
from rdkit import Chem
from rdkit.Chem import rdMolDescriptors as rdm
from rdkit.Chem import AllChem, Draw, rdDepictor, rdchem
from rdkit.Chem.SaltRemover import SaltRemover
from rdkit.Chem.Draw import IPythonConsole, rdMolDraw2D
from rdkit.Chem.Draw.MolDrawing import MolDrawing, DrawingOptions #Only needed if modifying defaults
from IPython.display import SVG
remover = SaltRemover()
rdDepictor.SetPreferCoordGen(True)

from collections import defaultdict
from sklearn.metrics.pairwise import cosine_similarity

class mychem():
    #
    def extendedSMILES(mol, smi):
        atoms = list(set([x.GetSymbol() for x in mol.GetAtoms()]))
        atoms = atoms + [c.lower() for c in atoms if len(c) == 1 ]
        i, idx = 0, 0 # i: original smiles index, # idx: atom index
        dDict, outSmiles = {}, []
        while i < len(smi):
            c1, c2 = smi[i], smi[i:i+2]
            if (c1 in atoms) or (c2 in atoms):
                atom = rdchem.Mol.GetAtomWithIdx(mol, idx).GetSymbol()
                if atom==c2:
                    dDict[idx]=c2
                    outSmiles.append(c2)
                    idx += 1 
                    i += 2
                elif atom == c1.upper():
                    dDict[idx]=c1
                    outSmiles.append(c1)
                    idx += 1
                    i += 1
            else: 
                outSmiles.append(c1)
                i += 1
        return outSmiles
    # END of extendedSMILES

    def calc_atom_feature(self, atom): # 11 + 6 + 3 + 7 = 27
        # in periodic table order  # length: 10
        # H B C N O, F P S Cl Br, I other
        feature = [0] * 11
        Chiral = {"CHI_UNSPECIFIED":0,  "CHI_TETRAHEDRAL_CW":1, "CHI_TETRAHEDRAL_CCW":2, "CHI_OTHER":3}    # len=4
        Hybridization = {"UNSPECIFIED":0, "S":1, "SP":2, "SP2":3, "SP3":4, "SP3D":5, "SP3D2":6, "OTHER":7} # len=8
        if atom.GetSymbol() == 'H':   feature[0] = 1   #v
        #elif atom.GetSymbol() == 'B': feature[1] = 1 
        elif atom.GetSymbol() == 'C': feature[1] = 1   #v
        elif atom.GetSymbol() == 'N': feature[2] = 1   #v
        elif atom.GetSymbol() == 'O': feature[3] = 1   #v
        elif atom.GetSymbol() == 'F': feature[4] = 1
        elif atom.GetSymbol() == 'P': feature[5] = 1
        elif atom.GetSymbol() == 'S': feature[6] = 1
        elif atom.GetSymbol() == 'Cl': feature[7] = 1
        elif atom.GetSymbol() == 'Br': feature[8] = 1
        elif atom.GetSymbol() == 'I': feature[9] = 1
        else: feature[-1] = 1
        ### until here: len(feature) = 11
        feature.append(atom.GetTotalNumHs()/8)
        feature.append(atom.GetTotalDegree()/4)
        feature.append(atom.GetFormalCharge()/8)
        feature.append(atom.GetTotalValence()/8)
        feature.append(atom.IsInRing()*1)
        feature.append(atom.GetIsAromatic()*1)
        #feature.append(atom.GetNumRadicalElectrons())
        ### until here: len(feature) = 17
        # len= 4 - 1 = 3
        f =  [0]*(len(Chiral)-1)
        if Chiral.get(str(atom.GetChiralTag()), 0) != 0:
            f[Chiral.get(str(atom.GetChiralTag()), 0)] = 1
        feature.extend(f)
        # len= 8 - 1 = 7
        f =  [0]*(len(Hybridization)-1)
        if Hybridization.get(str(atom.GetHybridization()), 0) != 0:
            f[Hybridization.get(str(atom.GetHybridization()), 0)] = 1
        feature.extend(f)
        ### until here: len(feature) = 27
        return(feature)
    # END of calc_atom_feature

    def featurize_atoms(self, mol, smiles):
        molfeature=[]
        smiles	= self.extendedSMILES(mol, smiles)	
        atoms = [mol.GetAtomWithIdx(x).GetSymbol() for x in range(mol.GetNumAtoms())]
        for idx, c in enumerate(atoms):
            molfeature.append(self.calc_atom_feature(self, rdchem.Mol.GetAtomWithIdx(mol, idx)))
        molfeature = pd.DataFrame(np.transpose(np.array(molfeature)), columns = atoms)
        return molfeature
    # END of featurize_atoms

    def rw_getatombondlist(molobj, paths):
        # paths: a list of paths from rwr_summary output 'edge_data'
        atomlist, bondlist = [], []
        for path in paths:
            a,b = list(map(int, path.split('_') ))
            atomlist.extend([a,b])
            try:
                bondidx = molobj.GetBondBetweenAtoms(a,b).GetIdx()
                bondlist.append(bondidx)
            except: continue
        atomlist, bondlist = list(set(atomlist)), list(set(bondlist))
        return atomlist, bondlist  
    # END of rw_getatombondlist

    def cal_T(self, molobj, molgraph, smiles, chemistry="graph"):
        A = np.array(nx.adj_matrix(molgraph).todense(), dtype = np.float64)
        D = np.diag(np.sum(A, axis=0))
        T = np.dot(np.linalg.inv(D),A) 
        if chemistry != "graph":
            encoding = self.featurize_atoms(self, molobj, smiles)
            for n1, n2 in zip(*T.nonzero()):
                source = encoding.iloc[:,n1].to_numpy().reshape(1,-1)
                target = encoding.iloc[:,n2].to_numpy().reshape(1,-1) 
                cos = cosine_similarity(source, target)[0][0] 
                T[n1, n2] = cos
        for idx, edge in enumerate(list(molgraph.edges())):
            src_idx, tgt_idx = edge
            if molgraph[src_idx][tgt_idx]['order'] == 0:
                T[src_idx][tgt_idx] = 0
        for idx, row in enumerate(T):
            if row.sum() > 0:
                T[idx] = np.array([x/row.sum() for x in row]) # proportion
            else: continue
        T = T.transpose()
        return T
    # END of cal_T

    def cal_path_df(molgraph, mol_T, walkLength=10, n_walker=100, mode = "argmax", parallel=False):
        nNodes = molgraph.number_of_nodes()
        pdPath = pd.DataFrame(0, columns=list(range(nNodes)), index=range(n_walker))
        pdPath = pdPath.rename_axis(index='walker', columns="atomID")
        def run_record(walkers):
            visited = defaultdict(list)
            for ind in range(nNodes):
                z = np.zeros(nNodes)
                p = z
                p[ind] = 1
                p = p.reshape(-1,1)
                visited[ind] = [ind]
                for k in range(walkLength):
                    if k == 0:
                        neigh = [x for x in molgraph.neighbors(ind)]
                    else:
                        neigh = [x for x in molgraph.neighbors(visited[ind][-1])]
                    p = np.dot(mol_T, p)
                    if mode == 'argmax':
                        visit_ind = np.argmax( [p[i] for i in neigh] ) 
                    elif mode == 'random':
                        #visit_ind = random.choice( np.argwhere([p[i] for i in neigh] == np.amax([p[i] for i in neigh] )).item()  )
                        visit_ind = random.choices( population = np.arange(len(neigh)).reshape(-1,1), weights = [p[i] for i in neigh])[0][0]
                    visit = neigh[visit_ind]
                    visited[ind].append(visit)
            return pd.Series(visited)
        if parallel == True:
            pdPath = pdPath.parallel_apply(run_record, axis=1)
        else:
            pdPath = pdPath.apply(run_record, axis=1)
        return pdPath

    # random walk summary for each molecular graph
    def rwr_summary(mol_graph, rw_result_dict, n_walker=100):
        edges = mol_graph.edges()
        edge_cnt_res = {}
        # key: node; values: 1. fragment_smiles, 2. edge_list
        for node in rw_result_dict:
            edge_cnt_res[node] = defaultdict(list)
            for walker in range(n_walker):
                # for each start node, walking history is stored at rw_result_dict[walk]
                sources, targets = rw_result_dict[node][walker][:-1], rw_result_dict[node][walker][1:]
                # res: to retrieve edges that a walker walked through
                used = list(set(  ['_'.join(list(map(str,[source, target]))) for source, target in zip(sources, targets)]   ))
                edge_cnt_res[node]['node_list'].append( sorted(list(set(rw_result_dict[node][walker]))) )
                edge_cnt_res[node]['edge_list'].append( sorted(used) )
        graph_used_edges = list()
        for node in edge_cnt_res:
            graph_used_edges.extend(   [item for sublist in edge_cnt_res[node]['edge_list'] for item in sublist]  )
        graph_usage = {}
        for edge in graph_used_edges:
            try: graph_usage[edge] += 1
            except: graph_usage[edge] = 1
        return graph_usage, edge_cnt_res

    def rw_getSmilesPathDict(self, rdmol, rwr_summary_edge): # from 'edge_data' object
        # rwr_summary_edge: node: {edge_list: [edge_id, ..]} === {0: {'node_list':[[0,1,2,,..],...,], 'edge_list': [[0],,] }, ...}
        dFragCntDict = defaultdict(int)
        dNodeDict = defaultdict(list)
        for node in rwr_summary_edge:
            for idx, walker in enumerate(rwr_summary_edge[node]['edge_list']):
                edges = rwr_summary_edge[node]['edge_list'][idx]
                atoms, bonds = self.rw_getatombondlist(rdmol, edges) # dose not consider ionic bonds here
                try:    frag = Chem.MolFragmentToSmiles(rdmol, atomsToUse = atoms, bondsToUse = bonds)
                except: frag = Chem.MolFragmentToSmiles(rdmol, atomsToUse = [node])
                dFragCntDict[frag] += 1
                dNodeDict[node].append(frag)
        return dFragCntDict, dNodeDict
