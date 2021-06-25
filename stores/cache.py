import time

from typing import List
from stores.firestore import get_collection
from google.cloud import firestore

_caches = {}
_max_stale_time = 120.0 # seconds

class DocumentCache:
    """Caches a document and refreshes it from Firestore if stale time is exceeded."""

    def __init__(self, ref: firestore.DocumentReference, max_stale_time: float):
        """Set a document cache.

        Args:
            ref (firestore.DocumentReference): 
                Reference to document that is cached.
            max_stale_time (float):
                Maximum number of seconds that pass before document is reloaded.
        """
        self.ref = ref
        self.max_stale_time = max_stale_time
        self._value = {}
        self.updated = 0.0

        _caches[self.ref.path] = self

    @property
    def value(self) -> dict:
        cur_time = time.time()
        if cur_time - self.updated > self.max_stale_time:
            return self.refresh()
        return self._value

    def refresh(self) -> dict:
        """Reloads a document from Firestore and resets the staleness timer.
        
        Returns:
            The current value of the document.
        """
        snapshot = self.ref.get()
        if snapshot.exists:
            self._value = snapshot.to_dict()
        else:
            self._value = {}
        self.updated = time.time()
        return self._value
    
    def set(self, path: List[str], value: dict):
        """Set the value of this cache perhaps through the optional path."""
        obj = self._value
        for name in path:
            if not name in obj:
                obj[name] = {}
            obj = obj[name]
        obj = value
        self.updated = time.time()
        self.ref.set(self._value)

def _pathname(collection: List[str], document: str) -> str:
    path = collection.copy()
    path.append(document)
    return '/'.join(path)

def _get_cache(collection_path: List[str], document: str) -> DocumentCache:
    docpath = _pathname(collection_path, document)
    if docpath in _caches:
        doc_cache = _caches[docpath]
    else:
        collection = get_collection(collection_path)
        ref = collection.document(document)
        doc_cache = DocumentCache(ref, _max_stale_time)
        _caches[docpath] = doc_cache
    return doc_cache

def get_value(collection_path: List[str], document: str, path: List[str] = []):
    doc_cache = _get_cache(collection_path, document)
    obj = doc_cache.value
    for name in path:
        if name in obj:
            obj = obj[name]
        else:
            print(f"Field {name} not found in document cache {_pathname(collection_path, document)}")
            return None
    return obj

def set_value(collection_path: List[str], document: str, value: dict, path: List[str] = []):
    doc_cache = _get_cache(collection_path, document)
    doc_cache.set(path, value)

def refresh_all():
    for cache in _caches.values():
        cache.refresh()