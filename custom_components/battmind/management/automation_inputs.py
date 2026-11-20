"""Which actions of the blueprint automation have steps: read from automations.yaml, never written.

BattMind can not see what an automation does, but an automation made from the blueprint has its inputs in the file the
automation editor writes. The entity of an automation does not help: for a blueprint automation its raw_config is the
substituted configuration, without use_blueprint and its inputs. Automations kept in other files (packages, YAML mode)
are not found, and the test then does not know: it never reads that as "no steps".
"""
from __future__ import annotations

import logging
import os

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import yaml as yaml_util

_LOGGER = logging.getLogger(__name__)

BLUEPRINT_PATH = "battmind/battery_control.yaml"
ACTION_INPUT = "battmind_action"


def automations_using_blueprint(path: str, action_sensor: str) -> list[dict]:
    """The inputs of the automations in the file at path that use the BattMind blueprint for action_sensor.

    Blocking file I/O. [] when the file is missing or unreadable or holds no such automation."""
    if not os.path.isfile(path):
        return []
    try:
        automations = yaml_util.load_yaml(path)
    except (HomeAssistantError, OSError) as err:
        _LOGGER.debug("Could not read %s to see which actions the automation has steps for: %s", path, err)
        return []
    found: list[dict] = []
    for automation in automations if isinstance(automations, list) else []:
        blueprint = automation.get("use_blueprint") if isinstance(automation, dict) else None
        if not isinstance(blueprint, dict) or blueprint.get("path") != BLUEPRINT_PATH or not isinstance(blueprint.get("input"), dict):
            continue
        if blueprint["input"].get(ACTION_INPUT) == action_sensor:
            found.append(blueprint["input"])
    return found


def actions_without_steps(inputs: list[dict], actions: tuple[str, ...]) -> set[str]:
    """The actions no automation of inputs has a step for (an empty action does nothing in the blueprint).

    With no automation found nothing is known, so nothing is returned."""
    if not inputs:
        return set()
    return {action for action in actions if not any(automation.get(action) for automation in inputs)}
