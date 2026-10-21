"""Narrow UI action interface; tabs do not hold references to sibling widgets."""


class UiContext:
    def __init__(self, services):
        self.services = services
        self._actions = {}

    def bind(self, **actions):
        self._actions.update(actions)

    def __getattr__(self, name):
        if name in self._actions:
            return self._actions[name]
        raise AttributeError(name)
