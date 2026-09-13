"""
==========================
Author: Niels Justesen
Year: 2019
==========================
This module contains enumerations and tables for the rules.
"""
from typing import Callable, Dict, List

from botbowl.core.model import Agent

class BotRegistry:

    def __init__(self):
        self.bots: Dict[str, Callable[[str], Agent]] = {}

    def register(self, id: str, cls: Callable[[str], Agent]) -> None:
        if id.lower() in self.bots:
            raise Exception('Bot with ID {} already registered.'.format(id.lower()))
        self.bots[id.lower()] = cls

    def make(self, id: str) -> Agent:
        if id.lower() not in self.bots:
            raise Exception('Bot with ID {} not registered.'.format(id.lower()))
        return self.bots[id.lower()](id.lower())

    def list(self) -> List[str]:
        result = []
        for key in self.bots:
            result.append(key)
        return result


# Have a global registry
registry = BotRegistry()


def register_bot(id: str, cls: Callable[[str], Agent]) -> None:
    return registry.register(id, cls)


def make_bot(id: str) -> Agent:
    return registry.make(id)


def list_bots() -> List[str]:
    return registry.list()
