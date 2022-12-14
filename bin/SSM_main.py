import time
import pandas as pd
from collections import defaultdict
from math import log
from copy import deepcopy
from collections import defaultdict
from sklearn.ensemble import RandomForestClassifier as RFC
from mychem import *

class DILInew:
    def __init__(self, chemistry = 'graph', n_rw=10, n_alpha = 0.5, iteration = 10, pruning=False, n_walker=100, rw_mode = "argmax"):
        self.chemistry, self.n_rw, self.n_alpha = chemistry, n_rw, n_alpha
        self.n_iteration, self.pruning, self.n_walkers  = iteration, pruning, n_walker
        self.n_train, self.n_valid = 0, 0
        self.rw_mode = rw_mode
        #
        self.molinfo_df = pd.DataFrame()
        self.train_molinfo_df = pd.DataFrame()
        #
        self.lfraglist, self.ledgelist = [], []
        self.dEdgeUsedCount  = defaultdict(dict) # number of visited edges by RW for each graph; key: ltkbid, value: {edge_1:n_1, edge_2:n_2, ...}
        self.dEdgelistUsage  = defaultdict(dict)
        self.dNodeFragCount  = defaultdict(dict) # fragment dictionary for each molecule; key: ltkbid, value: {frag_1:n_1, frag_2:n_2, ...}
        self.dNodeFragSmiles = defaultdict(dict)
        #
        self.dEdgeClassDict = {} # summary for class
        #
        self.dMolTransDict  = defaultdict(dict) # key: iteration, value: {ltkbid_0:T_0, ltkbid_1:T_1, ..., ltkbid_N:T_N} 
        self.dMolPreferDict = defaultdict(dict) # key: iteration, value: {ltkbid_0:F_0, ltkbid_1:F_1, ..., ltkbid_N:F_N} 
        self.dPreferDict = {} # key: iteration, value: Preference_iter
        #
        self.lexclusivefrags = defaultdict(list)
        self.lunionfrags     = defaultdict(list)
        self.dFragSearch     = {}
        self.prunehistory    = defaultdict(dict)

    def DoRandomWalk(self, n_iter, ltkbid, T): # run_rw starts here
        dpaths = mychem.cal_path_df(self.molinfo_df["molgraph"][ltkbid], T, walkLength = self.n_rw, n_walker = self.n_walkers, mode = self.rw_mode )
        # dpaths: walklist(= list of nodes that a walker has gone through)
        #         {node1: visited_nodes, node2: visited_nodes}
        self.dEdgeUsedCount[n_iter][ltkbid], self.dEdgelistUsage[n_iter][ltkbid]    = mychem.rwr_summary(self.molinfo_df["molgraph"][ltkbid], dpaths, n_walker = self.n_walkers)
        # dEdgeUsedCount: # of times edges used: {edge_1: 3, edge_2: 2, edge_3: 1, ...}
        # dEdgelistUsage: node: {node_list: [node_id, ..], edge_list: [edge_id, ..] }
        #                {0: {'node_list':[[0,1,2,,..], ..., []], 'edge_list': [['0_1', ...], ..., []]}
        self.dNodeFragCount[n_iter][ltkbid], self.dNodeFragSmiles[n_iter][ltkbid]   = mychem.rw_getSmilesPathDict(mychem, self.molinfo_df["molobj"][ltkbid], self.dEdgelistUsage[n_iter][ltkbid]) 
        # dNodeFragCount: {frag: 3, frag:1, ...}, dNodeFragSmiles = {node:frag, ... }
    # END of DoRandomWalk
    def cal_preference(self, n_it):
        for ltkbid in self.dEdgeUsedCount[n_it]:
            nClass = self.molinfo_df["class"][ltkbid]
            for edge in self.dEdgeUsedCount[n_it][ltkbid]:
                a,b = list(map(int, edge.split('_') ))
                frag = Chem.MolFragmentToSmiles(self.molinfo_df["molobj"][ltkbid], atomsToUse = [a, b])
                try: 
                    self.dEdgeClassDict[n_it][frag][nClass] += self.dEdgeUsedCount[n_it][ltkbid][edge]
                except: 
                    try: 
                        self.dEdgeClassDict[n_it][frag][nClass] = self.dEdgeUsedCount[n_it][ltkbid][edge]
                    except: 
                        self.dEdgeClassDict[n_it][frag] = {}
                        self.dEdgeClassDict[n_it][frag][nClass] = self.dEdgeUsedCount[n_it][ltkbid][edge]
    # END of cal_preference
    def get_individual_F(self, n_iter, n_iter_pref_df, ltkbid, mode=False):
        def get_likelihood(mySeries):
            val = (mySeries[1] + 1e-8) / (mySeries[0] + 1e-8)
            return val
        F = np.zeros( self.dMolTransDict[n_iter-1][ltkbid].shape )
        for edge in self.molinfo_df["molgraph"][ltkbid].edges():
            n1, n2 = edge
            if self.molinfo_df["molgraph"][ltkbid][n1][n2]['order'] != 0:
                bond = self.molinfo_df["molobj"][ltkbid].GetBondBetweenAtoms(n1, n2).GetIdx()
                frag_smi = Chem.MolFragmentToSmiles(self.molinfo_df["molobj"][ltkbid], atomsToUse = [n1, n2], bondsToUse = [bond])
                try: 
                    probSeries = n_iter_pref_df[frag_smi] / n_iter_pref_df.sum(axis=1) 
                    F[n1, n2] = get_likelihood( probSeries ) 
                    F[n2, n1] = F[n1, n2]
                except: F[n1, n2] = 0
        # normalize F
        for idx, row in enumerate(F):
            if row.sum() > 0:
                F[idx] = np.array([x/row.sum() for x in row]) # proportion
            else: continue
        F = F.transpose()
        return F
    # END of get_individual_F
    def rw_update_transitions(self, _T, _F, update_alpha):
        _T = _T * (1-update_alpha) + update_alpha * _F
        for idx, row in enumerate(_T):
            if row.sum() > 0:
                _T[idx] = np.array([x/row.sum() for x in row]) # proportion
        _T = _T.transpose()
        return _T
    # END of rw_update_transitions
    # START of get_fraglist
    def get_fraglist(self, n_iter):  # Get list of exclusive fragments
        #self.train_act_frag_df = pd.DataFrame(self.dNodeFragCount[n_iter], columns= self.dNodeFragCount[n_iter].keys())
        tempdf = pd.DataFrame(self.dNodeFragCount[n_iter], columns= self.dNodeFragCount[n_iter].keys()).fillna(0).T
        dfTempCnt = tempdf.merge(self.molinfo_df['class'], how='outer', left_index=True, right_on = self.molinfo_df.index)
        dfTempCnt.set_index(keys='key_0', inplace=True)
        nClassSpecificity  = dfTempCnt.groupby('class').any().astype(int).sum(axis=0)==1
        lExList = nClassSpecificity[nClassSpecificity].index.to_list()
        nClassSpecificity  = dfTempCnt.groupby('class').any().astype(int).sum(axis=0)>0
        lUnionList = nClassSpecificity[nClassSpecificity].index.to_list()
        return lExList, lUnionList
    # END of get_fraglist
    def search_fragments(self, sFrag):
        pdseries_search = pd.Series( 1e-10, index = self.train_molinfo_df['class'].unique(), name=sFrag)
        for ltkbid in self.train_molinfo_df["ID"]:
            if self.train_molinfo_df["molobj"][ltkbid].HasSubstructMatch(Chem.MolFromSmarts(sFrag), useChirality=True):
                pdseries_search[self.train_molinfo_df["class"][ltkbid]] += 1
        pdseries_search = pdseries_search.divide( self.train_molinfo_df.groupby("class")["ID"].count() + 1e-10 )
        return pdseries_search
    # END of search_fragments
    # START of DoPruning
    # MAIN HERE - argument: train_data
    def train(self, train_data): 
        self.molinfo_df = train_data
        self.train_molinfo_df = train_data
        self.n_train = train_data.shape[0]
        print(f'The Number of allowed walks: {self.n_rw}')
        for nI in range(self.n_iteration):  # iterate random walk process
            start = time.time()
            print(nI, 'loop starts', end="")
            for ltkbid in self.molinfo_df["ID"]: # iterate over molecules
                smiles = self.molinfo_df["smiles"][ltkbid]
                if nI == 0: # cal_T(molobj, molgraph, smiles, chemistry='graph')
                    T = mychem.cal_T(mychem, self.molinfo_df["molobj"][ltkbid], self.molinfo_df["molgraph"][ltkbid], smiles, chemistry = self.chemistry) 
                else:
                    pd_pref =  pd.DataFrame( self.dEdgeClassDict[nI-1], columns = self.dEdgeClassDict[nI-1].keys() ).fillna(0)
                    self.dMolPreferDict[nI-1][ltkbid] = self.get_individual_F(nI, pd_pref, ltkbid)
                    T = self.rw_update_transitions(self.dMolTransDict[nI-1][ltkbid], self.dMolPreferDict[nI-1][ltkbid], self.n_alpha) # T * (1-alpha) + F * alpha
                self.dMolTransDict[nI][ltkbid] = T
                self.DoRandomWalk(nI, ltkbid, T) # each molecule
            self.dEdgeClassDict[nI] = {}   
            self.cal_preference(nI) # Save Preference each iteration
            self.lexclusivefrags[nI], self.lunionfrags[nI] = self.get_fraglist(nI)
            fin = round( ( time.time() - start ) / 60 , 3 )
            print(f' for Random Walk {self.n_rw} completed in {fin} mins.')
        return self.dEdgeClassDict
    # END of train
    # MAIN HERE - argument: valid_data
    def valid(self, valid_data, train_df, train_edgeclassdict, train_dFragSearch): 
        self.molinfo_df = valid_data
        self.train_molinfo_df = train_df
        self.n_valid = valid_data.shape[0]
        print(f'The Number of allowed walks: {self.n_rw}')
        for nI in range(self.n_iteration):  # iterate random walk process
            start = time.time()
            print(nI, 'loop starts', end="")
            for ltkbid in self.molinfo_df.index: # iterate over molecules
                smiles = self.molinfo_df["smiles"][ltkbid]
                if nI == 0: # cal_T(molobj, molgraph, smiles, chemistry='graph')
                    T = mychem.cal_T(mychem, self.molinfo_df["molobj"][ltkbid], self.molinfo_df["molgraph"][ltkbid], smiles, chemistry = self.chemistry) 
                else:
                    pd_pref =  pd.DataFrame( train_edgeclassdict[nI-1], columns = train_edgeclassdict[nI-1].keys() ).fillna(0)
                    self.dMolPreferDict[nI-1][ltkbid] = self.get_individual_F(nI, pd_pref, ltkbid, mode='test')
                    T = self.rw_update_transitions(self.dMolTransDict[nI-1][ltkbid], self.dMolPreferDict[nI-1][ltkbid], self.n_alpha) # T * (1-alpha) + F * alpha
                self.dMolTransDict[nI][ltkbid] = T
                self.DoRandomWalk(nI, ltkbid, T) # each molecule
            fin = round( ( time.time() - start ) / 60 , 3 )
            print(f' for Random Walk {self.n_rw} completed in {fin} mins.')

