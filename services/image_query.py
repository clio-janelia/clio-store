import json
import os

from fastapi import APIRouter, Depends, HTTPException
from dependencies import get_user
from dependencies import User

from google.cloud import storage
from google.cloud import bigquery

from config import *


# constants for signature search
SIG_BUCKET = os.environ.get("SIG_BUCKET", None)
SIG_CACHE = None # dataset to meta data cache for signature image search
SIG_DATASET_SUFFIX = "_imgsearch"
MAX_DISTANCE = 100 # 100 pixels (TODO: make dynamic)

router = APIRouter()

@router.get('/atlocation/{dataset}')
async def at_location(dataset: str, x: int, y: int, z: int, current_user: User = Depends(get_user)):
    if SIG_BUCKET is None:
        raise HTTPException(status_code=503, detail="signature bucket not set: /signatures not available")
    return get_signature(current_user, dataset, (x, y, z))

@router.get('/likelocation/{dataset}')
async def like_location(dataset: str, x: int, y: int, z: int, current_user: User = Depends(get_user)):
    if SIG_BUCKET is None:
        raise HTTPException(status_code=503, detail="signature bucket not set: /signatures not available")
    return get_matches(current_user, dataset, (x, y, z))

# Computational methods -- TODO move to domain module

def get_signature(user: User, dataset: str, point: tuple):
    if not user.has_role("clio_general", dataset):
        raise HTTPException(status_code=403, detail="user doesn't have authorization")

    try:
        pt, sig = fetch_signature(dataset, *point)
        res = {"point": pt, "signature": str(sig)}
    except Exception as e:
        res = {"messsage": str(e)}
    return res

def get_matches(user: User, dataset: str, point: tuple):
    if not user.has_role("clio_general", dataset):
        raise HTTPException(status_code=403, detail="user doesn't have authorization")

    try:
        data = find_similar_signatures(dataset, *point)
        res = {"matches": data}
        if len(data) == 0:
            res["message"] = "no matches"
    except Exception as e:
        res = {"messsage": str(e)}
    return res

# helper function for getting sig/xyz for x,y,z
def fetch_signature(dataset, x, y, z):
    global SIG_CACHE
    storage_client = storage.Client()
    bucket = storage_client.bucket(SIG_BUCKET)

    # fetch metaadata
    meta = None
    if SIG_CACHE is not None and dataset in SIG_CACHE:
        meta = SIG_CACHE[dataset]
    else:
        blob = bucket.blob(dataset + "/info.json")
        try:
            meta = json.loads(blob.download_as_string())
            if SIG_CACHE is None:
                SIG_CACHE = {}
            SIG_CACHE[dataset] = meta
        except Exception as e:
            print(e)
            raise Exception("dataset not found")

    block_size = meta["block_size"]

    # TODO: get stride information from info and predict perfect coordinate or design sampling
    # so block boundaries do not contain samples
    xb = x // block_size
    yb = y // block_size
    zb = z // block_size

    closest_dist = 999999999999
    closest_point = [0, 0, 0]
    closest_sig = 0

    def distance(pt):
        return (((x-pt[0])**2 + (y-pt[1])**2 + (z-pt[2])**2)**(0.5))

    # grab block and find closest match
    try:
        RECORD_SIZE = 20 # 20 bytes per x,y,z,signature
        blob = bucket.blob(dataset + f"/blocks/{xb}_{yb}_{zb}")
        blockbin = blob.download_as_string()
        records = len(blockbin) // RECORD_SIZE

        for record in range(records):
            start = record*RECORD_SIZE
            xt = int.from_bytes(blockbin[start:(start+4)], "little")
            start += 4
            yt = int.from_bytes(blockbin[start:(start+4)], "little")
            start += 4
            zt = int.from_bytes(blockbin[start:(start+4)], "little")
            start += 4
            dist = distance((xt,yt,zt))
            if dist <= MAX_DISTANCE and dist < closest_dist:
                closest_dist = dist
                closest_point = [xt,yt,zt]
                closest_sig = int.from_bytes(blockbin[start:(start+8)], "little", signed=True) # make signed int
        if closest_dist > MAX_DISTANCE:
            raise Exception("point not found")
    except Exception:
        raise Exception("point not found")

    return closest_point, closest_sig

def murmur64(h):
    h ^= h >> 33
    h *= 0xff51afd7ed558ccd
    h &= 0xFFFFFFFFFFFFFFFF
    h ^= h >> 33
    h *= 0xc4ceb9fe1a85ec53
    h &= 0xFFFFFFFFFFFFFFFF
    h ^= h >> 33
    return h

# find the closest signatures by hamming distance
def find_similar_signatures(dataset, x, y, z):
    # don't catch error if there is one
    point, signature = fetch_signature(dataset, x, y, z)
    meta = SIG_CACHE[dataset]
    PARTITIONS = 4000

    # find partitions for the signature
    part0 = murmur64(int(meta["ham_0"]) & signature) % PARTITIONS
    part1 = murmur64(int(meta["ham_1"]) & signature) % PARTITIONS
    part2 = murmur64(int(meta["ham_2"]) & signature) % PARTITIONS
    part3 = murmur64(int(meta["ham_3"]) & signature) % PARTITIONS

    """
    part0 = murmur64(signature) % PARTITIONS
    part1 = murmur64(signature) % PARTITIONS
    part2 = murmur64(signature) % PARTITIONS
    part3 = murmur64(signature) % PARTITIONS
    """

    max_ham = 8

    SQL = f"SELECT signature, BIT_COUNT(signature^{signature}) AS hamming, x, y, z FROM `{dataset}{SIG_DATASET_SUFFIX}.hamming0`\nWHERE part={part0} AND BIT_COUNT(signature^{signature}) < {max_ham}\nUNION DISTINCT\n"
    SQL += f"SELECT signature, BIT_COUNT(signature^{signature}) AS hamming, x, y, z FROM `{dataset}{SIG_DATASET_SUFFIX}.hamming1`\nWHERE part={part1} AND BIT_COUNT(signature^{signature}) < {max_ham}\nUNION DISTINCT\n"
    SQL += f"SELECT signature, BIT_COUNT(signature^{signature}) AS hamming, x, y, z FROM `{dataset}{SIG_DATASET_SUFFIX}.hamming2`\nWHERE part={part2} AND BIT_COUNT(signature^{signature}) < {max_ham}\nUNION DISTINCT\n"
    SQL += f"SELECT signature, BIT_COUNT(signature^{signature}) AS hamming, x, y, z FROM `{dataset}{SIG_DATASET_SUFFIX}.hamming3`\nWHERE part={part3} AND BIT_COUNT(signature^{signature}) < {max_ham}\n"
    SQL += f"ORDER BY BIT_COUNT(signature^{signature}), rand()\nLIMIT 200"

    client = bigquery.Client()

    query_job = client.query(SQL)
    results = query_job.result()

    all_points = [[x,y,z]]
    def distance(pt):
        best = 999999999999
        for c in all_points:
            temp = (((c[0]-pt[0])**2 + (c[1]-pt[1])**2 + (c[2]-pt[2])**2)**(0.5))
            if temp < best:
                best = temp
        return best

    pruned_results = []
    for row in results:
        # load results
        if distance((row.x, row.y, row.z)) > MAX_DISTANCE:
            pruned_results.append({"point": [row.x, row.y, row.z], "dist": row.hamming, "score": (1.0-row.hamming/max_ham)})
            all_points.append([row.x, row.y, row.z])

    return pruned_results