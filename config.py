import os

# Allowed origins for CORS handling.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")

# Prefix to add before the actual API endpoints
URL_PREFIX = os.environ.get("URL_PREFIX", "")

# if OWNER email env var is set, the email automatically gets global "admin" privileges
OWNER = os.environ.get("OWNER", None)

# if TEST_USER env var is set, the user is set to this user email.
TEST_USER = os.environ.get("TEST_USER", None)

# TODO -- should really be in adapters to store

# firestore user collection name
CLIO_USERS = "clio_users"

# firestore dataset collection name
CLIO_DATASETS = "clio_datasets"

# firestore annotation collection name
CLIO_ANNOTATIONS = "clio_annotations"
CLIO_ANNOTATIONS_V2 = "clio_annotations_v2"
CLIO_ANNOTATIONS_GLOBAL = "clio_annotations_global"

# firestore saved searches collection name
CLIO_SAVEDSEARCHES = "clio_savedsearches"

# firestore keyvalue
CLIO_KEYVALUE = "clio_keyvalue"

# firestore pull requests
CLIO_PULL_REQUESTS = "clio_pull_requests"

# firestore subvolume edit collection
CLIO_SUBVOL = "clio_subvol"
