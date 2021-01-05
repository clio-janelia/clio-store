from google.cloud import firestore
from pydantic.typing import List, Union

db = firestore.Client()

def get_collection(path: List[str]):
    """Return a firestore collection given a path of collection/document/collection...

    Args:
        path (List[str]): Path to collection holding the document that 
            must be odd length corresponding to collection/doc/collection...
    """
    if path is None:
        raise ValueError('path must exist')
    if len(path) % 2 == 0:
        raise ValueError('path must end in a collection')
    ref = db
    for index, name in enumerate(path):
        if index % 2 == 0:
            ref = ref.collection(name)
        else:
            ref = ref.document(name)
    return ref