class analyze_individual():
    def __init__(self):
        self.frag_df = defaultdict(pd.DataFrame)
        self.lfraglist = defaultdict(list)
        self.ledgelist = defaultdict(list)
    def get_frag_df(self, srw, iteration=0):
        self.frag_df[iteration] = pd.DataFrame(srw.dNodeFragCount[iteration], columns= srw.dNodeFragCount[iteration].keys())
        self.frag_df[iteration] = self.frag_df[iteration].fillna(0).T
        self.lfraglist[iteration] = list(set(self.frag_df[iteration].columns))
        print(f'The number of fragments for iteration {iteration}: {len(self.lfraglist[iteration])}')
    def get_edge_df(self, iteration=0):
        self.ledgelist = list(set(self.dEdgeClassDict[iteration].keys()))
        print(f'The number of edges for iteration {iteration}: {len(self.ledgelist[iteration])}')

def prepare_classification(df, molinfo):
    #df_binary = df > 0
    df_binary = df
    df_binary = df_binary.astype(int)
    df_new = df.merge(molinfo['class'], how='outer', left_index=True, right_on=molinfo["ID"])
    df_new = df_new.set_index("key_0", drop=True)
    df_new.index.name = None
    df_new = df_new.fillna(0)
    X = df_new.drop('class',axis=1)
    y = df_new['class']    
    return X, y
            
