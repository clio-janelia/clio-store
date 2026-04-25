import os
import sys

# Allowed origins for CORS handling.
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")

# Prefix to add before the actual API endpoints
URL_PREFIX = os.environ.get("URL_PREFIX", "")

# Email that automatically receives global "admin" privileges.
OWNER = os.environ.get("OWNER", None)

# DatasetGateway base URL — required. clio-store delegates all auth/authz here.
DSG_URL = os.environ.get("DSG_URL", None)
if not DSG_URL:
    sys.exit("error: DSG_URL must be set (run `pixi run setup` to configure)")

# TODO -- should really be in adapters to store

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

# firestore site reports
CLIO_SITE_REPORTS = "clio_site_reports"

# firestore subvolume edit collection
CLIO_SUBVOL = "clio_subvol"

# firestore volume proxy
CLIO_VOLUMES = "clio_volumes"
