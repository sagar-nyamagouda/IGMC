from __future__ import division
from __future__ import print_function

import numpy as np
import pandas as pd
import scipy.sparse as sp
import random
import pdb

# For automatic dataset downloading
from urllib.request import urlopen
from zipfile import ZipFile
import shutil
import os.path
from tqdm import tqdm

try:
    from BytesIO import BytesIO
except ImportError:
    from io import BytesIO


def data_iterator(data, batch_size):
    """
    A simple data iterator from https://indico.io/blog/tensorflow-data-inputs-part1-placeholders-protobufs-queues/
    :param data: list of numpy tensors that need to be randomly batched across their first dimension.
    :param batch_size: int, batch_size of data_iterator.
    Assumes same first dimension size of all numpy tensors.
    :return: iterator over batches of numpy tensors
    """
    # shuffle labels and features
    max_idx = len(data[0])
    idxs = np.arange(0, max_idx)
    np.random.shuffle(idxs)
    shuf_data = [dat[idxs] for dat in data]

    # Does not yield last remainder of size less than batch_size
    for i in range(max_idx // batch_size):
        data_batch = [dat[i * batch_size:(i + 1) * batch_size] for dat in shuf_data]
        yield data_batch


def map_data(data):
    """
    Map data to proper indices in case they are not in a continues [0, N) range

    Parameters
    ----------
    data : np.int32 arrays

    Returns
    -------
    mapped_data : np.int32 arrays
    n : length of mapped_data

    """
    uniq = list(set(data))

    id_dict = {old: new for new, old in enumerate(sorted(uniq))}
    data = np.array([id_dict[x] for x in data])
    n = len(uniq)

    return data, id_dict, n


def download_dataset(dataset, files, data_dir):
    """ Downloads dataset if files are not present. """

    if not np.all([os.path.isfile(data_dir + f) for f in files]):
        url = "http://files.grouplens.org/datasets/movielens/" + dataset.replace('_', '-') + '.zip'
        request = urlopen(url)

        print('Downloading %s dataset' % dataset)

        if dataset in ['ml_100k', 'ml_1m']:
            target_dir = 'raw_data/' + dataset.replace('_', '-')
        elif dataset == 'ml_10m':
            target_dir = 'raw_data/' + 'ml-10M100K'
        else:
            raise ValueError('Invalid dataset option %s' % dataset)

        with ZipFile(BytesIO(request.read())) as zip_ref:
            zip_ref.extractall('raw_data/')

        os.rename(target_dir, data_dir)
        # shutil.rmtree(target_dir)


def load_data(fname, seed=1234, verbose=True):
    """ Loads dataset and creates adjacency matrix
    and feature matrix

    Parameters
    ----------
    fname : str, dataset
    seed: int, dataset shuffling seed
    verbose: to print out statements or not

    Returns
    -------
    num_users : int
        Number of users and items respectively

    num_items : int

    u_nodes : np.int32 arrays
        User indices

    v_nodes : np.int32 array
        item (movie) indices

    ratings : np.float32 array
        User/item ratings s.t. ratings[k] is the rating given by user u_nodes[k] to
        item v_nodes[k]. Note that that the all pairs u_nodes[k]/v_nodes[k] are unique, but
        not necessarily all u_nodes[k] or all v_nodes[k] separately.

    u_features: np.float32 array, or None
        If present in dataset, contains the features of the users.

    v_features: np.float32 array, or None
        If present in dataset, contains the features of the users.

    seed: int,
        For datashuffling seed with pythons own random.shuffle, as in CF-NADE.

    """

    u_features = None
    v_features = None

    print('Loading dataset', fname)

    data_dir = 'raw_data/' + fname

    if fname == 'ml_100k':

        # Check if files exist and download otherwise
        files = ['/u.data', '/u.item', '/u.user']

        download_dataset(fname, files, data_dir)

        sep = '\t'
        filename = data_dir + files[0]

        dtypes = {
            'u_nodes': np.int32, 'v_nodes': np.int32,
            'ratings': np.float32, 'timestamp': np.float64}

        data = pd.read_csv(
            filename, sep=sep, header=None,
            names=['u_nodes', 'v_nodes', 'ratings', 'timestamp'], dtype=dtypes)

        # shuffle here like cf-nade paper with python's own random class
        # make sure to convert to list, otherwise random.shuffle acts weird on it without a warning
        data_array = data.values.tolist()
        random.seed(seed)
        random.shuffle(data_array)
        data_array = np.array(data_array)

        u_nodes_ratings = data_array[:, 0].astype(dtypes['u_nodes'])
        v_nodes_ratings = data_array[:, 1].astype(dtypes['v_nodes'])
        ratings = data_array[:, 2].astype(dtypes['ratings'])

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int32)
        ratings = ratings.astype(np.float64)

        # Movie features (genres)
        sep = r'|'
        movie_file = data_dir + files[1]
        movie_headers = ['movie id', 'movie title', 'release date', 'video release date',
                         'IMDb URL', 'unknown', 'Action', 'Adventure', 'Animation',
                         'Childrens', 'Comedy', 'Crime', 'Documentary', 'Drama', 'Fantasy',
                         'Film-Noir', 'Horror', 'Musical', 'Mystery', 'Romance', 'Sci-Fi',
                         'Thriller', 'War', 'Western']
        movie_df = pd.read_csv(movie_file, sep=sep, header=None,
                               names=movie_headers, engine='python')

        genre_headers = movie_df.columns.values[6:]
        num_genres = genre_headers.shape[0]

        v_features = np.zeros((num_items, num_genres), dtype=np.float32)
        for movie_id, g_vec in zip(movie_df['movie id'].values.tolist(), movie_df[genre_headers].values.tolist()):
            # Check if movie_id was listed in ratings file and therefore in mapping dictionary
            if movie_id in v_dict.keys():
                v_features[v_dict[movie_id], :] = g_vec

        # User features

        sep = r'|'
        users_file = data_dir + files[2]
        users_headers = ['user id', 'age', 'gender', 'occupation', 'zip code']
        users_df = pd.read_csv(users_file, sep=sep, header=None,
                               names=users_headers, engine='python')

        occupation = set(users_df['occupation'].values.tolist())

        gender_dict = {'M': 0., 'F': 1.}
        occupation_dict = {f: i for i, f in enumerate(occupation, start=2)}

        num_feats = 2 + len(occupation_dict)

        u_features = np.zeros((num_users, num_feats), dtype=np.float32)
        for _, row in users_df.iterrows():
            u_id = row['user id']
            if u_id in u_dict.keys():
                # age
                u_features[u_dict[u_id], 0] = row['age']
                # gender
                u_features[u_dict[u_id], 1] = gender_dict[row['gender']]
                # occupation
                u_features[u_dict[u_id], occupation_dict[row['occupation']]] = 1.

        u_features = sp.csr_matrix(u_features)
        v_features = sp.csr_matrix(v_features)

    elif fname == 'ml_1m':

        # Check if files exist and download otherwise
        files = ['/ratings.dat', '/movies.dat', '/users.dat']
        download_dataset(fname, files, data_dir)

        sep = r'\:\:'
        filename = data_dir + files[0]

        dtypes = {
            'u_nodes': np.int64, 'v_nodes': np.int64,
            'ratings': np.float32, 'timestamp': np.float64}

        # use engine='python' to ignore warning about switching to python backend when using regexp for sep
        data = pd.read_csv(filename, sep=sep, header=None,
                           names=['u_nodes', 'v_nodes', 'ratings', 'timestamp'], converters=dtypes, engine='python')

        # shuffle here like cf-nade paper with python's own random class
        # make sure to convert to list, otherwise random.shuffle acts weird on it without a warning
        data_array = data.values.tolist()
        random.seed(seed)
        random.shuffle(data_array)
        data_array = np.array(data_array)

        u_nodes_ratings = data_array[:, 0].astype(dtypes['u_nodes'])
        v_nodes_ratings = data_array[:, 1].astype(dtypes['v_nodes'])
        ratings = data_array[:, 2].astype(dtypes['ratings'])

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int64)
        ratings = ratings.astype(np.float32)

        # Load movie features
        movies_file = data_dir + files[1]

        movies_headers = ['movie_id', 'title', 'genre']
        movies_df = pd.read_csv(movies_file, sep=sep, header=None,
                                names=movies_headers, engine='python')

        # Extracting all genres
        genres = []
        for s in movies_df['genre'].values:
            genres.extend(s.split('|'))

        genres = list(set(genres))
        num_genres = len(genres)

        genres_dict = {g: idx for idx, g in enumerate(genres)}

        # Creating 0 or 1 valued features for all genres
        v_features = np.zeros((num_items, num_genres), dtype=np.float32)
        for movie_id, s in zip(movies_df['movie_id'].values.tolist(), movies_df['genre'].values.tolist()):
            # Check if movie_id was listed in ratings file and therefore in mapping dictionary
            if movie_id in v_dict.keys():
                gen = s.split('|')
                for g in gen:
                    v_features[v_dict[movie_id], genres_dict[g]] = 1.

        # Load user features
        users_file = data_dir + files[2]
        users_headers = ['user_id', 'gender', 'age', 'occupation', 'zip-code']
        users_df = pd.read_csv(users_file, sep=sep, header=None,
                               names=users_headers, engine='python')

        # Extracting all features
        cols = users_df.columns.values[1:]

        cntr = 0
        feat_dicts = []
        for header in cols:
            d = dict()
            feats = np.unique(users_df[header].values).tolist()
            d.update({f: i for i, f in enumerate(feats, start=cntr)})
            feat_dicts.append(d)
            cntr += len(d)

        num_feats = sum(len(d) for d in feat_dicts)

        u_features = np.zeros((num_users, num_feats), dtype=np.float32)
        for _, row in users_df.iterrows():
            u_id = row['user_id']
            if u_id in u_dict.keys():
                for k, header in enumerate(cols):
                    u_features[u_dict[u_id], feat_dicts[k][row[header]]] = 1.

        u_features = sp.csr_matrix(u_features)
        v_features = sp.csr_matrix(v_features)

    elif fname == 'ml_10m':

        # Check if files exist and download otherwise
        files = ['/ratings.dat']
        download_dataset(fname, files, data_dir)

        sep = r'\:\:'

        filename = data_dir + files[0]

        dtypes = {
            'u_nodes': np.int64, 'v_nodes': np.int64,
            'ratings': np.float32, 'timestamp': np.float64}

        # use engine='python' to ignore warning about switching to python backend when using regexp for sep
        data = pd.read_csv(filename, sep=sep, header=None,
                           names=['u_nodes', 'v_nodes', 'ratings', 'timestamp'], converters=dtypes, engine='python')

        # shuffle here like cf-nade paper with python's own random class
        # make sure to convert to list, otherwise random.shuffle acts weird on it without a warning
        data_array = data.values.tolist()
        random.seed(seed)
        random.shuffle(data_array)
        data_array = np.array(data_array)

        u_nodes_ratings = data_array[:, 0].astype(dtypes['u_nodes'])
        v_nodes_ratings = data_array[:, 1].astype(dtypes['v_nodes'])
        ratings = data_array[:, 2].astype(dtypes['ratings'])

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int64)
        ratings = ratings.astype(np.float32)

    elif fname == 'ml_25m':

        # Please download the processed movielens25M.csv to raw_data/ml_25m/
        # Each row is uid,iid,cid,time,rating, sorted by time
        files = ['/movielens25M.csv']
        row_count = 24999850

        filename = data_dir + files[0]

        chunksize = 10000
        data = pd.DataFrame()
        pbar = tqdm(pd.read_csv(filename, header=0, usecols=['uid', 'iid', 'rating'],
                                chunksize=chunksize), total=row_count // chunksize)
        for chunk in pbar:
            data = pd.concat([data, chunk], ignore_index=True)

        data_array = data.values

        u_nodes_ratings = data_array[:, 0]
        v_nodes_ratings = data_array[:, 1]
        ratings = data_array[:, 2]

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int64)
        ratings = ratings.astype(np.float32)

    elif fname == 'openml':
        files = ['/openml_matrix.csv']
        filename = data_dir + files[0]

        dtypes = {
            'task_id': np.int64, 'flow_id': np.int64,
            'associated': np.int64}

        # use engine='python' to ignore warning about switching to python backend when using regexp for sep
        data = pd.read_csv(filename, sep=',', header=None,
                           names=['task_id', 'flow_id', 'associated'], converters=dtypes, engine='python')

        # shuffle here like cf-nade paper with python's own random class
        # make sure to convert to list, otherwise random.shuffle acts weird on it without a warning
        data_array = data.values.tolist()
        random.seed(seed)
        random.shuffle(data_array)
        data_array = np.array(data_array)

        u_nodes_ratings = data_array[:, 0].astype(dtypes['task_id'])
        v_nodes_ratings = data_array[:, 1].astype(dtypes['flow_id'])
        ratings = data_array[:, 2].astype(dtypes['associated'])

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int64)
        ratings = ratings.astype(np.int64)

    elif fname == 'openmlV2':

        files = ['/original_matrixV3.csv']

        filename = data_dir + files[0]

        dtypes = {
            'task_id': np.int64, 'flow_id': np.int64,
            'predictive_accuracy': np.float64}

        # task_dtypes = {'task_id': np.int64, 'dataset_id': np.int64, 'task_type': np.}

        actual_data = pd.read_csv(filename)
        data = actual_data[["task_id", "flow_id", "predictive_accuracy"]]
        task_side_information = actual_data[["task_id", "task_type", "dataset_id"]]
        flow_side_information = actual_data[["flow_id", "flow_class"]]

        # data = pd.read_csv(filename, sep=',', header=None,
        #                    usecols=['task_id', 'flow_id', 'predictive_accuracy'], engine='python')

        data['predictive_accuracy'] = data['predictive_accuracy'].multiply(10).round()
        data = data.iloc[1:, :]
        print(f"The data after round off is {data}")

        # shuffle here like cf-nade paper with python's own random class
        # make sure to convert to list, otherwise random.shuffle acts weird on it without a warning
        data_array = data.values.tolist()

        random.seed(seed)
        random.shuffle(data_array)
        data_array = np.array(data_array)

        u_nodes_ratings = data_array[:, 0].astype(dtypes['task_id'])
        v_nodes_ratings = data_array[:, 1].astype(dtypes['flow_id'])
        ratings = data_array[:, 2].astype(dtypes['predictive_accuracy'])

        u_nodes_ratings, u_dict, num_users = map_data(u_nodes_ratings)
        v_nodes_ratings, v_dict, num_items = map_data(v_nodes_ratings)

        print(f"u_node_ratings values are {u_nodes_ratings} and u_dict is {u_dict}")
        print(f"v_node_ratings values are {v_nodes_ratings} and v_dict is {v_dict}")

        u_nodes_ratings, v_nodes_ratings = u_nodes_ratings.astype(np.int64), v_nodes_ratings.astype(np.int64)
        ratings = ratings.astype(np.int64)

        print(f"u_node_ratings values are {u_nodes_ratings}")
        print(f"ratings values are {ratings}")

        # Task Features

        # task_side_information = pd.read_csv(filename, header=None,
        #                                     names=['task_id', 'dataset_id', 'task_type'], engine='python')

        task_type = set(task_side_information['task_type'].values.tolist())

        task_type_dict = {f: i for i, f in enumerate(task_type, start=1)}

        num_feats = 1 + len(task_type_dict)

        u_features = np.zeros((num_users, num_feats), dtype=np.float32)
        for _, row in task_side_information.iterrows():
            u_id = row['task_id']
            if u_id in u_dict.keys():
                # dataset id
                u_features[u_dict[u_id], 0] = row['dataset_id']
                # task type
                u_features[u_dict[u_id], task_type_dict[row['task_type']]] = 1.

        print(f"task feature matrix is before csr matrix is {u_features}")
        u_features = sp.csr_matrix(u_features)
        print(f"task feature matrix is after csr matrix is {u_features}")

        # flow features
        flow_class = set(flow_side_information['flow_class'].values.tolist())
        flow_class_dict = {f: i for i, f in enumerate(flow_class)}
        flow_num_feats = len(flow_class_dict)

        v_features = np.zeros((num_items, flow_num_feats), dtype=np.float32)

        for _, row in flow_side_information.iterrows():
            v_id = row['flow_id']
            if v_id in v_dict.keys():
                # task class
                v_features[v_dict[v_id], flow_class_dict[row['flow_class']]] = 1.

        v_features = sp.csr_matrix(v_features)

    else:
        raise ValueError('Dataset name not recognized: ' + fname)

    if verbose:
        print('Number of users = %d' % num_users)
        print('Number of items = %d' % num_items)
        print('Number of links = %d' % ratings.shape[0])
        print('Fraction of positive links = %.4f' % (float(ratings.shape[0]) / (num_users * num_items),))

    return num_users, num_items, u_nodes_ratings, v_nodes_ratings, ratings, u_features, v_features, u_dict, v_dict
