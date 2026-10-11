"""An in-memory stand-in for google.cloud.storage, just wide enough for bundles.gcs and warehouse.gold_source: buckets of
blobs with generations, the real google.api_core exceptions, and a call log. Any read-before-write helper the code must not
use (get_bucket, reload, exists) raises, so a test proves the create-only writer never reads."""

from __future__ import annotations

from dataclasses import dataclass, field

from google.api_core import exceptions as api


@dataclass
class _Object:
    data: bytes
    generation: int


@dataclass
class FakeStorage:
    buckets: dict[str, dict[str, _Object]] = field(default_factory=dict)
    calls: list[tuple] = field(default_factory=list)
    forbidden: bool = False  # every call raises Forbidden
    auth_error: Exception | None = None  # every call raises this (e.g. a google.auth RefreshError)

    def put(self, bucket: str, name: str, data: bytes) -> None:
        objects = self.buckets.setdefault(bucket, {})
        previous = objects.get(name)
        objects[name] = _Object(data, (previous.generation + 1) if previous else 1)

    def bucket(self, name: str) -> FakeBucket:
        self.calls.append(("bucket", name))
        return FakeBucket(self, name)

    def get_bucket(self, name):  # pragma: no cover - must never be called
        raise AssertionError("get_bucket needs storage.buckets.get, which the job roles lack")

    def _check(self) -> None:
        if self.auth_error is not None:
            raise self.auth_error
        if self.forbidden:
            raise api.Forbidden("403 caller does not have storage.objects.get access")


@dataclass
class FakeBlob:
    store: FakeStorage
    bucket_name: str
    name: str
    size: int | None = None

    def download_as_bytes(self) -> bytes:
        self.store.calls.append(("download", self.bucket_name, self.name))
        self.store._check()
        try:
            return self.store.buckets[self.bucket_name][self.name].data
        except KeyError:
            raise api.NotFound(f"404 No such object: {self.bucket_name}/{self.name}") from None

    def download_to_filename(self, filename: str) -> None:
        with open(filename, "wb") as handle:
            handle.write(self.download_as_bytes())

    def upload_from_string(self, data: bytes, *, content_type=None, if_generation_match=None, retry="default") -> None:
        self.store.calls.append(
            ("upload", self.bucket_name, self.name, {"if_generation_match": if_generation_match, "retry": retry})
        )
        self.store._check()
        objects = self.store.buckets.setdefault(self.bucket_name, {})
        if if_generation_match == 0 and self.name in objects:
            raise api.PreconditionFailed(f"412 At least one of the pre-conditions you specified did not hold: {self.name}")
        self.store.put(self.bucket_name, self.name, bytes(data))

    def reload(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("reload needs storage.objects.get")

    def exists(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("exists needs storage.objects.get")


@dataclass
class FakeBucket:
    store: FakeStorage
    name: str

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.store, self.name, name)

    def list_blobs(self, prefix: str = "", max_results: int | None = None):
        self.store.calls.append(("list", self.name, prefix, max_results))
        self.store._check()
        if self.name not in self.store.buckets:
            raise api.NotFound(f"404 The specified bucket does not exist: {self.name}")
        objects = self.store.buckets[self.name]
        found = [
            FakeBlob(self.store, self.name, name, size=len(obj.data))
            for name, obj in sorted(objects.items())
            if name.startswith(prefix)
        ]
        return found if max_results is None else found[:max_results]
