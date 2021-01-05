import os

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

# firestore saved searches collection name
CLIO_SAVEDSEARCHES = "clio_savedsearches"

# firestore keyvalue
CLIO_KEYVALUE = "clio_keyvalue"