def prediction(train_obj, valid_obj, nIter, output_fname, train_molinfo_df, valid_molinfo_df, output_name, sOutDir, n_seed = 0): # train.pickle, test.pickle
	print("Iteration: ", nIter)
	pd_result =  pd.DataFrame( 0, index = range(nIter),  columns = ['n_union_subgraphs', 'n_train_subgraphs', 'n_valid_subgraphs', 'Accuracy', 'BAcc', 'Precision', 'Recall', 'F1_score', 'AUC', 'MCC'], dtype=np.float64)
	pd_confusion = pd.DataFrame(0, index = range(nIter), columns = ["tn", "fp", "fn", "tp"])
	analyze_train = analyze_individual()
	analyze_valid = analyze_individual()
	for nI in range(nIter):
		analyze_train.get_frag_df(train_obj, iteration=nI)
		analyze_valid.get_frag_df(valid_obj, iteration=nI)
		train_mat = analyze_train.frag_df[nI]
		valid_mat = analyze_valid.frag_df[nI]
		# features
		n_train = train_mat.shape[1]
		n_valid = valid_mat.shape[1]
		merged_mat = train_mat.append(valid_mat, sort=False).fillna(0)
		n_union = merged_mat.shape[1]
		train_mat = merged_mat.iloc[:train_mat.shape[0], :n_train]
		valid_mat = merged_mat.iloc[train_mat.shape[0]:, :n_train]
		train_X, train_y = prepare_classification(train_mat, train_molinfo_df)
		valid_X = valid_mat
	if (nI + 1) == nIter:
		print("Subgraph matrix generation finished. Prediction starts.")
		print(f'Training/validation data shape: {train_X.shape} / {valid_X.shape}')
		# performance
		smi_rf = RFC(random_state = n_seed) # seed number here
		smi_rf.fit(train_X, train_y)
		rf_preds  = smi_rf.predict(valid_X)
		rf_probs  = smi_rf.predict_proba(valid_X)[:,1]
		pd_output = valid_obj.molinfo_df.loc[:,['smiles']]
		pd_output['probability'] = rf_preds.tolist()
		pd_output['prediction'] = rf_probs.tolist()
		fname = sOutDir + '../results/' + str(output_name) + '_predictions.tsv'
		pd_output.to_csv(fname, sep="\t")
	# END of classification
