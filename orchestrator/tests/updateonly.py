class UpdateOnly:
    """A store that refuses `save`: the actions under test must go through `update`."""

    def __init__(self, store):
        self._store, self.updates = store, []

    def load(self, task_id):
        return self._store.load(task_id)

    def update(self, task_id, change):
        self.updates.append(task_id)
        return self._store.update(task_id, change)

    def save(self, task):
        raise AssertionError("save could overwrite a concurrent write: use update")
