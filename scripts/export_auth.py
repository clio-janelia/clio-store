"""Export clio-store auth data from Firestore to JSON for DatasetGateway migration.

Usage:
    python scripts/export_auth.py [output_file]

Reads Firestore collections `clio_users` and `clio_datasets` and writes
a JSON file suitable for `python manage.py import_clio_auth`.

Requires GOOGLE_APPLICATION_CREDENTIALS or equivalent GCP auth.
"""

import json
import sys

from google.cloud import firestore


def export_auth(output_path: str = "exported_auth.json"):
    db = firestore.Client()

    # Export users
    users = {}
    for doc in db.collection("clio_users").stream():
        data = doc.to_dict()
        users[doc.id] = {
            "name": data.get("name", ""),
            "global_roles": list(data.get("global_roles", [])),
            "datasets": {
                ds: list(roles) for ds, roles in data.get("datasets", {}).items()
            },
            "groups": list(data.get("groups", [])),
            "disabled": data.get("disabled", False),
        }

    # Export datasets (just the public flag)
    datasets = {}
    for doc in db.collection("clio_datasets").stream():
        data = doc.to_dict()
        datasets[doc.id] = {
            "public": data.get("public", False),
        }

    output = {
        "users": users,
        "datasets": datasets,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Exported {len(users)} users and {len(datasets)} datasets to {output_path}")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "exported_auth.json"
    export_auth(path)
