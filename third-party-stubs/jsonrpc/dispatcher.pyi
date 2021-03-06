# Stubs for jsonrpc.dispatcher (Python 3)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

import collections
from typing import Any, Optional

class Dispatcher(collections.MutableMapping):
    method_map: Any = ...
    def __init__(self, prototype: Optional[Any] = ...) -> None: ...
    def __getitem__(self, key: Any): ...
    def __setitem__(self, key: Any, value: Any) -> None: ...
    def __delitem__(self, key: Any) -> None: ...
    def __len__(self): ...
    def __iter__(self): ...
    def add_class(self, cls: Any) -> None: ...
    def add_object(self, obj: Any) -> None: ...
    def add_dict(self, dict: Any, prefix: str = ...) -> None: ...
    def add_method(self, f: Optional[Any] = ..., name: Optional[Any] = ...): ...
    def build_method_map(self, prototype: Any, prefix: str = ...) -> None: ...
