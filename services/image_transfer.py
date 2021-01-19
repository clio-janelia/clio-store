import json
import os
import random
import string
import threading
import traceback

from fastapi import APIRouter, Depends, HTTPException
import requests as requests2

from google.cloud import firestore
from google.cloud import storage

from config import *
from dependencies import get_user, User, CORSHandler

# transfer cloud run location and destination bucket
TRANSFER_FUNC = os.environ.get("TRANSFER_FUNC", None)
TRANSFER_DEST = os.environ.get("TRANSFER_DEST", None)

router = APIRouter(route_class=CORSHandler)

@router.post('/')
async def transfer(jsondata, current_user: User = Depends(get_user)):
    if TRANSFER_FUNC is None or TRANSFER_DEST is None:
        raise HTTPException(status_code=503, detail="transfer func/dest not set: /transfer not available")
    return transferData(current_user, jsondata)

def transferData(user, jsondata):
    """Transfer data for the given dataset, location, and model.

    JSON format

    {
        "center": [x,y,z] # center point x,y,z
        "dataset": "dataset name",
        "model_name": "model_name" # must be listed in the dataset info
    }

    """

    if not user.has_role("clio_general"):
        raise HTTPException(status_code=403, detail="user doesn't have authorization")

    """Json schema for cloud run request.

    {
        "location": "bucket and data location",
        "start": [x,y,z], # where to start reading -- should be multiple 64 from global offset
        "glbstart": [x,y,z], # for 0,0,0 offset
        "size": [x,y,z]. # multiple of 64
        "model_name": "model:version",
        "dest": "bucket and dest location for neuroglancer"
    }
    """

    try:
        # get dataset info and check model
        datasets_info = {}

        try:
            db = firestore.Client()
            datasets = db.collection(CLIO_DATASETS).get()
            for dataset in datasets:
                if dataset.id == jsondata["dataset"]:
                    datasets_info[dataset.id] = dataset.to_dict()
        except Exception:
            raise HTTPException(status_code=400, detail="unable to get datasets")

        # is model in the dataset meta
        if jsondata["dataset"] not in datasets_info:
            raise HTTPException(status_code=400, detail="dataset requested not available")
        dataset_info = datasets_info[jsondata["dataset"]]
        if "transfer" not in dataset_info:
            raise HTTPException(status_code=400, detail="transfer not in dataset info")
        if jsondata["model_name"] not in dataset_info["transfer"]:
            raise HTTPException(status_code=400, detail="model_name not in dataset transfer info")
        dataset_source = dataset_info["location"]

        # create random meta
        # write to google bucket
        storage_client = storage.Client()
        bucket = storage_client.bucket(TRANSFER_DEST)

        # create random name
        letters = string.ascii_lowercase
        random_dir = ''.join(random.choice(letters) for i in range(20))

        # write config
        tsize = [256,256,256]
        config = {
                        "@type" : "neuroglancer_multiscale_volume",
                        "data_type" : "uint8",
                        "num_channels" : 1,
                        "scales" : [
                            {
                                "chunk_sizes" : [
                                    [ 64, 64, 64 ]
                                    ],
                                "encoding" : "raw",
                                "key" : "8.0x8.0x8.0",
                                "resolution" : [ 8,8,8 ],
                                "size" : [ tsize[0], tsize[1], tsize[2] ],
                                "offset": [0, 0, 0]
                            }
                        ],
                        "type" : "image"
                    }
        blob = bucket.blob(random_dir + "/info")
        blob.upload_from_string(json.dumps(config))
        dest = TRANSFER_DEST + "/" + random_dir + "/8.0x8.0x8.0"

        # handle auth
        # Set up metadata server request
        # See https://cloud.google.com/compute/docs/instances/verifying-instance-identity#request_signature
        metadata_server_token_url = 'http://metadata/computeMetadata/v1/instance/service-accounts/default/identity?audience='

        token_request_url = metadata_server_token_url + TRANSFER_FUNC
        token_request_headers = {'Metadata-Flavor': 'Google'}

        # Fetch the token
        token_response = requests2.get(token_request_url, headers=token_request_headers)
        jwt = token_response.content.decode("utf-8")

        headers = {}
        headers["Content-type"] = "application/json"
        # Provide the token in the request to the receiving service
        headers["Authorization"] = f"Bearer {jwt}"

        # create request config template (start is custom for each job)
        tpsize = [128,128,128]
        config_cr = {
                "location": dataset_source,
                "glbstart": [jsondata["center"][0] - tsize[0]//2, jsondata["center"][1] - tsize[1]//2, jsondata["center"][2] - tsize[2]//2],
                "size": tpsize,
                "model_name": jsondata["model_name"],
                "dest": dest
        }

        # thread (up to 64) call to cloud run
        NUM_THREADS = 8
        def call_cr(thread_id):
            num = 0
            for ziter in range(0, tsize[2], 128):
                for yiter in range(0, tsize[1], 128):
                    for xiter in range(0, tsize[0], 128):
                        num += 1
                        if num % NUM_THREADS != thread_id:
                            continue
                        config_temp = config_cr.copy()
                        base = config_temp["glbstart"]
                        config_temp["start"] = [base[0]+xiter, base[1]+yiter, base[2]+ziter]
                        # occaaional errors are not critically important
                        retries = 10
                        while retries > 0:
                            resp = requests2.post(TRANSFER_FUNC, data=json.dumps(config_temp), headers=headers)
                            if resp.status_code != 200:
                                retries -= 1
                                time.sleep(5)
                            else:
                                break

        threads = [threading.Thread(target=call_cr, args=(thread_id,)) for thread_id in range(NUM_THREADS)]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # return address
        return {"addr": f"https://neuroglancer-demo.appspot.com/#!%7B%22layers%22%3A%5B%7B%22type%22%3A%22image%22%2C%22source%22%3A%7B%22url%22%3A%22precomputed%3A%2F%2Fgs%3A%2F%2F{TRANSFER_DEST}%2F{random_dir}%22%7D%2C%22tab%22%3A%22source%22%2C%22name%22%3A%22jpeg%22%7D%5D%2C%22selectedLayer%22%3A%7B%22layer%22%3A%22jpeg%22%2C%22visible%22%3Atrue%7D%7D"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=traceback.format_exc())

